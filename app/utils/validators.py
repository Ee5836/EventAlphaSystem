"""Lightweight request validation for Flask routes.

Provides decorator-based validation without heavy dependencies.

Usage:
    from app.utils.validators import validate_query, validate_body

    @bp.route("/events")
    @validate_query(page=int, per_page=int, level=str)
    def list_events(page=1, per_page=20, level=None):
        ...

    @bp.route("/sources", methods=["POST"])
    @validate_body(name=str, url=str, is_active=bool)
    def create_source(name, url, is_active=True):
        ...

Design decisions:
  - No marshmallow/pydantic — keep dependencies minimal per user preference
  - Decorators inject validated kwargs into the view function
  - Missing optional params get their default values (None / provided default)
  - Type conversion errors raise APIError(400)
"""

import functools
from typing import Any, Callable

from flask import request


def _coerce(value: Any, target_type: type, param_name: str):
    """Coerce a string value to `target_type`, or raise APIError on failure."""
    if value is None:
        return None
    if target_type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)
    if target_type is int:
        try:
            return int(value)
        except (ValueError, TypeError):
            from app.errors import APIError
            raise APIError(f"Parameter '{param_name}' must be an integer", 400)
    if target_type is float:
        try:
            return float(value)
        except (ValueError, TypeError):
            from app.errors import APIError
            raise APIError(f"Parameter '{param_name}' must be a number", 400)
    if target_type is str:
        return str(value).strip() if value else None
    return value


def validate_query(**param_types: type):
    """Decorator: extract & coerce query-string params, inject as kwargs.

    Each kwarg is `name=type`. The decorator reads `request.args.get(name)`,
    coerces to the declared type, and passes it as a keyword argument.

    Example:
        @validate_query(page=int, per_page=int, level=str)
        def list_events(page=1, per_page=20, level=None):
            ...
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for param_name, param_type in param_types.items():
                raw = request.args.get(param_name, None)
                if raw is not None:
                    kwargs[param_name] = _coerce(raw, param_type, param_name)
                # If no raw value, keep the function's default (already in kwargs from Flask)
                # or fall back to the annotation default
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def validate_body(**param_types: type):
    """Decorator: extract & coerce JSON body fields, inject as kwargs.

    Reads `request.get_json(silent=True)` and coerces each declared field.

    Example:
        @validate_body(name=str, url=str, is_active=bool)
        def create_source(name, url, is_active=True):
            ...
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            data = request.get_json(silent=True) or {}
            for param_name, param_type in param_types.items():
                raw = data.get(param_name, None)
                if raw is not None:
                    kwargs[param_name] = _coerce(raw, param_type, param_name)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
