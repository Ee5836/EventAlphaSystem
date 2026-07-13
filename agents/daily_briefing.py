"""Daily Briefing Agent — generates structured daily investment report."""
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.extensions import db
from models.briefing import DailyBriefing
from models.card import EventCard
from models.source import NewsSource
from llm.factory import get_llm
from agents.market_data import MarketDataAgent

logger = logging.getLogger(__name__)

BRIEFING_SYSTEM_PROMPT = """你是一个专业的金融信息编辑，负责生成每日投资简报。

简报要求：
1. 客观、准确，引用具体数据
2. 区分事实陈述与分析推测
3. 中文表达简洁有力
4. 标注数据来源和时间
5. 每个板块不超过300字

⚠️ 所有内容仅供参考，不构成投资建议。"""

SECTION_PROMPTS = {
    "executive_summary": "基于以下今日事件和市场数据，生成2-3句核心摘要(200字内)。重点关注：1)最重要的1-2个事件 2)市场整体基调 3)需要关注的风险。",
    "market_snapshot": (
        "基于以下市场数据，生成今日市场快照(200字内)。"
        "必须包含：1)主要指数涨跌 2)领涨/领跌板块(从行业数据中提取) 3)市场情绪判断。"
        "如果某项数据缺失，用已有数据推断，绝对不要写'数据未提供'或'无法生成'之类的表述。"
    ),
    "top_events": "基于以下事件列表，选出最重要的5-8个S级/A级事件，每个用1-2句话概括关键信息。",
    "prediction_summary": "基于以下预测数据，生成走势预测速览(200字内)。突出：最看涨/看跌的标的，置信度排序。",
    "risk_alert": (
        "基于以下真实事件风险标签和今日市场数据，列出未来1周需要关注的3-5个具体风险事件。"
        "每条风险必须：1) 关联具体事件或数据 2) 说明潜在影响路径 3) 避免笼统空泛的表述。"
        "严格按以下格式输出，每行一个风险，不要使用markdown标题或编号：\n"
        "风险：xxx事件导致xxx风险，可能影响xxx\n"
        "风险：xxx\n"
        "风险：xxx"
    ),
}


class DailyBriefingAgent:
    """Generates daily investment briefing by aggregating all pipeline outputs."""

    def __init__(self):
        pass

    def generate(self, target_date: Optional[date] = None, force: bool = False) -> Optional[DailyBriefing]:
        """Generate a full daily briefing.

        Args:
            target_date: Target date (default: today).
            force: If True, overwrite existing briefing for this date.
                   If False (default), return cached if exists.

        Returns:
            DailyBriefing model instance, or None on failure.
        """
        if target_date is None:
            target_date = date.today()

        # Check cache (skip when force=True)
        if not force:
            existing = DailyBriefing.query.filter_by(date=target_date).first()
            if existing:
                logger.info(f"Briefing for {target_date} already exists, returning cached")
                return existing
        else:
            # Delete old briefing if force-regenerating
            old = DailyBriefing.query.filter_by(date=target_date).first()
            if old:
                db.session.delete(old)
                db.session.flush()
                logger.info(f"Force-regenerating briefing for {target_date}")

        try:
            briefing = DailyBriefing(date=target_date)

            # ── Phase 1: Collect all data sources ─────────────────────
            events = self._get_today_events(target_date)
            sources_count = NewsSource.query.filter_by(is_active=True).count()

            briefing.sources_count = sources_count
            briefing.articles_processed = len(events)
            briefing.event_stats_json = self._count_events(events)
            briefing.top_events_json = events[:10]

            # ── Phase 1b: Fetch live market data ──────────────────────
            market_agent = MarketDataAgent()

            # Sector performance (heatmap data)
            sector_data = market_agent.fetch_sector_performance()
            briefing.sector_heatmap_json = {
                "sectors": sector_data,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            # Index data for market snapshot context
            index_context = self._build_index_context(market_agent)

            # Collect risk_flags from S/A events for risk section
            risk_context = self._build_risk_context(events)

            # ── Phase 2: Generate each section via LLM ────────────────
            llm = get_llm()

            # Executive Summary
            briefing.executive_summary = self._generate_section(
                llm, "executive_summary",
                events=json.dumps(events[:5], ensure_ascii=False),
                sector_summary=json.dumps(self._summarize_sectors(sector_data), ensure_ascii=False),
            )

            # Market Snapshot — index data + sector data as primary context
            sector_summary = json.dumps(self._summarize_sectors(sector_data), ensure_ascii=False)
            briefing.market_snapshot_json = {
                "summary": self._generate_section(
                    llm, "market_snapshot",
                    index_data=index_context,
                    sector_data=sector_summary if sector_summary != "[]" else "行业数据暂无",
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Prediction Summary
            briefing.prediction_summary_json = {
                "summary": self._generate_section(
                    llm, "prediction_summary",
                    events=json.dumps(events[:8], ensure_ascii=False),
                ),
                "predictions": [],
            }

            # Risk Alert — pass aggregated risk_flags from real events
            risk_text = self._generate_section(
                llm, "risk_alert",
                top_events=json.dumps(events[:8], ensure_ascii=False),
                risk_flags=risk_context,
                index_data=index_context,
            )
            # Parse LLM response: prefer lines with meaningful content, fall back
            # to treating the entire response as one alert.
            raw_lines = [l.strip() for l in risk_text.split("\n") if l.strip()]
            risk_alerts = []
            for line in raw_lines:
                cleaned = line.lstrip("-·*# 	").strip()
                if not cleaned:
                    continue
                is_warning = "风险" in cleaned or "risk" in cleaned.lower()
                risk_alerts.append({
                    "alert": cleaned,
                    "level": "warning" if is_warning else "info",
                })
            if not risk_alerts and risk_text.strip():
                risk_alerts.append({
                    "alert": risk_text.strip()[:300],
                    "level": "warning" if "风险" in risk_text else "info",
                })
            briefing.risk_alert_json = risk_alerts[:5]
# Key numbers
            briefing.key_numbers_json = {
                "total_events": len(events),
                "s_level": sum(1 for e in events if e.get("level") == "S"),
                "a_level": sum(1 for e in events if e.get("level") == "A"),
                "active_sources": sources_count,
                "sectors_updated": len(sector_data),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Title
            briefing.title = f"BubbleEvent 每日投资简报 — {target_date.strftime('%Y年%m月%d日')}"

            # Full markdown report
            briefing.full_report_md = self._build_markdown(briefing)

            briefing.generated_at = datetime.now(timezone.utc)

            db.session.add(briefing)
            db.session.commit()

            logger.info(f"Generated daily briefing for {target_date} with {len(sector_data)} sectors, {len(events)} events")
            return briefing

        except Exception as e:
            logger.error(f"Failed to generate briefing for {target_date}: {e}")
            db.session.rollback()
            return None

    # Beijing timezone offset (UTC+8)
    _BEIJING_TZ = timezone(timedelta(hours=8))

    def _get_today_events(self, target_date: date) -> list[dict]:
        """Get top events for a given date (in Beijing time UTC+8)."""
        start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=self._BEIJING_TZ)
        end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=self._BEIJING_TZ)

        cards = (
            EventCard.query
            .filter(EventCard.created_at.between(start, end))
            .order_by(EventCard.created_at.desc())
            .limit(20)
            .all()
        )

        if not cards:
            # Fallback: get most recent events
            cards = (
                EventCard.query
                .order_by(EventCard.created_at.desc())
                .limit(20)
                .all()
            )

        return [
            {
                "id": c.id,
                "event_id": c.event_id,
                "title": c.title,
                "summary": c.summary,
                "level": c.level,
                "credibility_label": c.credibility_label,
                "event_type": c.event_type,
                "affected_industries": c.affected_industries or [],
                "source_summary": c.source_summary,
                "risk_flags": c.risk_flags_json or [],
            }
            for c in cards
        ]

    @staticmethod
    def _count_events(events: list[dict]) -> dict:
        """Count events by level."""
        counts = {"total": len(events), "S": 0, "A": 0, "B": 0, "C": 0}
        for e in events:
            level = e.get("level", "C")
            counts[level] = counts.get(level, 0) + 1
        return counts

    @staticmethod
    def _summarize_sectors(sector_data: list[dict]) -> list[dict]:
        """Extract a compact summary of sector performance for LLM context."""
        if not sector_data:
            return []
        summary = []
        for s in sector_data[:10]:
            summary.append({
                "name": s.get("name", ""),
                "change": f"{s.get('change_pct', 0):+.2f}%",
                "up": s.get("up_count", 0),
                "down": s.get("down_count", 0),
            })
        return summary

    @staticmethod
    def _build_index_context(market_agent) -> str:
        """Fetch major index data and return a text summary for LLM context.

        Tries multiple indices: 上证综指, 深证成指, 创业板指.
        Returns a composite summary string.
        """
        # (index_code, prefix, display_name)
        # sh = Shanghai, sz = Shenzhen
        index_list = [
            ("000001", "sh", "上证综指"),
            ("399001", "sz", "深证成指"),
            ("399006", "sz", "创业板指"),
        ]
        lines = []
        for code, prefix, name in index_list:
            try:
                # Build full AKShare symbol: sh000001 / sz399001 / sz399006
                symbol = f"{prefix}{code}"
                data = market_agent.fetch_index_data(symbol)
                if data and len(data) >= 2:
                    latest = data[-1]
                    prev = data[-2]
                    prev_close = prev.get("close", 0)
                    if prev_close and prev_close > 0:
                        chg = (latest.get("close", 0) - prev_close) / prev_close * 100
                        vol_yi = latest.get("volume", 0) // 100000000
                        lines.append(
                            f"{name}: {latest.get('close', 0):.2f} "
                            f"({chg:+.2f}%), 成交{vol_yi}亿"
                        )
            except Exception as e:
                logger.warning(f"Failed to fetch {name}({symbol}): {e}")

        if lines:
            return "今日主要指数：\n" + "\n".join(lines)

        # Ultimate fallback — no index data at all
        return "今日指数数据暂未更新（非交易时间或数据源延迟）"

    @staticmethod
    def _build_risk_context(events: list[dict]) -> str:
        """Aggregate risk_flags from all events into LLM context.

        S/A level events are listed first (higher priority), followed by
        B-level events with risk flags. This ensures the risk section is
        populated even when no S/A events carry explicit risk tags.
        """
        high_priority = []
        low_priority = []
        for e in events:
            level = e.get("level", "")
            flags = e.get("risk_flags", [])
            if not flags:
                continue
            if level in ("S", "A"):
                for flag in flags:
                    high_priority.append(f"[{level}级] {e.get('title', '')} → {flag}")
            elif level == "B":
                for flag in flags[:2]:  # limit B-level flags to avoid noise
                    low_priority.append(f"[{level}级] {e.get('title', '')} → {flag}")

        if not high_priority and not low_priority:
            return "今日暂无已标记的风险事件（请基于事件标题和摘要自行判断潜在风险）"

        parts = []
        if high_priority:
            parts.append("🔴 高优先级（S/A级事件风险标签）：\n" + "\n".join(high_priority))
        if low_priority:
            parts.append("🟡 次要关注（B级事件风险标签）：\n" + "\n".join(low_priority))

        return "以下为系统从今日事件中提取的风险标签：\n" + "\n".join(parts)

    def _generate_section(self, llm, section_name: str, **context) -> str:
        """Generate a single briefing section using LLM."""
        prompt = SECTION_PROMPTS.get(section_name, "请生成内容。")
        ctx_parts = [f"{k}: {v}" for k, v in context.items()]
        user_message = prompt + "\n\n" + "\n".join(ctx_parts)

        try:
            result = llm.complete(BRIEFING_SYSTEM_PROMPT, user_message, max_tokens=500, temperature=0.3)
            return result.strip()
        except Exception as e:
            logger.error(f"Failed to generate section '{section_name}': {e}")
            return f"[{section_name} generation failed: {e}]"

    def _build_markdown(self, briefing: DailyBriefing) -> str:
        """Build full markdown report from briefing sections."""
        parts = [
            f"# {briefing.title}",
            "",
            "> ⚠️ 本报告由 AI 自动生成，仅供研究参考，不构成投资建议。",
            "",
            "## 一、执行摘要",
            briefing.executive_summary or "",
            "",
            "## 二、市场快照",
            briefing.market_snapshot_json.get("summary", "") if isinstance(briefing.market_snapshot_json, dict) else "",
            "",
            "## 三、今日重要事件",
        ]

        for i, event in enumerate(briefing.top_events_json or []):
            level = event.get("level", "")
            parts.append(f"### [{level}级] {event.get('title', '')}")
            parts.append(f"{event.get('summary', '')}")
            parts.append(f"可信度: {event.get('credibility_label', '')} | 类型: {event.get('event_type', '')}")
            parts.append("")

        parts.extend([
            "## 四、走势预测速览",
            briefing.prediction_summary_json.get("summary", "") if isinstance(briefing.prediction_summary_json, dict) else "",
            "",
            "## 五、风险预警",
        ])

        for alert in (briefing.risk_alert_json or []):
            if isinstance(alert, dict):
                parts.append(f"- ⚠️ {alert.get('alert', '')}")

        parts.extend([
            "",
            "---",
            f"📊 数据统计: 采集源 {briefing.sources_count} 个 | 事件 {briefing.event_stats_json.get('total', 0)} 个",
            f"生成时间: {briefing.generated_at.strftime('%Y-%m-%d %H:%M') if briefing.generated_at else ''}",
        ])

        return "\n".join(parts)
