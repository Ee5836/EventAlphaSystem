"""Celery tasks for event processing.

Note: `tasks.collection.process_new_articles` exists as a separate task with
`fast_mode=True` — it is called by the auto-chain inside `collect_all_sources`.
This module's `process_new_articles` runs a full pipeline (no fast_mode) and is
used by the 4-hour safety-net schedule and the daily briefing pipeline.
"""
from celery import shared_task

# Imported for process_full_pipeline — this task lives in tasks.collection
from tasks.collection import collect_all_sources


@shared_task
def process_new_articles():
    """Chained task: run the full event processing pipeline (full mode, no fast_mode).

    Used as a 4-hour safety-net and by the daily briefing pipeline.
    For the auto-chain version with fast_mode, see tasks.collection.process_new_articles.
    """
    from pipeline.orchestrator import PipelineOrchestrator
    orchestrator = PipelineOrchestrator()
    result = orchestrator.run_full_pipeline()
    return {
        "success": result.success,
        "metadata": result.metadata,
        "errors": result.errors,
    }


@shared_task
def process_full_pipeline():
    """Scheduled task: collect + process all in one go (for daily briefing)."""
    collect_result = collect_all_sources.apply()
    process_result = process_new_articles.apply()
    return {
        "collection": collect_result.get(),
        "processing": process_result.get(),
    }
