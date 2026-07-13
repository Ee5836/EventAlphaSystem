"""AI Assistant web view and API routes."""
from flask import Blueprint, render_template, request, jsonify, Response

from assistant.chat_manager import ChatManager
from models.chat import ChatSession

# ── Web blueprint ────────────────────────────────────────────────────
web_bp = Blueprint("web_assistant", __name__)
# ── API blueprint ────────────────────────────────────────────────────
api_bp = Blueprint("api_assistant", __name__, url_prefix="/api/v1/assistant")

chat_manager = ChatManager()


# ── Web routes ───────────────────────────────────────────────────────
@web_bp.route("/assistant")
def assistant_page():
    """Render the standalone AI assistant page with full-size window."""
    return render_template("assistant.html")


# ── API routes ───────────────────────────────────────────────────────
@api_bp.route("/sessions", methods=["GET"])
def list_sessions():
    """List chat sessions with optional pagination."""
    offset = request.args.get("offset", 0, type=int)
    limit = request.args.get("limit", 50, type=int)
    sessions, total = chat_manager.list_sessions(offset=offset, limit=limit)
    return jsonify({
        "success": True,
        "data": [s.to_dict() for s in sessions],
        "total": total,
        "offset": offset,
        "limit": limit,
    })


@api_bp.route("/sessions", methods=["POST"])
def create_session():
    """Create a new chat session."""
    data = request.get_json(silent=True) or {}
    title = data.get("title", "新对话")
    session = chat_manager.create_session(title=title)
    return jsonify({
        "success": True,
        "data": session.to_dict(),
    }), 201


@api_bp.route("/sessions", methods=["DELETE"])
def delete_all_sessions():
    """Delete ALL chat sessions — clear all conversation history."""
    count = chat_manager.delete_all_sessions()
    return jsonify({
        "success": True,
        "data": {"deleted_count": count},
    })


@api_bp.route("/sessions/<string:session_id>", methods=["DELETE"])
def delete_session(session_id: str):
    """Delete a chat session."""
    ok = chat_manager.delete_session(session_id)
    if not ok:
        return jsonify({"success": False, "error": "Session not found"}), 404
    return jsonify({"success": True})


@api_bp.route("/sessions/<string:session_id>", methods=["GET"])
def get_session(session_id: str):
    """Get session details with full message history."""
    session = chat_manager.get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404

    messages = chat_manager.get_full_history(session_id)
    return jsonify({
        "success": True,
        "data": {
            "id": session.id,
            "title": session.title,
            "messages": messages,
        },
    })


@api_bp.route("/sessions/<string:session_id>/rename", methods=["POST"])
def rename_session(session_id: str):
    """Rename a chat session."""
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"success": False, "error": "title is required"}), 400

    ok = chat_manager.rename_session(session_id, title)
    if not ok:
        return jsonify({"success": False, "error": "Session not found"}), 404
    return jsonify({"success": True})


@api_bp.route("/sessions/<string:session_id>/messages", methods=["POST"])
def send_message(session_id: str):
    """Send a message and get assistant response."""
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()

    if not content:
        return jsonify({"success": False, "error": "content is required"}), 400

    session = chat_manager.get_session(session_id)
    if not session:
        return jsonify({"success": False, "error": "Session not found"}), 404

    # Extract optional parameters
    focus_industries = data.get("focus_industries", [])
    use_stream = data.get("stream", False)

    if use_stream:
        return _stream_response(session_id, content, focus_industries)

    # Non-streaming response
    try:
        from agents.research_assistant import ResearchAssistantAgent
        agent = ResearchAssistantAgent()
        result = agent.process_message(session_id, content, focus_industries=focus_industries)

        return jsonify({
            "success": True,
            "data": {
                "response": result["response"],
                "reasoning_chain": result["reasoning_chain"],
                "tool_calls": result["tool_calls"],
                "sources": result["sources"],
            },
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Assistant error: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/sessions/<string:session_id>/messages/<string:message_id>", methods=["DELETE"])
def delete_message(session_id: str, message_id: str):
    """Delete a single message (and its paired assistant response if user msg)."""
    result = chat_manager.delete_message(session_id, message_id)
    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 404
    return jsonify({
        "success": True,
        "data": {"deleted_ids": result["deleted_ids"]},
    })


def _stream_response(session_id: str, content: str, focus_industries: list = None):
    """Generate SSE streaming response."""
    def generate():
        try:
            from agents.research_assistant import ResearchAssistantAgent
            agent = ResearchAssistantAgent()
            result = agent.process_message(session_id, content, focus_industries=focus_industries or [])

            # Stream the response in chunks for a typewriter effect
            response_text = result["response"]
            chunk_size = 10
            for i in range(0, len(response_text), chunk_size):
                chunk = response_text[i:i + chunk_size]
                yield f"data: {chunk.replace(chr(10), chr(10) + 'data: ')}\n\n"

            # Send metadata at the end
            import json
            meta = {
                "reasoning_chain": result["reasoning_chain"],
                "tool_calls": result["tool_calls"],
                "sources": result["sources"],
            }
            yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Stream error: {e}")
            yield f"data: [错误: {e}]\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@api_bp.route("/quick-search", methods=["POST"])
def quick_search():
    """Quick search without session context."""
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()

    if not query:
        return jsonify({"success": False, "error": "query is required"}), 400

    try:
        from assistant.tools.web_search import web_search
        results = web_search(query, max_results=5)
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/crawl", methods=["POST"])
def crawl_page():
    """Crawl a specific URL."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"success": False, "error": "url is required"}), 400

    try:
        from assistant.tools.web_crawler import crawl_url
        result = crawl_url(url)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/industries", methods=["GET"])
def list_industries():
    """Return categorized industry list for the settings UI."""
    from app.utils.industry_stocks import INDUSTRY_STOCKS

    category_map = {
        "科技 / TMT": ["科技", "半导体", "芯片", "人工智能", "AI", "机器人", "电子制造",
                       "苹果供应链", "光电子", "光学", "消费电子", "软件", "云计算", "5G", "量子计算"],
        "汽车 / 新能源": ["汽车", "新能源汽车", "新能源", "自动驾驶", "锂电池",
                          "光伏", "储能", "充电桩"],
        "金融": ["金融", "银行", "银行业", "证券", "保险", "互联网金融", "金融科技",
                 "数字货币", "区块链", "债券", "资产管理", "私募股权"],
        "消费 / 医药": ["白酒", "食品饮料", "家电", "医药", "医疗器械", "创新药",
                        "游戏", "影视", "旅游"],
        "能源 / 资源": ["能源", "石油天然气", "煤炭", "电力", "核电", "水电", "风电"],
        "房地产 / 基建": ["房地产", "基建", "建材"],
        "交通运输": ["航空", "航运", "铁路", "物流"],
        "国防军工": ["军工", "航空航天"],
        "农业": ["农业", "养殖", "种业"],
        "化工 / 材料": ["化工", "稀土", "钢铁", "有色金属"],
        "通信 / 传媒": ["通信", "传媒", "广告"],
        "其他": ["环保", "教育", "零售", "网络安全", "存储", "直播", "电商",
                 "纺织服装", "造纸", "定制家居", "液压机制造", "审计服务业"],
    }

    categories = {}
    for cat_name, keywords in category_map.items():
        industries = [kw for kw in keywords if kw in INDUSTRY_STOCKS]
        if industries:
            categories[cat_name] = industries

    return jsonify({
        "success": True,
        "data": {
            "categories": categories,
            "total": sum(len(v) for v in categories.values()),
        },
    })
