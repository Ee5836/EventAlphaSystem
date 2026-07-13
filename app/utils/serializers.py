"""Shared serialization utilities for API responses.

Provides:
  - paginated_response(): build standard paginated JSON envelope
  - serialize_list(): apply a serializer to a list of items
  - to_iso(): safely convert datetime/date to ISO 8601 string
"""

from datetime import date, datetime
from typing import Any, Callable, Optional


def to_iso(value: Optional[Any]) -> Optional[str]:
    """Convert a datetime or date to ISO 8601 string, or None."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def serialize_list(items: list, serializer: Callable = None) -> list:
    """Apply serializer to each item; defaults to item.to_dict()."""
    if serializer is None:
        return [item.to_dict() for item in items]
    return [serializer(item) for item in items]


def paginated_response(
    query,
    page: int = 1,
    per_page: int = 20,
    serializer: Callable = None,
    **extra,
) -> dict:
    """Build a standard paginated JSON response envelope.

    Args:
        query: SQLAlchemy query object.
        page: 1-indexed page number.
        per_page: Items per page (default 20).
        serializer: Callable(item) -> dict. Defaults to item.to_dict().
        **extra: Additional keys to merge into the response.

    Returns:
        dict: {"success": True, "data": [...], "pagination": {...}, **extra}
    """
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    if serializer is None:
        data = [item.to_dict() for item in pagination.items]
    else:
        data = [serializer(item) for item in pagination.items]

    result = {
        "success": True,
        "data": data,
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
    }
    result.update(extra)
    return result
