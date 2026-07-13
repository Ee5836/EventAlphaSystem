import os
from dotenv import load_dotenv
from celery.schedules import crontab

load_dotenv()

# Force offline mode for HuggingFace Hub — model cached locally.
# Prevents SSL errors + 5× retries when huggingface.co is unreachable.
# Set HF_HUB_OFFLINE=0 in .env to re-enable online access.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


class Config:
    """Base configuration."""
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-prod")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///bubbleevent.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Celery
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
    CELERY_BEAT_SCHEDULE = {
        # ── Collect + auto-process (every hour) ─────────────────────
        # If new articles are found, processing kicks in immediately.
        # Otherwise skips to avoid wasted LLM calls.
        "collect-and-process-hourly": {
            "task": "tasks.collection.collect_all_sources",
            "schedule": 3600.0,
        },
        # ── Safety-net processing (every 4 hours at :45) ────────────
        # Catches any articles that were missed by the auto-chain
        # (e.g. server restart mid-pipeline, DB errors, etc.)
        "process-safety-net": {
            "task": "tasks.processing.process_new_articles",
            "schedule": crontab(minute=45, hour="*/4"),
        },
        # ── Daily briefing (08:30 CST = 00:30 UTC) ──────────────────
        "daily-briefing-morning": {
            "task": "tasks.processing.process_full_pipeline",
            "schedule": crontab(hour=0, minute=30),
        },
    }

    # LLM
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_API_BASE = os.getenv("LLM_API_BASE", "https://api.deepseek.com/v1")
    LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
    LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))

    # LLM rate limiting (requests per minute)
    LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "8"))
    LLM_RATE_LIMIT_RPM = int(os.getenv("LLM_RATE_LIMIT_RPM", "120"))
    LLM_RETRY_COUNT = int(os.getenv("LLM_RETRY_COUNT", "3"))
    LLM_RETRY_BACKOFF_BASE = float(os.getenv("LLM_RETRY_BACKOFF_BASE", "1.5"))
    LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "30.0"))

    # Embedding
    EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

    # Sources
    NEWS_SOURCES = os.getenv("NEWS_SOURCES", "cls,ak_cctv,ak_futures")

    # Pagination
    EVENTS_PER_PAGE = int(os.getenv("EVENTS_PER_PAGE", "20"))

    # AI Assistant widget (set False to temporarily disable)
    ENABLE_AI_ASSISTANT = os.getenv("ENABLE_AI_ASSISTANT", "true").lower() == "true"

    # Floating assistant widget (FAB + draggable window). Set False to hide the
    # widget while keeping homepage inline chat fully functional.
    ENABLE_ASSISTANT_WIDGET = os.getenv("ENABLE_ASSISTANT_WIDGET", "true").lower() == "true"

    # Flask
    FLASK_ENV = os.getenv("FLASK_ENV", "development")

    # Logging
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] %(levelname)-7s %(name)-30s %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "brief": {
                "format": "%(levelname)-7s %(name)s — %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "brief",
                "level": "INFO",
            },
        },
        "loggers": {
            # Application loggers
            "agent": {"level": "INFO", "handlers": ["console"], "propagate": False},
            "source": {"level": "INFO", "handlers": ["console"], "propagate": False},
            "pipeline": {"level": "INFO", "handlers": ["console"], "propagate": False},
            "llm": {"level": "INFO", "handlers": ["console"], "propagate": False},
            "assistant": {"level": "INFO", "handlers": ["console"], "propagate": False},
            "utils": {"level": "INFO", "handlers": ["console"], "propagate": False},
            "services": {"level": "INFO", "handlers": ["console"], "propagate": False},
            # SQLAlchemy — only show warnings by default
            "sqlalchemy.engine": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        },
        "root": {
            "level": "INFO",
            "handlers": ["console"],
        },
    }


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_ECHO = False


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CELERY_BROKER_URL = "memory://"
    CELERY_RESULT_BACKEND = "cache+memory://"


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
