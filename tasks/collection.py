"""Celery tasks for news collection and auto-processing."""
from celery import shared_task
import logging

logger = logging.getLogger("tasks.collection")


@shared_task
def collect_all_sources():
    """Scheduled task: fetch from all active news sources, then auto-process.

    If new articles were collected, immediately triggers the full processing
    pipeline so the event feed stays fresh without manual intervention.
    """
    from pipeline.orchestrator import PipelineOrchestrator
    orchestrator = PipelineOrchestrator()
    result = orchestrator.scout.run()
    article_count = len(result.output.get("articles", []))

    response = {
        "success": result.success,
        "articles": article_count,
        "source_counts": result.output.get("source_counts", {}),
        "skipped_sources": result.metadata.get("skipped_sources", 0),
        "errors": result.errors,
    }

    # ── Auto-chain: if new articles arrived, process them immediately ──
    if article_count > 0:
        logger.info(f"Auto-processing {article_count} new articles...")
        process_result = process_new_articles.apply()
        response["processing"] = process_result.get()
    else:
        response["processing"] = "skipped (no new articles)"

    return response


@shared_task
def collect_single_source(source_name: str):
    """Collect from a single specified source."""
    from pipeline.orchestrator import PipelineOrchestrator
    orchestrator = PipelineOrchestrator()
    result = orchestrator.scout.run(source_names=[source_name])
    return {
        "success": result.success,
        "source": source_name,
        "articles": len(result.output.get("articles", [])),
        "errors": result.errors,
    }


@shared_task
def process_new_articles():
    """Run the full event processing pipeline on unprocessed articles."""
    from pipeline.orchestrator import PipelineOrchestrator
    orchestrator = PipelineOrchestrator()
    result = orchestrator.run_full_pipeline(fast_mode=True)
    return {
        "success": result.success,
        "metadata": result.metadata,
        "errors": result.errors,
    }
