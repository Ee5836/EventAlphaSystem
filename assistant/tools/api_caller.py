"""Generic API caller tool for AI Assistant."""
import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def api_call(
    url: str,
    method: str = "GET",
    headers: Optional[dict] = None,
    body: Optional[dict] = None,
    timeout: int = 10,
) -> dict:
    """Make a generic HTTP API call.

    Args:
        url: API endpoint URL.
        method: HTTP method (GET/POST).
        headers: Optional request headers.
        body: Optional request body (for POST).
        timeout: Request timeout in seconds.

    Returns:
        Dict with: success, status_code, data (parsed JSON), error (if any).
    """
    result = {
        "success": False,
        "status_code": None,
        "data": None,
        "error": None,
    }

    default_headers = {
        "User-Agent": "BubbleEvent/1.0 Research Assistant",
        "Accept": "application/json",
    }
    merged_headers = {**default_headers, **(headers or {})}

    try:
        if method.upper() == "GET":
            resp = requests.get(url, headers=merged_headers, timeout=timeout)
        elif method.upper() == "POST":
            resp = requests.post(url, headers=merged_headers, json=body, timeout=timeout)
        else:
            result["error"] = f"Unsupported HTTP method: {method}"
            return result

        result["status_code"] = resp.status_code
        result["success"] = 200 <= resp.status_code < 300

        # Try JSON first, then text
        try:
            result["data"] = resp.json()
        except (json.JSONDecodeError, ValueError):
            result["data"] = resp.text[:5000]  # Truncate long responses

    except requests.Timeout:
        result["error"] = f"Request timed out after {timeout}s"
    except requests.ConnectionError:
        result["error"] = f"Connection failed to {url}"
    except Exception as e:
        result["error"] = str(e)

    return result
