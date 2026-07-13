"""Pipeline orchestrator — chains all agents in sequence."""
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agents.scout import ScoutAgent
from agents.extraction import ExtractionAgent
from agents.clustering import ClusteringAgent
from agents.verification import VerificationAgent
from agents.scoring import ScoringAgent
from agents.card_generation import CardGenerationAgent

logger = logging.getLogger("pipeline.orchestrator")


@dataclass
class PipelineResult:
    """Result of a full pipeline run."""
    success: bool
    stage_results: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class PipelineOrchestrator:
    """Coordinates agent execution in the proper sequence."""

    def __init__(self, config: dict = None):
        if config is None:
            from flask import current_app
            config = current_app.config
        self.config = config
        self.scout = ScoutAgent(config)
        self.extraction = ExtractionAgent(config)
        self.clustering = ClusteringAgent(config)
        self.verification = VerificationAgent(config)
        self.scoring = ScoringAgent(config)
        self.card_gen = CardGenerationAgent(config)

    def run_processing_only(self) -> PipelineResult:
        """Run processing stages only (skip scout/collection).

        Processes already-collected but unprocessed articles through
        extraction → clustering → verification → scoring → card generation.
        Does NOT collect new articles from sources.

        Returns:
            PipelineResult with processing metadata.
        """
        stage_results = {}
        errors = []
        stage_times = {}
        t0 = time.time()

        # Stage 1 (Skipped): Scout — no collection
        logger.info("Pipeline [process-only]: Stage 1 — Scout (skipped)")
        stage_results["scout"] = {"skipped": True}
        stage_times["scout"] = 0

        # Stage 2: Extract events from unprocessed articles
        logger.info("Pipeline [process-only]: Stage 2 — Extraction")
        t2 = time.time()
        extraction_result = self.extraction.run()
        stage_times["extraction"] = round(time.time() - t2, 1)
        stage_results["extraction"] = extraction_result
        logger.info(f"  Extraction done in {stage_times['extraction']}s")

        # Stage 3: Cluster events
        logger.info("Pipeline [process-only]: Stage 3 — Clustering")
        t3 = time.time()
        cluster_result = self.clustering.run()
        stage_times["clustering"] = round(time.time() - t3, 1)
        stage_results["clustering"] = cluster_result
        logger.info(f"  Clustering done in {stage_times['clustering']}s")

        # Stage 4: Verify credibility
        logger.info("Pipeline [process-only]: Stage 4 — Verification")
        t4 = time.time()
        verify_result = self.verification.run()
        stage_times["verification"] = round(time.time() - t4, 1)
        stage_results["verification"] = verify_result
        logger.info(f"  Verification done in {stage_times['verification']}s")

        # Stage 5: Score events
        logger.info("Pipeline [process-only]: Stage 5 — Scoring")
        t5 = time.time()
        score_result = self.scoring.run()
        stage_times["scoring"] = round(time.time() - t5, 1)
        stage_results["scoring"] = score_result
        logger.info(f"  Scoring done in {stage_times['scoring']}s")

        # Stage 6: Generate cards
        logger.info("Pipeline [process-only]: Stage 6 — Card Generation")
        t6 = time.time()
        card_result = self.card_gen.run()
        stage_times["card_generation"] = round(time.time() - t6, 1)
        stage_results["card_generation"] = card_result
        logger.info(f"  Card Generation done in {stage_times['card_generation']}s")

        total_events = len(card_result.output.get("cards", [])) if card_result.success else 0
        total_time = round(time.time() - t0, 1)
        has_errors = bool(errors)

        logger.info(f"Pipeline [process-only] complete: {total_events} event cards | "
                    f"total={total_time}s | stages={stage_times}")

        return PipelineResult(
            success=not has_errors and total_events >= 0,
            stage_results=stage_results,
            errors=errors,
            metadata={
                "articles_collected": 0,
                "events_extracted": extraction_result.metadata.get("events_extracted", 0),
                "clusters_found": cluster_result.metadata.get("clusters_found", 0),
                "events_verified": verify_result.metadata.get("total_verified", 0),
                "events_scored": score_result.metadata.get("total_scored", 0),
                "cards_generated": total_events,
                "total_time_s": total_time,
                "stage_times_s": stage_times,
            },
        )

    def run_full_pipeline(self, force_scout: bool = False,
                          fast_mode: bool = True) -> PipelineResult:
        """Run the complete event processing pipeline.

        Sequence: Scout → Extraction → Clustering → Verification → Scoring → Card Generation
        Optional: Timeline Build + Prediction Extension (skipped in fast_mode)

        Args:
            force_scout: If True, skip poll_interval check in scout stage.
            fast_mode: If True (default), skip expensive LLM timeline analysis.
        """
        stage_results = {}
        errors = []
        stage_times = {}
        t0 = time.time()

        # Stage 1: Collect articles
        logger.info("Pipeline: Stage 1 — Scout (collect articles)")
        t1 = time.time()
        scout_result = self.scout.run(force=force_scout)
        stage_times["scout"] = round(time.time() - t1, 1)
        stage_results["scout"] = scout_result
        logger.info(f"  Scout done in {stage_times['scout']}s")
        if not scout_result.success and scout_result.errors:
            errors.extend(scout_result.errors)

        # Stage 2: Extract events
        logger.info("Pipeline: Stage 2 — Extraction (extract events)")
        t2 = time.time()
        extraction_result = self.extraction.run()
        stage_times["extraction"] = round(time.time() - t2, 1)
        stage_results["extraction"] = extraction_result
        logger.info(f"  Extraction done in {stage_times['extraction']}s")
        if not extraction_result.success and extraction_result.errors:
            errors.extend(extraction_result.errors)

        # Stage 3: Cluster events
        logger.info("Pipeline: Stage 3 — Clustering (deduplicate)")
        t3 = time.time()
        cluster_result = self.clustering.run()
        stage_times["clustering"] = round(time.time() - t3, 1)
        stage_results["clustering"] = cluster_result
        logger.info(f"  Clustering done in {stage_times['clustering']}s")
        if not cluster_result.success and cluster_result.errors:
            errors.extend(cluster_result.errors)

        # Stage 4: Verify credibility
        logger.info("Pipeline: Stage 4 — Verification (credibility)")
        t4 = time.time()
        verify_result = self.verification.run()
        stage_times["verification"] = round(time.time() - t4, 1)
        stage_results["verification"] = verify_result
        logger.info(f"  Verification done in {stage_times['verification']}s")
        if not verify_result.success and verify_result.errors:
            errors.extend(verify_result.errors)

        # Stage 5: Score events
        logger.info("Pipeline: Stage 5 — Scoring (importance)")
        t5 = time.time()
        score_result = self.scoring.run()
        stage_times["scoring"] = round(time.time() - t5, 1)
        stage_results["scoring"] = score_result
        logger.info(f"  Scoring done in {stage_times['scoring']}s")
        if not score_result.success and score_result.errors:
            errors.extend(score_result.errors)

        # Stage 6: Generate cards
        logger.info("Pipeline: Stage 6 — Card Generation")
        t6 = time.time()
        card_result = self.card_gen.run()
        stage_times["card_generation"] = round(time.time() - t6, 1)
        stage_results["card_generation"] = card_result
        logger.info(f"  Card Generation done in {stage_times['card_generation']}s")
        if not card_result.success and card_result.errors:
            errors.extend(card_result.errors)

        total_events = len(card_result.output.get("cards", [])) if card_result.success else 0

        # Stage 7: Build timeline (auto from event cards) — skipped in fast_mode
        timeline_created = 0
        stage_results["timeline"] = {"nodes_created": 0}
        stage_times["timeline"] = 0

        if not fast_mode:
            logger.info("Pipeline: Stage 7 — Timeline Build")
            t7 = time.time()
            try:
                from agents.timeline_builder import TimelineBuilderAgent
                timeline_agent = TimelineBuilderAgent()
                timeline_created = timeline_agent.auto_build_from_events()
                stage_results["timeline"] = {"nodes_created": timeline_created}

                # Auto-extend predictions
                prediction_count = 0
                try:
                    from models.timeline import TimelineNode, CausalEdge
                    for ntype in ["root_event", "derived_event"]:
                        if prediction_count > 0:
                            break
                        candidate_nodes = (
                            TimelineNode.query
                            .filter_by(node_type=ntype)
                            .order_by(TimelineNode.confidence.desc())
                            .limit(3).all()
                        )
                        for node in candidate_nodes:
                            if prediction_count >= 5:
                                break
                            existing_preds = CausalEdge.query.filter_by(
                                source_node_id=node.id, created_by="llm"
                            ).filter(
                                CausalEdge.target_node.has(
                                    TimelineNode.node_type == "prediction")
                            ).count()
                            if existing_preds > 0:
                                continue
                            preds = timeline_agent.extend_predictions(node.id)
                            prediction_count += len(preds)
                    if prediction_count > 0:
                        logger.info(f"Auto-extended {prediction_count} prediction nodes")
                        stage_results["timeline"]["predictions_created"] = prediction_count
                except Exception as e:
                    logger.warning(f"Auto-prediction failed (non-blocking): {e}")
            except Exception as e:
                logger.warning(f"Timeline auto-build failed (non-blocking): {e}")

            stage_times["timeline"] = round(time.time() - t7, 1)
            logger.info(f"  Timeline done in {stage_times['timeline']}s")

            # Optional: cleanup very old expired nodes
            try:
                cleanup_result = timeline_agent.cleanup_expired_nodes(
                    older_than_days=90)
                if cleanup_result.get("deleted_nodes", 0) > 0:
                    logger.info(f"Timeline cleanup: removed "
                                f"{cleanup_result['deleted_nodes']} expired nodes")
            except Exception as e:
                logger.warning(f"Timeline cleanup failed (non-blocking): {e}")
        else:
            logger.info("Pipeline: Stage 7 — Timeline Build (skipped in fast_mode)")

        logger.info(f"Pipeline complete: {total_events} event cards generated | "
                    f"total={round(time.time()-t0, 1)}s | "
                    f"stages={stage_times}")

        total_time = round(time.time() - t0, 1)
        has_errors = bool(errors)
        return PipelineResult(
            success=not has_errors and total_events >= 0,
            stage_results=stage_results,
            errors=errors,
            metadata={
                "articles_collected": len((scout_result.output or {}).get("articles", [])),
                "events_extracted": extraction_result.metadata.get("events_extracted", 0),
                "clusters_found": cluster_result.metadata.get("clusters_found", 0),
                "events_verified": verify_result.metadata.get("total_verified", 0),
                "events_scored": score_result.metadata.get("total_scored", 0),
                "cards_generated": total_events,
                "timeline_nodes_created": timeline_created,
                "total_time_s": total_time,
                "stage_times_s": stage_times,
            },
        )
