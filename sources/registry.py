"""Source connector registry.

Connectors auto-register via the @register_connector decorator.
Access via get_connector(name) or SOURCE_MAP dict.
"""
from sources.base import AbstractSourceConnector

# ── Decorator-based auto-registration ──────────────────────────────────
SOURCE_MAP: dict[str, type] = {}


def register_connector(name: str):
    """Class decorator to register a source connector."""
    def decorator(cls):
        SOURCE_MAP[name] = cls
        return cls
    return decorator


# ── Import connectors to trigger registration ─────────────────────────
# Each connector uses @register_connector("name") above its class.
from sources.cls import CLSConnector  # noqa: E402,F401
from sources.akshare import AkshareConnector  # noqa: E402,F401
from sources.smart_crawler import SmartCrawlerConnector  # noqa: E402,F401


def get_connector(name: str, config: dict = None, source_record=None) -> AbstractSourceConnector:
    """Get a source connector by name.

    Args:
        name: Connector name (e.g., "cls", "ak_cctv", "ak_futures")
        config: Application config dict
        source_record: Optional NewsSource ORM object for connectors that need it

    Returns:
        Configured connector instance, or None if name not found.
    """
    connector_class = SOURCE_MAP.get(name.lower())
    if connector_class is None:
        return None
    import inspect
    sig = inspect.signature(connector_class.__init__)
    if 'source_record' in sig.parameters:
        return connector_class(source_record=source_record, config=config)
    return connector_class(config=config)


def list_connectors() -> list[str]:
    """List all available connector names."""
    return list(SOURCE_MAP.keys())
