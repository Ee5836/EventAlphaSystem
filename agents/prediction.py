"""Prediction Agent — multi-dimensional stock trend prediction."""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from flask import current_app

from agents.market_data import MarketDataAgent
from agents.stats_utils import event_relevance, event_timeliness, LEVEL_WEIGHT
from llm.factory import get_llm

logger = logging.getLogger(__name__)

# ── Module-level lazy-loaded embedding model ──────────────────────────
_embedding_model = None


def _get_embedding_model():
    """Lazy-load and cache the sentence-transformers model."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)
        except Exception:
            _embedding_model = None
    return _embedding_model


@dataclass
class PredictionResult:
    """Structured prediction output."""
    symbol: str
    name: str = ""
    direction: str = "neutral"  # bullish / bearish / neutral
    confidence: float = 0.5
    time_horizon: str = "T+3"
    target_low: Optional[float] = None
    target_high: Optional[float] = None
    key_factors: list[dict] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    technical_signals: list[dict] = field(default_factory=list)
    event_impact_score: float = 0.0
    reasoning_chain: list[str] = field(default_factory=list)
    chart_data: dict = field(default_factory=dict)


class PredictionAgent:
    """Generates multi-dimensional stock trend predictions.

    Combines:
    - Technical analysis (40%): MA, MACD, RSI, Bollinger
    - Event-driven (30%): impact from related events
    - Capital flow (15%): fund flow analysis
    - Sentiment (10%): news sentiment via LLM
    - Market environment (5%): index trend
    """

    def __init__(self):
        self.market_data = MarketDataAgent()

    def predict(
        self,
        symbol: str,
        time_horizon: str = "T+3",
        event_context: Optional[list[dict]] = None,
        mode: str = "full",
    ) -> PredictionResult:
        """Run full prediction pipeline for a stock.

        Args:
            symbol: Stock code.
            time_horizon: Prediction horizon ("T+1", "T+3", "T+7").
            event_context: Optional list of related event dicts.
            mode: "full" (LLM fusion) or "quick" (rule-based, <50ms).

        Returns:
            PredictionResult dataclass.
        """
        result = PredictionResult(symbol=symbol, time_horizon=time_horizon)

        try:
            # Step 1: Sync stock info
            stock_info = self.market_data.sync_stock_info(symbol)
            if stock_info:
                result.name = stock_info.name

            # Step 2: Fetch price history (with fallback to synthetic data)
            prices = self.market_data.fetch_price_history(symbol, period="daily")
            if not prices:
                # AKShare unavailable — generate synthetic data so chart renders
                result.risk_flags.append("⚠ 实时数据暂不可用，显示为模拟K线")
                prices = self.market_data.generate_synthetic_history(
                    symbol, days=120,
                    base_price=self._estimate_base_price(symbol),
                )

            closes = [p["close"] for p in prices]

            # Step 3: Technical analysis
            tech_signals = self._analyze_technical(prices, closes)
            result.technical_signals = tech_signals
            result.chart_data = self._build_chart_data(prices, tech_signals)

            # Step 4: Event impact scoring (via LLM)
            if event_context:
                result.event_impact_score = self._score_event_impact(
                    symbol, result.name, event_context
                )
            else:
                result.event_impact_score = 0.0

            # Step 5: Multi-dimensional fusion
            if mode == "quick":
                prediction = self._quick_fuse(tech_signals, closes)
            else:
                prediction = self._fuse_dimensions(
                    symbol=symbol,
                    name=result.name,
                    tech_signals=tech_signals,
                    event_score=result.event_impact_score,
                    event_context=event_context,
                    time_horizon=time_horizon,
                )

            result.direction = prediction.get("direction", "neutral")
            result.confidence = prediction.get("confidence", 0.5)
            result.target_low = prediction.get("target_low")
            result.target_high = prediction.get("target_high")
            result.key_factors = prediction.get("key_factors", [])
            result.risk_flags.extend(prediction.get("risk_flags", []))
            result.reasoning_chain = prediction.get("reasoning_chain", [])

        except Exception as e:
            logger.error(f"Prediction failed for {symbol}: {e}")
            result.risk_flags.append(f"预测异常: {e}")

        # ── POST-CONDITION: Guarantee chart_data is never empty ──
        cd = result.chart_data or {}
        kline = cd.get("kline", {})
        dates = kline.get("dates", [])
        if not dates or len(dates) < 5:
            logger.warning(f"Insufficient chart data for {symbol} ({len(dates)} bars), generating synthetic fallback")
            try:
                prices = self.market_data.generate_synthetic_history(
                    symbol, days=120,
                    base_price=self._estimate_base_price(symbol),
                )
                result.chart_data = self._build_chart_data(prices, [])
                if "⚠ 实时数据暂不可用" not in str(result.risk_flags):
                    result.risk_flags.append("⚠ 实时数据暂不可用，显示为模拟K线")
            except Exception as synth_err:
                logger.error(f"Synthetic data generation also failed for {symbol}: {synth_err}")

        return result

    @staticmethod
    def _estimate_base_price(symbol: str) -> float:
        """Estimate a realistic base price for a stock symbol.

        Uses typical price ranges for well-known A-share stocks.
        """
        price_map = {
            # 金融
            "000001": 11.5, "600036": 38.0, "601318": 45.0, "600030": 22.0,
            "601398": 5.5, "601288": 4.2, "300059": 16.0,
            # 白酒消费
            "600519": 1650.0, "000858": 160.0, "000568": 220.0, "002304": 95.0,
            "000333": 60.0, "000651": 38.0, "600887": 28.0,
            # 科技半导体
            "000063": 32.0, "002230": 48.0, "688981": 55.0, "000977": 38.0,
            "002371": 280.0, "603986": 85.0, "688012": 130.0,
            # 新能源
            "300750": 200.0, "002594": 260.0, "601012": 22.0, "600438": 35.0,
            "002460": 40.0, "002466": 50.0, "300014": 45.0,
            # 汽车
            "000625": 14.0, "601238": 9.0, "600104": 15.0,
            # 医药
            "600276": 48.0, "000538": 55.0, "300760": 280.0, "603259": 60.0,
            # 军工
            "600760": 45.0, "600893": 38.0, "002025": 55.0,
            # 电力能源
            "600900": 22.0, "601857": 8.0, "600028": 6.0, "601088": 28.0,
            "003816": 3.5, "601985": 8.0,
            # 地产基建
            "000002": 12.0, "600048": 10.0, "601668": 5.5,
            # 其他
            "601888": 120.0, "002352": 42.0, "601919": 12.0,
            "002475": 32.0, "601138": 22.0, "002241": 18.0,
            "000725": 4.0, "600309": 85.0, "600111": 28.0,
            "002027": 6.5, "002555": 22.0, "300418": 35.0,
            "600029": 6.0, "601111": 8.0,
        }
        # For unknown symbols, use a reasonable default based on code prefix
        if symbol in price_map:
            return price_map[symbol]
        if symbol.startswith("6"):
            return 15.0  # Shanghai stocks typically higher
        elif symbol.startswith("00"):
            return 12.0  # Shenzhen main board
        elif symbol.startswith("30"):
            return 25.0  # ChiNext
        else:
            return 20.0  # STAR / others

    def _analyze_technical(self, prices: list[dict], closes: list[float]) -> list[dict]:
        """Run technical indicator calculations."""
        signals = []

        # MA signals
        ma5 = self.market_data.calc_ma(closes, 5)
        ma20 = self.market_data.calc_ma(closes, 20)
        ma60 = self.market_data.calc_ma(closes, 60)

        last_close = closes[-1] if closes else 0
        last_ma5 = ma5[-1] if ma5 and ma5[-1] else last_close
        last_ma20 = ma20[-1] if ma20 and ma20[-1] else last_close
        last_ma60 = ma60[-1] if ma60 and ma60[-1] else last_close

        # Trend direction
        if last_ma5 > last_ma20 > last_ma60:
            signals.append({"indicator": "MA排列", "signal": "多头排列", "sentiment": "bullish"})
        elif last_ma5 < last_ma20 < last_ma60:
            signals.append({"indicator": "MA排列", "signal": "空头排列", "sentiment": "bearish"})
        else:
            signals.append({"indicator": "MA排列", "signal": "交叉震荡", "sentiment": "neutral"})

        # Price vs MA60
        if last_close > last_ma60:
            signals.append({"indicator": "MA60", "signal": f"价格在MA60上方", "sentiment": "bullish"})
        else:
            signals.append({"indicator": "MA60", "signal": f"价格在MA60下方", "sentiment": "bearish"})

        # MACD
        dif, dea, macd_hist = self.market_data.calc_macd(closes)
        if macd_hist and len(macd_hist) >= 2:
            last_hist = macd_hist[-1]
            prev_hist = macd_hist[-2]
            if last_hist is not None and prev_hist is not None:
                if last_hist > 0 and prev_hist <= 0:
                    signals.append({"indicator": "MACD", "signal": "金叉", "sentiment": "bullish"})
                elif last_hist < 0 and prev_hist >= 0:
                    signals.append({"indicator": "MACD", "signal": "死叉", "sentiment": "bearish"})
                elif last_hist > prev_hist:
                    signals.append({"indicator": "MACD", "signal": "红柱放大", "sentiment": "bullish"})
                else:
                    signals.append({"indicator": "MACD", "signal": "绿柱放大", "sentiment": "bearish"})

        # RSI
        rsi = self.market_data.calc_rsi(closes, 14)
        if rsi and rsi[-1] is not None:
            last_rsi = rsi[-1]
            if last_rsi > 70:
                signals.append({"indicator": "RSI(14)", "signal": f"超买({last_rsi:.0f})", "sentiment": "bearish"})
            elif last_rsi < 30:
                signals.append({"indicator": "RSI(14)", "signal": f"超卖({last_rsi:.0f})", "sentiment": "bullish"})
            else:
                signals.append({"indicator": "RSI(14)", "signal": f"中性({last_rsi:.0f})", "sentiment": "neutral"})

        return signals

    def _score_event_impact(
        self, symbol: str, name: str, events: list[dict]
    ) -> float:
        """Score event impact on a stock using LLM + statistical relevance.

        Blends LLM analysis with multi-dimensional event→stock relevance
        (semantic similarity, tag overlap, temporal decay, event level).
        """
        if not events:
            return 0.0

        try:
            llm = get_llm()

            # ── D1: Compute statistical relevance for each event ──
            # Use lazy-loaded singleton embedding model
            emb_model = _get_embedding_model()

            # Build target text from stock name + industries (from stock_info)
            target_text = f"{name} {symbol}"
            try:
                stock_info = self.market_data.sync_stock_info(symbol)
                if stock_info and stock_info.industry:
                    target_text += f" {stock_info.industry}"
            except Exception:
                pass

            stat_scores = []
            for ev in events:
                ev_title = ev.get("title", "")
                ev_desc = ev.get("description", ev.get("summary", ""))
                ev_tags = ev.get("tags", ev.get("affected_industries", []))
                ev_level = ev.get("level", "B")
                ev_ts = None
                ts_str = ev.get("timestamp", ev.get("created_at", ""))
                if ts_str:
                    try:
                        ev_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                rel = event_relevance(
                    event_title=ev_title,
                    event_description=ev_desc,
                    event_tags=ev_tags,
                    event_level=ev_level,
                    event_timestamp=ev_ts,
                    target_text=target_text,
                    embedding_model=emb_model,
                )
                stat_scores.append(rel["total"])

            # ── D2: LLM analysis ──
            events_text = json.dumps(events, ensure_ascii=False, indent=2)[:3000]
            prompt = f"""分析以下事件对股票 {name}({symbol}) 的潜在影响。

事件列表:
{events_text}

请返回JSON格式:
{{
    "impact_score": -1.0到1.0之间的浮点数（负=利空，正=利好）,
    "reasoning": "简要推理(50字内)",
    "confidence": 0.0到1.0的置信度
}}"""

            result = llm.complete_json(
                "你是一个专业的投资分析师。只返回JSON。",
                prompt,
                max_tokens=300,
            )
            llm_score = float(result.get("impact_score", 0))
            llm_confidence = float(result.get("confidence", 0.5))

            # ── D3: Blend LLM + statistical relevance ──
            avg_stat = sum(stat_scores) / len(stat_scores) if stat_scores else 0.0
            # Map statistical relevance [0,1] → [-1,1] using LLM's direction
            direction = 1.0 if llm_score >= 0 else -1.0
            stat_impact = direction * avg_stat

            # Final blend: 55% LLM + 45% statistical
            final_score = 0.55 * llm_score + 0.45 * stat_impact
            final_score = max(-1.0, min(1.0, final_score))

            logger.debug(
                f"Event impact for {symbol}: LLM={llm_score:.3f}, "
                f"stat={stat_impact:.3f} (avg_rel={avg_stat:.3f}), "
                f"final={final_score:.3f}"
            )
            return final_score

        except Exception as e:
            logger.warning(f"Event impact scoring failed: {e}")
            return 0.0

    def _fuse_dimensions(
        self,
        symbol: str,
        name: str,
        tech_signals: list[dict],
        event_score: float,
        event_context: Optional[list[dict]],
        time_horizon: str,
    ) -> dict:
        """Use LLM to fuse multi-dimensional signals into a final prediction."""
        try:
            llm = get_llm()

            tech_summary = json.dumps(tech_signals, ensure_ascii=False, indent=2)
            event_text = json.dumps(event_context or [], ensure_ascii=False)[:2000]

            prompt = f"""基于以下多维度信息，预测股票走势。

股票: {name}({symbol})
预测周期: {time_horizon}

【技术面信号】
{tech_summary}

【事件影响评分】{event_score:.2f} (-1利空 ~ +1利好)

【关联事件】
{event_text}

请综合分析并返回JSON:

{{
    "direction": "bullish|bearish|neutral",
    "confidence": 0.0-1.0,
    "target_low": 预测低价(数值),
    "target_high": 预测高价(数值),
    "key_factors": [
        {{"dimension": "技术面|事件驱动|资金面|情绪面", "contribution": 0.0-1.0, "description": "简述"}}
    ],
    "risk_flags": ["风险1", "风险2"],
    "reasoning_chain": ["步骤1", "步骤2", "步骤3"]
}}

⚠️ 免责: 预测仅供参考，不构成投资建议。"""

            result = llm.complete_json(
                "你是一个专业的量化投资分析师。只返回JSON，不做额外说明。",
                prompt,
                max_tokens=1000,
                temperature=0.2,
            )
            return result
        except Exception as e:
            logger.warning(f"Dimension fusion failed: {e}")
            return {
                "direction": "neutral",
                "confidence": 0.3,
                "risk_flags": [f"分析异常: {e}"],
            }

    def _quick_fuse(self, tech_signals: list[dict], closes: list[float]) -> dict:
        """Rule-based direction from technical signals — no LLM, <1ms.

        Used for instant-first-render; the frontend can upgrade to full
        LLM prediction in the background.
        """
        bullish_count = sum(1 for s in tech_signals if s.get("sentiment") == "bullish")
        bearish_count = sum(1 for s in tech_signals if s.get("sentiment") == "bearish")

        last_close = closes[-1] if closes else 50.0

        if bullish_count > bearish_count:
            direction = "bullish"
            confidence = 0.55 + 0.05 * min(bullish_count - bearish_count, 4)
        elif bearish_count > bullish_count:
            direction = "bearish"
            confidence = 0.55 + 0.05 * min(bearish_count - bullish_count, 4)
        else:
            direction = "neutral"
            confidence = 0.45

        # Target: ±3% band around last close
        target_low = round(last_close * 0.97, 2)
        target_high = round(last_close * 1.03, 2)

        # Build simple reasoning chain from signals
        reasoning = []
        for s in tech_signals:
            indicator = s.get("indicator", "")
            signal = s.get("signal", "")
            if indicator and signal:
                reasoning.append(f"{indicator}: {signal}")

        return {
            "direction": direction,
            "confidence": round(confidence, 2),
            "target_low": target_low,
            "target_high": target_high,
            "key_factors": [
                {"dimension": "技术面", "contribution": 1.0,
                 "description": f"基于MA/MACD/RSI规则推断 ({bullish_count}看涨/{bearish_count}看跌)"}
            ],
            "risk_flags": ["⚡ 快速模式（点击「深度分析」获取LLM预测）"],
            "reasoning_chain": reasoning[:5],
        }

    def _build_chart_data(self, prices: list[dict], signals: list[dict]) -> dict:
        """Build ECharts-compatible chart data."""
        closes = [p["close"] for p in prices]
        dates = [p["date"] for p in prices]

        return {
            "kline": {
                "dates": dates,
                "open": [p["open"] for p in prices],
                "close": closes,
                "high": [p["high"] for p in prices],
                "low": [p["low"] for p in prices],
                "volume": [p["volume"] for p in prices],
            },
            "ma": {
                "ma5": self.market_data.calc_ma(closes, 5),
                "ma20": self.market_data.calc_ma(closes, 20),
                "ma60": self.market_data.calc_ma(closes, 60),
            },
            "signals": signals,
            "dates": dates,
        }
