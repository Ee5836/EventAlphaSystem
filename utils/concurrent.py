"""ThreadPool-based concurrent execution with Flask app context.

Provides run_concurrently() — the central utility for parallelizing
LLM API calls across independent work items. Each worker thread
automatically gets its own Flask app context and DB session.

Design decisions:
- ThreadPoolExecutor (not asyncio / ProcessPool) — bottleneck is I/O (API calls)
- Per-thread DB session via Flask-SQLAlchemy scoped_session
- Per-item error isolation — one failing item does not abort others
- Sequential fallback when max_workers <= 1 or items <= 1
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

logger = logging.getLogger("utils.concurrent")


def run_concurrently(
    items: list[dict],
    worker_fn: Callable[[dict], Optional[dict]],
    max_workers: int = 5,
    description: str = "",
) -> tuple[list[dict], list[dict]]:
    """Execute worker_fn on each item concurrently using a thread pool.

    Each worker thread automatically gets:
      - app.app_context() pushed (safe for current_app, db.session, etc.)
      - its own db.session (Flask-SQLAlchemy thread-local scoped_session)
      - the item dict as sole argument

    Args:
        items: List of serialized work-item dicts.
        worker_fn: Callable(item) -> dict | None.
                   Return a dict on success, None to skip, or raise on error.
        max_workers: Max concurrent threads (default 5).
                     When <= 1 or len(items) <= 1, falls back to sequential.
        description: Optional label for log messages.

    Returns:
        (successes, failures) tuple.
        - successes: list of non-None return values from worker_fn
        - failures: list of {"item": item, "error": str}
    """
    if not items:
        return [], []

    label = f" [{description}]" if description else ""

    # ── Sequential fallback (backward compatible / deterministic) ──
    if max_workers <= 1 or len(items) <= 1:
        logger.info(f"Running{label} sequentially ({len(items)} items, max_workers={max_workers})")
        successes = []
        failures = []
        for item in items:
            try:
                result = worker_fn(item)
                if result is not None:
                    successes.append(result)
            except Exception as e:
                logger.error(f"Worker failed{label}: {e}")
                failures.append({"item": item, "error": str(e)})
        return successes, failures

    # ── Concurrent execution ──────────────────────────────────────
    from flask import current_app

    app = current_app._get_current_object()
    actual_workers = min(max_workers, len(items))
    logger.info(
        f"Running{label} concurrently ({len(items)} items, "
        f"{actual_workers} workers)"
    )

    def _worker_wrapper(app_obj, fn, item):
        """Push app context in this thread, then call worker_fn."""
        with app_obj.app_context():
            return fn(item)

    successes = []
    failures = []
    with ThreadPoolExecutor(max_workers=actual_workers) as executor:
        future_to_item = {
            executor.submit(_worker_wrapper, app, worker_fn, item): item
            for item in items
        }

        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                result = future.result()
                if result is not None:
                    successes.append(result)
            except Exception as e:
                logger.error(f"Worker failed{label} for item: {e}")
                failures.append({"item": item, "error": str(e)})

    logger.info(
        f"Completed{label}: {len(successes)} successes, "
        f"{len(failures)} failures"
    )
    return successes, failures
