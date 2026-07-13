"""Centralized error handling for Flask application.

Provides:
  - APIError: custom exception class for controlled HTTP error responses
  - register_error_handlers(): Flask errorhandler registration

Usage in routes (gradual migration):
    from app.errors import APIError
    if not event: raise APIError("Event not found", 404)
    if bad_input: raise APIError("Missing required field", 400)
"""

import logging

from flask import Flask, jsonify, render_template, request

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Controlled API error — raised in route handlers, caught by errorhandler.

    All API responses use a consistent envelope:
        {"success": false, "error": "<message>", "code": "<machine_readable_code>"}
    """

    def __init__(self, message: str, status_code: int = 400, code: str = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code or self._default_code(status_code)

    @staticmethod
    def _default_code(status_code: int) -> str:
        mapping = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "unprocessable_entity",
            429: "rate_limited",
            500: "internal_error",
            503: "service_unavailable",
        }
        return mapping.get(status_code, "error")


def register_error_handlers(app: Flask) -> None:
    """Register Flask error handlers for consistent JSON error responses."""

    @app.errorhandler(APIError)
    def handle_api_error(e: APIError):
        logger.warning(f"API error {e.status_code}: {e.message}")
        return jsonify({
            "success": False,
            "error": e.message,
            "code": e.code,
        }), e.status_code

    @app.errorhandler(400)
    def handle_bad_request(e):
        msg = getattr(e, 'description', None) or str(e) or 'Bad request'
        if not request.path.startswith('/api/'):
            try:
                return render_template("error.html", code=400, message=msg), 400
            except Exception:
                pass
            return f"<h1>400 Bad Request</h1><p>{msg}</p>", 400
        return jsonify({
            "success": False,
            "error": msg,
            "code": "bad_request",
        }), 400

    @app.errorhandler(404)
    def handle_not_found(e):
        msg = getattr(e, 'description', None) or str(e) or 'Resource not found'
        if not request.path.startswith('/api/'):
            try:
                return render_template("error.html", code=404, message=msg), 404
            except Exception:
                pass
            return f"<h1>404 Not Found</h1><p>{msg}</p>", 404
        return jsonify({
            "success": False,
            "error": msg,
            "code": "not_found",
        }), 404

    @app.errorhandler(405)
    def handle_method_not_allowed(e):
        msg = getattr(e, 'description', None) or str(e) or 'Method not allowed'
        if not request.path.startswith('/api/'):
            try:
                return render_template("error.html", code=405, message=msg), 405
            except Exception:
                pass
            return f"<h1>405 Method Not Allowed</h1><p>{msg}</p>", 405
        return jsonify({
            "success": False,
            "error": msg,
            "code": "method_not_allowed",
        }), 405

    @app.errorhandler(500)
    def handle_internal_error(e):
        logger.exception("Unhandled internal error")
        # In production, don't leak exception details
        if app.config.get("DEBUG", False):
            msg = str(e)
        else:
            msg = "Internal server error"
        if not request.path.startswith('/api/'):
            try:
                return render_template("error.html", code=500, message=msg), 500
            except Exception:
                pass
            return f"<h1>500 Internal Server Error</h1><p>{msg}</p>", 500
        return jsonify({
            "success": False,
            "error": msg,
            "code": "internal_error",
        }), 500

    logger.info("Error handlers registered")
