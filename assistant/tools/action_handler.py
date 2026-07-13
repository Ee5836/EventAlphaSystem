"""Action handler for AI Assistant — executes internal project API actions via direct Python calls.

All handlers are called within the Flask request context (since process_message runs inside
a request handler), so they have access to current_app, db.session, etc.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger(__name__)


# ── Action definition ─────────────────────────────────────────────────

@dataclass
class ActionDef:
    """Description of an available action for LLM parsing."""
    name: str                       # e.g. "trigger_pipeline"
    category: str                   # "pipeline" | "sources" | "briefing" | "timeline"
    description: str                # for system prompt
    method: str                     # "POST" | "GET" | "DELETE"
    endpoint: str                   # API path for reference
    params_desc: dict               # {param_name: description}
    requires_confirmation: bool = False
    handler: Optional[Callable] = None


# ── Handler implementations ──────────────────────────────────────────

def _trigger_pipeline(force: bool = False, mode: str = "full"):
    """Trigger pipeline: mode='full' or 'scout'."""
    from pipeline.orchestrator import PipelineOrchestrator

    if mode == "scout":
        from agents.scout import ScoutAgent
        agent = ScoutAgent()
        result = agent.run(force=force)
        count = result.metadata.get("total_articles", 0) if hasattr(result, "metadata") else 0
        return f"✅ 信息采集完成，共收集 {count} 篇文章。"
    else:
        orchestrator = PipelineOrchestrator()
        result = orchestrator.run_full_pipeline(force_scout=force, fast_mode=True)
        cards = result.metadata.get("cards_generated", 0)
        articles = result.metadata.get("articles_collected", 0)
        msg = f"✅ 管道执行完成：采集 {articles} 篇文章，生成 {cards} 张事件卡片。"
        if result.errors:
            msg += f" (⚠️ {len(result.errors)} 个警告)"
        return msg


def _manage_source(sub_action: str = "list", name: str = "", source_id: str = "",
                   source_type: str = "rss", url: str = "", **kwargs):
    """Source CRUD operations via direct DB calls."""
    from app.extensions import db
    from models.source import NewsSource
    from datetime import datetime, timezone

    key = source_id or name

    if sub_action == "list":
        sources = NewsSource.query.order_by(NewsSource.created_at.desc()).all()
        if not sources:
            return "📡 当前暂无信息源。"
        lines = ["📡 **信息源列表**"]
        for s in sources:
            status = "🟢 启用" if s.is_active else "⚫ 禁用"
            sys_tag = " [系统]" if s.is_system else ""
            lines.append(f"  • {s.name}{sys_tag} ({s.source_type}) — {status}")
        return "\n".join(lines)

    # ── Operations that need a specific source ────────────────────
    source = None
    if source_id:
        source = NewsSource.query.get(source_id)
    elif name:
        source = NewsSource.query.filter_by(name=name).first()
        if not source:
            # Fuzzy match by display_name
            source = NewsSource.query.filter(
                NewsSource.display_name.contains(name)
            ).first()

    if sub_action == "toggle":
        if not source:
            return f"❌ 未找到信息源: {name or source_id}"
        source.is_active = not source.is_active
        source.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        status = "启用" if source.is_active else "禁用"
        return f"✅ 信息源 '{source.display_name or source.name}' 已{status}。"

    elif sub_action == "delete":
        if not source:
            return f"❌ 未找到信息源: {name or source_id}"
        if source.is_system:
            return f"❌ 系统信息源 '{source.name}' 不可删除。如需停用，请使用「切换」操作。"
        label = source.display_name or source.name
        db.session.delete(source)
        db.session.commit()
        return f"✅ 信息源 '{label}' 已删除。"

    elif sub_action == "add":
        if not name:
            return "❌ 请提供信息源名称。例如：「添加信息源 华尔街见闻，类型 rss，地址 https://...」"
        existing = NewsSource.query.filter_by(name=name).first()
        if existing:
            return f"❌ 信息源 '{name}' 已存在。"
        source = NewsSource(
            name=name,
            display_name=name,
            source_type=source_type or "rss",
            base_url=url or "",
            is_active=True,
            is_system=False,
            created_by="assistant",
        )
        db.session.add(source)
        db.session.commit()
        return f"✅ 信息源 '{name}' 已添加（类型: {source_type or 'rss'}，待采集）。"

    elif sub_action == "collect":
        if not source:
            return f"❌ 未找到信息源: {name or source_id}"
        from agents.scout import ScoutAgent
        agent = ScoutAgent()
        result = agent.run(source_names=[source.name])
        count = result.metadata.get("total_articles", 0) if hasattr(result, "metadata") else 0
        return f"✅ 从 '{source.display_name or source.name}' 采集到 {count} 篇文章。"

    return f"❌ 未知操作: {sub_action}"


def _handle_briefing(action: str = "generate", date: str = ""):
    """Briefing generation / lookup."""
    from datetime import date as dt_date

    if action == "generate":
        from agents.daily_briefing import DailyBriefingAgent
        agent = DailyBriefingAgent()
        target = dt_date.today()
        if date:
            try:
                target = dt_date.fromisoformat(date)
            except ValueError:
                return f"❌ 日期格式错误: {date}，请使用 YYYY-MM-DD 格式。"
        briefing = agent.generate(target_date=target, force=True)
        if briefing:
            return f"✅ 简报已生成（{target.isoformat()}）。可在简报页面查看完整内容。"
        return "❌ 简报生成失败，可能当日无事件数据。"
    elif action == "latest":
        from models.briefing import DailyBriefing
        briefing = DailyBriefing.query.order_by(DailyBriefing.date.desc()).first()
        if briefing:
            return f"📋 最新简报日期: {briefing.date.isoformat()}"
        return "📋 暂无简报。"
    return f"❌ 未知简报操作: {action}"


def _handle_timeline(action: str = "stats", event_id: str = ""):
    """Timeline operations."""
    if action == "rebuild":
        from agents.timeline_builder import TimelineBuilderAgent
        agent = TimelineBuilderAgent()
        result = agent.rebuild()
        nodes = result.get("nodes_created", 0)
        edges = result.get("edges_created", 0)
        return f"✅ 时间线已重建：{nodes} 个节点，{edges} 条因果边。"

    elif action == "discover":
        if not event_id:
            return "❌ 请指定要发现因果关系的事件 ID。"
        from agents.timeline_builder import TimelineBuilderAgent
        agent = TimelineBuilderAgent()
        node = agent.add_event_node(event_id)
        if not node:
            return f"❌ 未找到事件: {event_id}"
        edges = agent.discover_causal_links(node.id)
        return f"✅ 已为事件发现 {len(edges)} 条因果关联（节点ID: {node.id}）。"

    elif action == "cleanup":
        from agents.timeline_builder import TimelineBuilderAgent
        agent = TimelineBuilderAgent()
        result = agent.cleanup_expired_nodes(older_than_days=30)
        deleted = result.get("deleted_nodes", 0)
        return f"✅ 已清理 {deleted} 个过期时间线节点。"

    return f"❌ 未知时间线操作: {action}"


# ── Action registry ──────────────────────────────────────────────────

ACTIONS: dict[str, ActionDef] = {
    # ── Pipeline ──
    "trigger_pipeline": ActionDef(
        "触发管道", "pipeline", "触发完整事件处理管道（采集→提取→聚类→验证→评分→卡片）",
        "POST", "/api/v1/pipeline/trigger",
        {"force": "强制采集（跳过间隔检查）", "mode": "full 或 scout"},
        requires_confirmation=False,
        handler=lambda **kw: _trigger_pipeline(**kw),
    ),
    "trigger_scout": ActionDef(
        "采集新闻", "pipeline", "仅从活跃信息源采集新闻文章，不进行下游处理",
        "POST", "/api/v1/pipeline/trigger/scout",
        {"force": "强制采集（跳过间隔检查）"},
        requires_confirmation=False,
        handler=lambda **kw: _trigger_pipeline(mode="scout", **kw),
    ),

    # ── Sources ──
    "list_sources": ActionDef(
        "列出信息源", "sources", "列出所有信息源及其启用/禁用状态",
        "GET", "/api/v1/sources",
        {},
        requires_confirmation=False,
        handler=lambda **kw: _manage_source(sub_action="list"),
    ),
    "add_source": ActionDef(
        "添加信息源", "sources", "添加一个新的 RSS/网页/API 信息源",
        "POST", "/api/v1/sources",
        {"name": "信息源名称", "type": "rss / webpage / api", "url": "信息源地址"},
        requires_confirmation=False,
        handler=lambda **kw: _manage_source(sub_action="add", **kw),
    ),
    "toggle_source": ActionDef(
        "切换信息源", "sources", "启用或禁用一个信息源",
        "POST", "/api/v1/sources/<id>/toggle",
        {"name": "信息源名称或ID"},
        requires_confirmation=False,
        handler=lambda **kw: _manage_source(sub_action="toggle", **kw),
    ),
    "delete_source": ActionDef(
        "删除信息源", "sources", "删除一个用户添加的信息源（系统源不可删除）",
        "DELETE", "/api/v1/sources/<id>",
        {"name": "信息源名称或ID"},
        requires_confirmation=True,
        handler=lambda **kw: _manage_source(sub_action="delete", **kw),
    ),
    "collect_source": ActionDef(
        "采集单个源", "sources", "从指定的单个信息源采集文章",
        "POST", "/api/v1/sources/<id>/collect",
        {"name": "信息源名称或ID"},
        requires_confirmation=False,
        handler=lambda **kw: _manage_source(sub_action="collect", **kw),
    ),

    # ── Briefing ──
    "generate_briefing": ActionDef(
        "生成简报", "briefing", "生成每日投资简报（摘要+市场快照+风险预警）",
        "POST", "/api/v1/briefing/generate",
        {"date": "日期 YYYY-MM-DD（可选，默认今天）"},
        requires_confirmation=False,
        handler=lambda **kw: _handle_briefing("generate", date=kw.get("date", "")),
    ),

    # ── Timeline ──
    "rebuild_timeline": ActionDef(
        "重建时间线", "timeline", "删除全部时间线节点和因果边，从事件卡片重新构建",
        "POST", "/api/v1/timeline/rebuild",
        {},
        requires_confirmation=True,
        handler=lambda **kw: _handle_timeline("rebuild"),
    ),
    "discover_causal": ActionDef(
        "因果发现", "timeline", "为指定事件发现因果关系并添加到时间线",
        "POST", "/api/v1/timeline/discover",
        {"event_id": "事件 ID"},
        requires_confirmation=False,
        handler=lambda **kw: _handle_timeline("discover", **kw),
    ),
    "cleanup_expired": ActionDef(
        "清理过期节点", "timeline", "删除所有过期超过30天的时间线节点和关联边",
        "POST", "/api/v1/timeline/cleanup",
        {},
        requires_confirmation=True,
        handler=lambda **kw: _handle_timeline("cleanup"),
    ),
}

# ── Destructive actions (require user confirmation) ──────────────────

DESTRUCTIVE_ACTIONS = {"delete_source", "rebuild_timeline", "cleanup_expired"}


# ── Public API ──────────────────────────────────────────────────────

def get_available_actions_markdown() -> str:
    """Generate a markdown summary of available actions for the system prompt."""
    cats: dict[str, list[tuple[str, ActionDef]]] = {}
    for key, a in ACTIONS.items():
        cats.setdefault(a.category, []).append((key, a))

    lines = ["## 可执行操作"]
    cat_labels = {
        "pipeline": "🔧 管道", "sources": "📡 信息源",
        "briefing": "📋 简报", "timeline": "🕸️ 时间线",
    }
    for cat, items in cats.items():
        label = cat_labels.get(cat, cat)
        lines.append(f"### {label}")
        for key, a in items:
            safety = " ⚠️需确认" if key in DESTRUCTIVE_ACTIONS else ""
            params_str = ", ".join(f"{k}: {v}" for k, v in a.params_desc.items())
            lines.append(f"- **{a.name}**{safety}: {a.description}")
            if params_str:
                lines.append(f"  参数: {params_str}")
    return "\n".join(lines)


def execute_action(action_name: str, params: dict = None, confirmed: bool = False) -> str:
    """Execute a named action via direct Python call.

    Returns a natural-language summary string suitable for display to the user.
    If the action requires confirmation and confirmed=False, returns a
    confirmation-request message instead.

    Args:
        action_name: Key in the ACTIONS registry.
        params: Dict of parameter values to pass to the handler.
        confirmed: True if user has already confirmed a destructive action.
    """
    params = params or {}
    action = ACTIONS.get(action_name)
    if not action:
        available = ", ".join(ACTIONS.keys())
        return f"未知操作: {action_name}。可用操作: {available}"

    if action.requires_confirmation and not confirmed:
        params_desc = ", ".join(f"{k}={v}" for k, v in params.items()) if params else "无参数"
        return (
            f"⚠️ **确认操作**：「{action.name}」\n"
            f"参数: {params_desc}\n\n"
            f"此操作不可撤销。回复「确认」继续执行，或回复其他内容取消。"
        )

    try:
        if action.handler:
            result = action.handler(**params)
            return result
        return f"操作 {action.name} 暂未实现直接调用。"
    except Exception as e:
        logger.error(f"Action '{action_name}' failed: {e}", exc_info=True)
        return f"❌ 操作执行失败: {e}"
