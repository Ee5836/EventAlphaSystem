"""Research Assistant Agent — event-focused Q&A with DB lookup."""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from flask import current_app

from assistant.chat_manager import ChatManager
from assistant.reasoning import ReasoningChain
from assistant.tools.web_search import web_search
from assistant.tools.api_caller import api_call
from assistant.tools.web_crawler import crawl_url
from assistant.tools.action_handler import (
    ACTIONS, DESTRUCTIVE_ACTIONS, execute_action, get_available_actions_markdown,
)
from agents.stats_utils import (
    event_relevance,
    event_timeliness,
    LEVEL_WEIGHT,
    encode_nodes_batch,
)
from llm.factory import get_llm

logger = logging.getLogger(__name__)

RESEARCH_SYSTEM_PROMPT = """你是 BubbleEvent 投资事件研究助手，拥有对全局系统的深度分析能力。你基于系统中的事件数据、行情数据、时间线因果网络、每日简报、走势预测等全方位信息进行分析和回答。

核心能力：
1. **事件分析**: 检索系统内事件库，解释事件对市场/行业/个股的影响机制
2. **时间线分析**: 查询Bubble网络，分析事件关联、节点统计、因果链验证状态
3. **简报查询**: 查阅每日简报（执行摘要+市场快照+风险预警），支持触发重新生成
4. **走势解读**: 查询实时行情+历史K线，计算技术指标（均线/MACD/RSI），解读趋势
5. **系统状态**: 聚合查看系统运行全貌（事件数/时间线规模/活跃源/简报日期）
6. **通用知识**: 解释投资概念、行业逻辑、技术分析原理
7. **操作执行**: 可执行系统操作（触发管道采集新闻、管理信息源、生成简报、重建时间线等），操作结果会说明实际发生了什么

核心规则：
1. 优先使用系统提供的数据，严禁编造任何事件、日期、价格、统计数据
2. 引用系统数据时注明来源（事件标题、股票名称、简报日期等）
3. 如果系统中无直接相关数据，说明"当前系统中暂无直接相关记录"
4. 区分"数据事实"和"分析推测"，推测性内容必须标注

回答要求：
- 快速问题简洁回答（200字内），深度分析可按需扩展
- 先给结论再分析依据，结构化组织信息
- 趋势图解读使用通俗比喻，让非专业用户也能理解
- 统计数据使用数字呈现，对比分析有逻辑

禁止事项：
- 不要透露系统内部运作（工具调用、数据库查询等技术细节）
- 不要输出推理过程或工具使用步骤
- 不要使用"根据搜索结果""通过爬取"等暴露工具使用的词汇
- 不要给出精确买卖点或目标价建议

所有输出不构成投资建议，仅供研究参考。"""

CONTEXT_TEMPLATE = """以下是系统中的相关数据：

【相关事件】
{events_text}

【行情与趋势数据】
{market_text}

请基于以上数据回答用户问题。如果数据不足，可用你的知识简要补充并标注"通用知识参考"。"""


class ResearchAssistantAgent:
    """Event-focused research assistant with DB event lookup."""

    def __init__(self):
        self.chat_manager = ChatManager()
        self.llm = None
        self._pending_action: dict = None  # {name, params} for destructive action confirmation
        self._focus_industries: list[str] = []  # user's focus industries

    def _get_llm(self):
        if self.llm is None:
            self.llm = get_llm()
        return self.llm

    def _lookup_events(self, query: str, limit: int = 10) -> str:
        """Search local event database for relevant events.

        Uses multi-dimensional relevance ranking:
        - Semantic similarity (text2vec embedding cosine)
        - Tag overlap (keyword → tag matching)
        - Timeliness decay (fresher events rank higher)
        - Event level weighting (S > A > B)
        """
        try:
            from models.card import EventCard
            from sentence_transformers import SentenceTransformer

            cards = (
                EventCard.query
                .order_by(EventCard.created_at.desc())
                .limit(200)
                .all()
            )

            if not cards:
                return "（当前系统中暂无事件记录）"

            # ── Lazy-load embedding model ──
            try:
                emb_model = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)
            except Exception:
                emb_model = None

            # ── Score each card by multi-dimensional relevance ──
            scored = []
            for card in cards:
                rel = event_relevance(
                    event_title=card.title or "",
                    event_description=(card.summary or "")[:300],
                    event_tags=card.affected_industries or [],
                    event_level=card.level or "B",
                    event_timestamp=card.created_at,
                    target_text=query,
                    embedding_model=emb_model,
                )
                # Also add a keyword bonus for exact query term matches
                text_lower = ((card.title or '') + ' ' + (card.summary or '')).lower()
                kw_bonus = sum(1 for kw in query.lower().split() if kw in text_lower) * 0.05
                total = rel["total"] + kw_bonus

                # Boost for user's focus industries
                if self._focus_industries:
                    card_industries = card.affected_industries or []
                    for focus_ind in self._focus_industries:
                        if focus_ind in card_industries:
                            total += 0.15
                            break

                if total > 0.05:  # minimum relevance threshold
                    scored.append((total, rel, card))

            # Sort by relevance score descending
            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[:limit]

            if not top:
                return "（当前系统中暂无与查询直接相关的事件记录）"

            lines = []
            for i, (total, rel, card) in enumerate(top, 1):
                industries = ', '.join(card.affected_industries or []) or '未分类'
                time_str = card.created_at.strftime('%m-%d %H:%M') if card.created_at else '未知'
                lines.append(
                    f"{i}. [{card.level}级 | 相关度:{total:.2f}] {card.title}\n"
                    f"   摘要: {(card.summary or '无')[:150]}\n"
                    f"   影响行业: {industries}\n"
                    f"   可信度: {card.credibility_label or '未知'} · 时间: {time_str}"
                )
            return "\n\n".join(lines)

        except Exception as e:
            logger.error(f"Event lookup failed: {e}")
            return "（事件数据查询异常，请稍后重试）"

    def _lookup_market(self, query: str, events_data: str) -> str:
        """Search market data: stock info, snapshots, price history.

        Uses multiple matching strategies:
        1. DB StockInfo table (if populated)
        2. Industry→stock mapping (same source as prediction page)
        3. Direct stock name/symbol match from query
        """
        try:
            from models.market import StockInfo, PriceSnapshot, PriceHistory
            from app.utils.industry_stocks import INDUSTRY_STOCKS, get_stocks_for_industry, _ALIASES

            lines = []
            matched_symbols = []  # list of (symbol, name, industry)

            # ── Strategy 1: DB StockInfo ──
            db_stocks = StockInfo.query.limit(500).all()
            query_lower = query.lower()

            if db_stocks:
                for s in db_stocks:
                    if s.symbol in query or (s.name and s.name in query):
                        matched_symbols.append((s.symbol, s.name, s.industry or ''))
                    elif s.industry and s.industry in query:
                        matched_symbols.append((s.symbol, s.name, s.industry or ''))

            # ── Strategy 2a: Focus industries first ──
            if self._focus_industries and not matched_symbols:
                for focus_ind in self._focus_industries:
                    stocks = INDUSTRY_STOCKS.get(focus_ind, [])
                    for sym, name in stocks[:2]:
                        if (sym, name) not in [(m[0], m[1]) for m in matched_symbols]:
                            matched_symbols.append((sym, name, focus_ind))

            # ── Strategy 2b: Industry→Stock mapping (fallback) ──
            if not matched_symbols:
                # Extract possible industry/stock names from query
                for industry, stocks in INDUSTRY_STOCKS.items():
                    if industry in query or industry in events_data:
                        for sym, name in stocks[:3]:
                            if (sym, name) not in [(m[0], m[1]) for m in matched_symbols]:
                                matched_symbols.append((sym, name, industry))

            # ── Strategy 3: Direct name lookup in mapping ──
            if not matched_symbols:
                for industry, stocks in INDUSTRY_STOCKS.items():
                    for sym, name in stocks:
                        if name in query:
                            matched_symbols.append((sym, name, industry))

            # ── Strategy 4: Event industry match ──
            if not matched_symbols and events_data:
                # Parse industries from event data text
                for industry, stocks in INDUSTRY_STOCKS.items():
                    if industry in events_data:
                        for sym, name in stocks[:2]:
                            if (sym, name) not in [(m[0], m[1]) for m in matched_symbols]:
                                matched_symbols.append((sym, name, industry))
                # Also try aliases
                for alias, canonical in _ALIASES.items():
                    if alias in events_data or alias in query:
                        stocks = INDUSTRY_STOCKS.get(canonical, [])
                        for sym, name in stocks[:2]:
                            if (sym, name) not in [(m[0], m[1]) for m in matched_symbols]:
                                matched_symbols.append((sym, name, canonical))

            matched_symbols = matched_symbols[:5]

            # ── Get snapshots from DB ──
            if matched_symbols:
                symbols = [m[0] for m in matched_symbols]
                snaps = PriceSnapshot.query.filter(PriceSnapshot.symbol.in_(symbols)).all()
                snapshots = {p.symbol: p for p in snaps}

                if snapshots:
                    lines.append("【实时行情】")
                    for sym, name, industry in matched_symbols:
                        snap = snapshots.get(sym)
                        if snap and snap.latest_price:
                            change_str = f"{snap.change_pct:+.2f}%" if snap.change_pct else "--"
                            lines.append(
                                f"{sym} {name} | "
                                f"最新价: {snap.latest_price:.2f} | "
                                f"涨跌幅: {change_str} | "
                                f"行业: {industry or '未知'}"
                            )
                    lines.append("")
                else:
                    # No snapshots yet — tell AI to suggest viewing on prediction page
                    lines.append("【匹配股票】（行情数据暂未加载，建议用户在走势预测页面查看图表）")
                    for sym, name, industry in matched_symbols:
                        lines.append(f"{sym} {name} | 行业: {industry or '未知'} | 提示: 可在走势预测页面输入 {sym} 查看完整K线图")
                    lines.append("")

            # ── Get price history for first matched stock ──
            if matched_symbols:
                sym, name, industry = matched_symbols[0]
                hist = (
                    PriceHistory.query
                    .filter_by(symbol=sym, period='daily')
                    .order_by(PriceHistory.date.desc())
                    .limit(20)
                    .all()
                )
                if hist:
                    closes = [h.close for h in hist if h.close]
                    if closes:
                        ma5 = sum(closes[:5]) / min(5, len(closes[:5])) if len(closes) >= 5 else None
                        ma10 = sum(closes[:10]) / min(10, len(closes[:10])) if len(closes) >= 10 else None
                        latest = closes[0]
                        high_20 = max(closes[:20])
                        low_20 = min(closes[:20])

                        lines.append(f"【{name}({sym}) 近期走势摘要】")
                        lines.append(f"最新收盘: {latest:.2f}")
                        if ma5: lines.append(f"5日均线: {ma5:.2f} {'(↑ 站上均线，短期偏强)' if latest > ma5 else '(↓ 跌破均线，短期偏弱)'}")
                        if ma10: lines.append(f"10日均线: {ma10:.2f}")
                        lines.append(f"20日最高: {high_20:.2f} / 最低: {low_20:.2f}")
                        lines.append(f"20日振幅: {((high_20 - low_20) / low_20 * 100):.1f}%")
                        lines.append(f"（提示：均线=过去N天收盘价平均值，价格在均线之上通常短期强势。20日振幅越大说明波动越大。）")
                        lines.append("")

            if not matched_symbols:
                return "（当前系统中暂无与查询相关的股票或行情数据。建议用户在走势预测页面搜索股票代码查看K线图。）"

            if not lines:
                return "（已匹配到相关股票，但行情数据尚未加载。建议在走势预测页面查看。）"

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Market lookup failed: {e}")
            return "（行情数据查询异常，请稍后重试）"

    def _lookup_timeline_stats(self, query: str = "") -> str:
        """Query timeline statistics: node counts by type/status, edge counts by verification."""
        try:
            from models.timeline import TimelineNode, CausalEdge
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)

            # Node counts by type
            nodes = TimelineNode.query.all()
            type_counts = {}
            status_counts = {}
            level_counts = {}
            total_nodes = len(nodes)
            for n in nodes:
                t = n.node_type or "unknown"
                type_counts[t] = type_counts.get(t, 0) + 1
                s = n.status or "unknown"
                status_counts[s] = status_counts.get(s, 0) + 1
                # Count by event level from metadata
                meta = n.metadata_json or {}
                level = meta.get("level", "")
                if level:
                    level_counts[level] = level_counts.get(level, 0) + 1
                # Also count unexpired
                if n.expires_at and n.expires_at < now:
                    level_counts["_expired"] = level_counts.get("_expired", 0) + 1

            # Edge counts by verification status
            edges = CausalEdge.query.all()
            total_edges = len(edges)
            verified_count = sum(1 for e in edges if e.verified is True)
            inferred_count = sum(1 for e in edges if e.verified is None)
            refuted_count = sum(1 for e in edges if e.verified is False)

            # Build readable summary
            type_labels = {
                "root_event": "根事件", "derived_event": "衍生事件",
                "prediction": "预测", "market_reaction": "市场反应", "verification": "验证"
            }
            status_labels = {"confirmed": "已确认", "predicted": "预测中", "pending": "待验证", "refuted": "已证伪"}

            lines = [f"【时间线统计】"]
            lines.append(f"总节点: {total_nodes} | 总因果边: {total_edges}")
            lines.append(f"边状态: 已验证 {verified_count} · 推断 {inferred_count} · 证伪 {refuted_count}")

            type_parts = []
            for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
                label = type_labels.get(t, t)
                type_parts.append(f"{label} {c}")
            lines.append(f"节点类型: {', '.join(type_parts)}")

            if level_counts:
                level_parts = []
                for lv in ["S", "A", "B", "C"]:
                    if lv in level_counts:
                        level_parts.append(f"{lv}级 {level_counts[lv]}")
                if level_parts:
                    lines.append(f"按事件等级: {', '.join(level_parts)}")
                if level_counts.get("_expired", 0) > 0:
                    lines.append(f"已过期节点: {level_counts['_expired']}")

            status_parts = []
            for s, c in sorted(status_counts.items(), key=lambda x: -x[1]):
                label = status_labels.get(s, s)
                status_parts.append(f"{label} {c}")
            lines.append(f"节点状态: {', '.join(status_parts)}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Timeline stats lookup failed: {e}")
            return "（时间线统计数据查询异常）"

    def _lookup_briefing(self, date_str: str = None, regenerate: bool = False) -> str:
        """Query daily briefing or trigger regeneration."""
        try:
            from models.briefing import DailyBriefing
            from datetime import date

            if regenerate:
                return "（简报重新生成请求已提交，请在简报页面查看最新结果。或使用 POST /api/v1/briefing/generate 接口触发。）"

            target_date = None
            if date_str:
                try:
                    target_date = date.fromisoformat(date_str)
                except ValueError:
                    pass

            briefing = None
            if target_date:
                briefing = DailyBriefing.query.filter_by(date=target_date).first()
            else:
                briefing = DailyBriefing.query.order_by(DailyBriefing.date.desc()).first()

            if not briefing:
                return f"（{'指定日期' if date_str else '当前'}暂无简报数据。可触发管道或手动生成简报。）"

            lines = [f"【每日简报 — {briefing.date.isoformat()}】"]

            if briefing.executive_summary:
                lines.append(f"📌 执行摘要: {briefing.executive_summary[:300]}")

            if briefing.key_numbers_json:
                kn = briefing.key_numbers_json
                lines.append(f"📊 关键数字: 事件 {kn.get('total_events', '?')} (S级 {kn.get('s_level', '?')}, A级 {kn.get('a_level', '?')}) | 活跃源 {kn.get('active_sources', '?')}")

            if briefing.top_events_json:
                events = briefing.top_events_json
                if isinstance(events, list) and len(events) > 0:
                    lines.append(f"🔥 今日要闻 ({len(events)}条):")
                    for i, ev in enumerate(events[:5]):
                        if isinstance(ev, dict):
                            lines.append(f"  {i+1}. [{ev.get('level', '?')}级] {ev.get('title', '')}")

            if briefing.risk_alert_json:
                alerts = briefing.risk_alert_json
                if isinstance(alerts, list) and len(alerts) > 0:
                    lines.append(f"⚠️ 风险预警 ({len(alerts)}条):")
                    for alert in alerts[:3]:
                        if isinstance(alert, dict):
                            lines.append(f"  • {alert.get('title', alert.get('description', str(alert)))[:120]}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Briefing lookup failed: {e}")
            return "（简报数据查询异常）"

    def _lookup_prediction(self, symbol: str) -> str:
        """Look up stock prediction data including technical indicators."""
        try:
            from models.market import StockInfo, PriceSnapshot, PriceHistory

            symbol = symbol.strip().upper()
            stock = StockInfo.query.filter_by(symbol=symbol).first()
            name = stock.name if stock else symbol

            lines = [f"【{name}({symbol}) 走势分析】"]

            # Real-time snapshot
            snap = PriceSnapshot.query.filter_by(symbol=symbol).first()
            if snap and snap.latest_price:
                change_str = f"{snap.change_pct:+.2f}%" if snap.change_pct else "--"
                lines.append(f"最新价: {snap.latest_price:.2f} | 涨跌: {change_str}")
                if snap.high and snap.low:
                    lines.append(f"日内: 高 {snap.high:.2f} / 低 {snap.low:.2f} / 开 {snap.open:.2f}" if snap.open else f"日内: 高 {snap.high:.2f} / 低 {snap.low:.2f}")
            else:
                lines.append("（实时行情暂未加载，建议在走势预测页面查看）")
                return "\n".join(lines)

            # 20-day price history + technical indicators
            hist = (
                PriceHistory.query
                .filter_by(symbol=symbol, period='daily')
                .order_by(PriceHistory.date.desc())
                .limit(30)
                .all()
            )

            if hist and len(hist) >= 5:
                closes = [h.close for h in hist if h.close]
                if len(closes) >= 5:
                    ma5 = sum(closes[:5]) / 5
                    ma10 = sum(closes[:10]) / min(10, len(closes[:10])) if len(closes) >= 10 else None
                    ma20 = sum(closes[:20]) / min(20, len(closes[:20])) if len(closes) >= 20 else None
                    latest = closes[0]
                    high_n = max(closes[:min(20, len(closes))])
                    low_n = min(closes[:min(20, len(closes))])

                    lines.append(f"均线: MA5 {ma5:.2f} | {'↑ 站上MA5' if latest > ma5 else '↓ 跌破MA5'}")
                    if ma10:
                        ma10_status = "金叉(↑)" if ma5 > ma10 else "死叉(↓)"
                        lines.append(f"MA10 {ma10:.2f} | MA5/MA10: {ma10_status}")
                    if ma20:
                        lines.append(f"MA20 {ma20:.2f}")

                    # MACD-like signal
                    if len(closes) >= 12:
                        ema12 = sum(closes[:12]) / 12
                        ema26 = sum(closes[:min(26, len(closes))]) / min(26, len(closes[:26]))
                        dif = ema12 - ema26
                        lines.append(f"DIF (EMA12-EMA26): {dif:+.3f} | {'多头' if dif > 0 else '空头'}排列")

                    # RSI-14 (Wilder's smoothing)
                    if len(closes) >= 15:
                        # Calculate price changes (oldest→newest, so reverse closes)
                        closes_chrono = list(reversed(closes))
                        gains = []
                        losses = []
                        for i in range(1, len(closes_chrono)):
                            chg = closes_chrono[i] - closes_chrono[i-1]
                            gains.append(max(chg, 0))
                            losses.append(max(-chg, 0))
                        period = 14
                        avg_gain = sum(gains[:period]) / period
                        avg_loss = sum(losses[:period]) / period
                        for i in range(period, len(gains)):
                            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
                        if avg_loss == 0:
                            rsi = 100
                        else:
                            rs = avg_gain / avg_loss
                            rsi = 100 - (100 / (1 + rs))
                        rsi_status = "超买" if rsi > 70 else ("超卖" if rsi < 30 else "中性")
                        lines.append(f"RSI-14: {rsi:.1f} ({rsi_status})")

                    lines.append(f"20日: 高 {high_n:.2f} / 低 {low_n:.2f} | 振幅 {((high_n - low_n) / low_n * 100):.1f}%")

            elif not hist:
                lines.append("（暂无历史K线数据）")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Prediction lookup failed: {e}")
            return f"（{symbol} 走势数据查询异常）"

    def _lookup_system_stats(self) -> str:
        """Aggregate system-wide stats: events, timeline, sources, briefing."""
        try:
            from datetime import date, datetime, timezone
            from models.card import EventCard
            from models.event import Event
            from models.source import NewsSource
            from models.timeline import TimelineNode, CausalEdge
            from models.briefing import DailyBriefing

            now = datetime.now(timezone.utc)
            today = date.today()

            # Event stats
            today_cards = EventCard.query.filter(
                EventCard.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0)
            ).count()
            total_cards = EventCard.query.count()
            s_level = EventCard.query.filter_by(level='S').count()
            a_level = EventCard.query.filter_by(level='A').count()

            # Source stats
            from models.source import NewsSource as Source
            active_sources = Source.query.filter_by(is_active=True).count()
            total_sources = Source.query.count()

            # Timeline stats
            total_nodes = TimelineNode.query.count()
            total_edges = CausalEdge.query.count()
            prediction_nodes = TimelineNode.query.filter_by(node_type='prediction').count()

            # Briefing
            latest_briefing = DailyBriefing.query.order_by(DailyBriefing.date.desc()).first()
            briefing_date = latest_briefing.date.isoformat() if latest_briefing else "暂无"

            lines = [f"【BubbleEvent 系统状态 — {today.isoformat()}】"]
            lines.append(f"📰 事件卡片: 今日 {today_cards} | 总计 {total_cards} (S级 {s_level}, A级 {a_level})")
            lines.append(f"📡 信息源: {active_sources}/{total_sources} 活跃")
            lines.append(f"🕸️ Bubble: {total_nodes} 节点 · {total_edges} 因果边 · {prediction_nodes} 预测节点")
            lines.append(f"📋 最新简报: {briefing_date}")
            lines.append(f"🟢 系统状态: 正常运行")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"System stats lookup failed: {e}")
            return "（系统状态查询异常，请稍后重试）"

    def _execute_action(self, user_message: str) -> tuple:
        """Parse user intent into a specific action + params using LLM, then execute.

        Returns (action_name: str | None, result_summary: str).
        """
        llm = self._get_llm()
        actions_desc = get_available_actions_markdown()

        parse_prompt = f"""你是动作解析器。根据用户输入选择要执行的操作并提取参数。

{actions_desc}

用户输入: {user_message}

请严格按以下JSON格式输出（只输出JSON，不要其他内容）:
{{"action": "操作名称（从上述列表中选择）", "params": {{"参数名": "参数值"}}, "reason": "简短说明为何选择此操作"}}

如果用户输入不匹配任何可用操作，输出: {{"action": null, "reason": "不匹配"}}"""

        try:
            result = llm.complete("解析用户操作意图。只输出JSON。", parse_prompt, max_tokens=300)
            text = result.strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json.loads(text)
        except Exception as e:
            logger.error(f"Action parsing failed: {e}")
            return None, "无法解析操作意图，请换个方式描述。"

        action_name = parsed.get("action")
        if not action_name:
            return None, parsed.get("reason", "当前消息不匹配任何可执行操作。")

        # Validate action exists
        if action_name not in ACTIONS:
            available = ", ".join(ACTIONS.keys())
            return None, f"未知操作 '{action_name}'。可用操作: {available}"

        params = parsed.get("params", {})

        # Check if destructive action — flag for confirmation
        needs_confirm = action_name in DESTRUCTIVE_ACTIONS

        if needs_confirm:
            self._pending_action = {"name": action_name, "params": params}
            result_text = execute_action(action_name, params, confirmed=False)
            return action_name, result_text

        result_text = execute_action(action_name, params, confirmed=True)
        return action_name, result_text

    def _classify_intent(self, message: str) -> str:
        """Classify user intent to activate appropriate data sources.

        Returns one of: action, event, briefing, timeline, prediction, system, general
        """
        msg_lower = message.lower()

        # Action intent — strong signal keywords for operations
        action_keywords = [
            "更新", "添加", "删除", "开启", "关闭", "生成", "触发",
            "采集", "重建", "清理", "切换", "禁用", "启用", "增加",
            "创建", "移除", "执行", "运行", "添加源", "添加信息源",
            "帮我采集", "帮我更新", "帮我生成", "帮我触发", "帮我重建",
        ]
        if any(kw in msg_lower for kw in action_keywords):
            return "action"

        # Briefing intent
        briefing_keywords = ["简报", "今日", "今天", "昨天", "日报", "更新简报", "生成简报", "briefing"]
        if any(kw in msg_lower for kw in briefing_keywords):
            return "briefing"

        # Timeline intent
        timeline_keywords = ["时间线", "因果", "节点", "边", "timeline", "网络", "因果链", "根事件", "预测节点"]
        if any(kw in msg_lower for kw in timeline_keywords):
            return "timeline"

        # Prediction intent (stock symbols or trend analysis)
        pred_keywords = ["走势", "预测", "k线", "均线", "macd", "rsi", "技术分析", "趋势", "股价", "行情"]
        # Also check for stock symbol patterns (6-digit codes)
        import re
        if re.search(r'\b\d{6}\b', message):
            return "prediction"
        if any(kw in msg_lower for kw in pred_keywords):
            return "prediction"

        # System stats intent
        system_keywords = ["系统状态", "统计", "多少", "几个", "数量", "dashboard", "全貌", "概况", "运行"]
        if any(kw in msg_lower for kw in system_keywords):
            return "system"

        # Default to event analysis
        return "event"

    def process_message(
        self,
        session_id: str,
        user_message: str,
        focus_industries: list[str] = None,
    ) -> dict:
        """Process a user message and return assistant response.

        Uses intent classification to activate appropriate data sources in parallel.
        """
        chain = ReasoningChain()
        tool_calls = []
        sources = []

        # Store focus industries for lookup boosting
        self._focus_industries = focus_industries or []

        # Step 1: Classify intent
        intent = self._classify_intent(user_message)
        chain.add_step(
            "intent_classification",
            f"识别意图: {intent}",
            output_data={"intent": intent},
        )

        # Step 2: Parallel data retrieval based on intent
        chain.add_step(
            "knowledge_lookup",
            "并行检索系统数据",
            input_data={"intent": intent, "query": user_message[:100]},
        )

        # Always retrieve events (core capability)
        events_text = self._lookup_events(user_message)
        market_text = self._lookup_market(user_message, events_text)

        # Intent-specific data sources
        timeline_text = ""
        briefing_text = ""
        prediction_text = ""
        system_text = ""

        if intent == "timeline":
            timeline_text = self._lookup_timeline_stats(user_message)
            tool_calls.append({"tool": "timeline_stats", "intent": intent})

        if intent == "briefing":
            # Check if user wants regeneration
            regenerate = any(kw in user_message for kw in ["更新", "生成", "重新", "刷新"])
            briefing_text = self._lookup_briefing(regenerate=regenerate)
            tool_calls.append({"tool": "briefing", "intent": intent, "regenerate": regenerate})

        if intent == "prediction":
            # Extract stock symbol
            import re
            symbols = re.findall(r'\b\d{6}\b', user_message)
            if symbols:
                prediction_text = self._lookup_prediction(symbols[0])
            else:
                prediction_text = self._lookup_market(user_message, events_text)  # enhanced market lookup
            tool_calls.append({"tool": "prediction", "intent": intent})

        if intent == "system":
            system_text = self._lookup_system_stats()
            tool_calls.append({"tool": "system_stats", "intent": intent})

        # Action intent — execute operations
        action_result = ""
        action_name = ""
        if intent == "action":
            # Check if user is confirming a pending destructive action
            confirm_keywords = ["确认", "是的", "继续", "确定", "好", "可以", "yes", "ok", "执行"]
            if self._pending_action and any(kw in user_message for kw in confirm_keywords):
                action_name = self._pending_action["name"]
                action_params = self._pending_action["params"]
                action_result = execute_action(action_name, action_params, confirmed=True)
                self._pending_action = None
            else:
                action_name, action_result = self._execute_action(user_message)
            system_text = self._lookup_system_stats()
            tool_calls.append({"tool": "action", "action": action_name, "result": action_result[:100]})

        # General intent gets a lightweight system overview too
        if intent == "event" or intent == "general":
            system_text = self._lookup_system_stats()

        chain.add_step(
            "knowledge_lookup",
            "检索完成",
            output_data={
                "events": events_text[:80],
                "market": market_text[:80],
                "timeline": timeline_text[:80] if timeline_text else "",
                "briefing": briefing_text[:80] if briefing_text else "",
                "prediction": prediction_text[:80] if prediction_text else "",
                "system": system_text[:80] if system_text else "",
            },
        )

        # Step 3: Supplementary web search (only if all system data is empty)
        tool_results = []
        all_empty = (
            ("暂无" in events_text or "查询异常" in events_text)
            and ("暂无" in market_text or "查询异常" in market_text)
            and not briefing_text
            and not timeline_text
            and not prediction_text
        )
        if all_empty:
            try:
                results = web_search(user_message, max_results=3)
                tool_results.extend(results)
                tool_calls.append({"tool": "search", "query": user_message[:80], "results_count": len(results)})
                for r in results:
                    if r.get("url"):
                        sources.append({"title": r.get("title", ""), "url": r["url"]})
                chain.add_step("tool_call", "搜索补充信息", output_data={"results": len(results)})
            except Exception as e:
                logger.warning(f"Search failed: {e}")

        # Step 4: Generate response with all gathered context
        chain.add_step(
            "llm_inference",
            "基于全系统数据生成回答",
            confidence=0.85,
        )

        response_text = self._generate_response(
            session_id=session_id,
            user_message=user_message,
            events_text=events_text,
            market_text=market_text,
            tool_results=tool_results,
            chain=chain,
            extra_context={
                "timeline": timeline_text,
                "briefing": briefing_text,
                "prediction": prediction_text,
                "system": system_text,
                "action_result": action_result,
                "action_name": action_name,
                "focus_industries": self._focus_industries,
            },
        )

        # Step 5: Save messages
        self.chat_manager.add_message(
            session_id=session_id, role="user", content=user_message,
        )
        self.chat_manager.add_message(
            session_id=session_id,
            role="assistant",
            content=response_text,
            reasoning_chain=chain,
            tool_calls=tool_calls,
            sources=sources,
        )

        return {
            "response": response_text,
            "reasoning_chain": chain.to_dict(),
            "tool_calls": tool_calls,
            "sources": sources,
        }

    def _generate_response(
        self,
        session_id: str,
        user_message: str,
        events_text: str,
        market_text: str,
        tool_results: list[dict],
        chain: ReasoningChain,
        extra_context: dict = None,
    ) -> str:
        """Generate the final response using LLM with event + market + extra context."""
        llm = self._get_llm()
        context_messages = self.chat_manager.get_context_messages(session_id)
        extra_context = extra_context or {}

        # Build full context from all available data sources
        context_parts = [CONTEXT_TEMPLATE.format(
            events_text=events_text,
            market_text=market_text,
        )]

        # Append extra context sections
        timeline_text = extra_context.get("timeline", "")
        briefing_text = extra_context.get("briefing", "")
        prediction_text = extra_context.get("prediction", "")
        system_text = extra_context.get("system", "")

        if timeline_text:
            context_parts.append(f"\n{timeline_text}")
        if briefing_text:
            context_parts.append(f"\n{briefing_text}")
        if prediction_text:
            context_parts.append(f"\n{prediction_text}")
        if system_text:
            context_parts.append(f"\n{system_text}")

        # Action result (Feature: operation execution)
        action_result = extra_context.get("action_result", "")
        if action_result:
            context_parts.append(f"\n【操作结果】\n{action_result}")

        full_context = "\n".join(context_parts)

        supp_context = ""
        if tool_results:
            supp_context = "\n\n补充搜索结果（仅供参考）：\n"
            for i, tr in enumerate(tool_results[:3]):
                snippet = json.dumps(tr, ensure_ascii=False)[:300]
                supp_context += f"\n[{i+1}] {snippet}\n"

        # Build instruction based on which data sources are available
        data_types = []
        if events_text and "暂无" not in events_text:
            data_types.append("事件")
        if market_text and "暂无" not in market_text:
            data_types.append("行情")
        if timeline_text:
            data_types.append("时间线")
        if briefing_text:
            data_types.append("简报")
        if prediction_text:
            data_types.append("走势预测")
        if system_text:
            data_types.append("系统状态")
        if action_result:
            data_types.append("操作结果")

        data_hint = "、".join(data_types) if data_types else "通用知识"

        # Augment system prompt with focus industries
        focus_industries = extra_context.get("focus_industries", self._focus_industries)
        augmented_prompt = RESEARCH_SYSTEM_PROMPT
        if focus_industries:
            industry_list = "、".join(focus_industries)
            augmented_prompt += (
                f"\n\n用户关注的行业: {industry_list}\n"
                "当事件或行情涉及这些行业时，应主动提及并优先展示。"
                "在分析事件影响时，特别说明对用户关注行业的影响。"
            )

        messages = [
            {"role": "system", "content": augmented_prompt},
        ]
        for msg in context_messages[-6:]:
            messages.append(msg)

        messages.append({
            "role": "user",
            "content": (
                f"{full_context}\n{supp_context}\n"
                f"用户问题: {user_message}\n\n"
                f"已激活数据源: {data_hint}\n"
                f"请基于以上数据综合回答。优先使用系统数据，区分事实与推测。不要提及工具使用或系统内部细节。"
            )
        })

        try:
            # Use larger max_tokens for complex queries with multiple data sources
            max_tokens = 2048 if len(data_types) >= 3 else 1536
            response = llm.chat(messages, max_tokens=max_tokens, temperature=0.3)
            return response.strip()
        except Exception as e:
            logger.error(f"LLM response generation failed: {e}")
            return f"抱歉，生成回答时遇到了问题。请稍后重试。"
