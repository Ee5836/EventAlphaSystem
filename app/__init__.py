"""Flask application factory."""
from flask import Flask
from config import config


def create_app(config_name: str = None) -> Flask:
    """Create and configure the Flask application."""
    if config_name is None:
        import os
        config_name = os.getenv("FLASK_ENV", "default")

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config.get(config_name, config["default"]))

    # ── Centralized logging ──────────────────────────────────────────
    _init_logging(app)

    # ── Custom Jinja2 filters ────────────────────────────────────────
    _register_filters(app)

    # ── Extensions ───────────────────────────────────────────────────
    from app.extensions import db, migrate, celery, init_celery
    db.init_app(app)
    migrate.init_app(app, db)
    init_celery(app)

    # ── Error handlers ───────────────────────────────────────────────
    from app.errors import register_error_handlers
    register_error_handlers(app)

    # ── Blueprints ───────────────────────────────────────────────────
    _register_blueprints(app)

    # ── Create tables (SQLite dev convenience) ───────────────────────
    with app.app_context():
        from models import source, event, verification, scoring, card, chat, market, briefing, timeline  # noqa
        db.create_all()
        from utils.seed import seed_system_sources
        seed_system_sources()

    return app


# ── Private helpers ──────────────────────────────────────────────────

def _init_logging(app: Flask) -> None:
    """Apply centralized logging configuration."""
    import logging.config
    logging_cfg = app.config.get("LOGGING")
    if logging_cfg:
        logging.config.dictConfig(logging_cfg)


def _register_filters(app: Flask) -> None:
    """Register custom Jinja2 template filters."""

    @app.template_filter("markdown")
    def markdown_filter(text: str) -> str:
        """Convert LLM-output markdown to safe HTML.

        Handles: **bold**, *italic*, headings, double-newline paragraphs,
        single newlines as <br>.
        """
        import re
        if not text:
            return ""
        text = str(text)

        # Escape HTML entities first
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # Headings
        text = re.sub(r"(?m)^###\s+(.+?)$", r"<h5 class='briefing-h5'>\1</h5>", text)
        text = re.sub(r"(?m)^##\s+(.+?)$", r"<h4 class='briefing-h4'>\1</h4>", text)
        text = re.sub(r"(?m)^#\s+(.+?)$", r"<h3 class='briefing-h3'>\1</h3>", text)

        # Bold **text** — do before italic to avoid conflict
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        # Italic *text* (single * not part of **)
        text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)

        # Split into paragraphs by double (or more) newlines
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

        result_parts = []
        for para in paragraphs:
            # If the paragraph is already a block-level element, keep as-is
            if re.match(r"^<(h[345]|ul|ol|blockquote|div)", para):
                result_parts.append(para)
            elif re.match(r"^<li", para):
                result_parts.append(f"<ul class='briefing-list'>{para}</ul>")
            else:
                # Replace single newlines within paragraph with <br>
                para = para.replace("\n", "<br>")
                result_parts.append(f"<p>{para}</p>")

        # Collapse: if multiple <ul> blocks are adjacent, merge them
        html = "\n".join(result_parts)
        html = re.sub(r"</ul>\n<ul class='briefing-list'>", "", html)

        return html


def _register_blueprints(app: Flask) -> None:
    """Register all Flask blueprints."""
    from app.routes.web_health import bp as health_bp
    from app.routes.web_events import bp as events_bp
    from app.routes.web_sources import bp as sources_bp
    from app.routes.web_assistant import web_bp as assistant_web_bp
    from app.routes.web_assistant import api_bp as assistant_api_bp
    from app.routes.web_prediction import web_bp as prediction_web_bp
    from app.routes.web_prediction import api_bp as prediction_api_bp
    from app.routes.web_briefing import web_bp as briefing_web_bp
    from app.routes.web_briefing import api_bp as briefing_api_bp
    from app.routes.web_timeline import web_bp as timeline_web_bp
    from app.routes.web_timeline import api_bp as timeline_api_bp
    from app.routes.api_events import bp as api_events_bp
    from app.routes.api_sources import bp as api_sources_bp
    from app.routes.web_fragments import bp as fragments_bp

    blueprints = [
        health_bp, events_bp, sources_bp,
        assistant_web_bp, assistant_api_bp,
        prediction_web_bp, prediction_api_bp,
        briefing_web_bp, briefing_api_bp,
        timeline_web_bp, timeline_api_bp,
        api_events_bp, api_sources_bp,
        fragments_bp,
    ]
    for bp in blueprints:
        app.register_blueprint(bp)
