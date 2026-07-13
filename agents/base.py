"""Base Agent class and AgentResult dataclass."""
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    """Standard envelope returned by every agent.run()."""
    success: bool
    output: Any = None
    errors: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class BaseAgent(ABC):
    """Every agent inherits from this base class."""

    name: str = "base"

    def __init__(self, config: dict = None):
        if config is None:
            try:
                from flask import current_app
                config = current_app.config
            except RuntimeError:
                # Fallback when outside Flask app context (Celery worker, script, tests)
                config = {}
                # Load minimal config from environment for LLM access
                for key in ["LLM_PROVIDER", "LLM_API_KEY", "LLM_API_BASE", "LLM_MODEL",
                           "LLM_TEMPERATURE", "LLM_MAX_TOKENS", "LLM_MAX_CONCURRENCY",
                           "LLM_RATE_LIMIT_RPM", "LLM_RETRY_COUNT", "LLM_RETRY_BACKOFF_BASE",
                           "LLM_TIMEOUT", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL",
                           "NEWS_SOURCES"]:
                    val = os.getenv(key)
                    if val is not None:
                        config[key] = val
        self.config = config
        self.logger = logging.getLogger(f"agent.{self.name}")

    @abstractmethod
    def run(self, **kwargs) -> AgentResult:
        """Execute the agent's task. kwargs are agent-specific inputs."""
        ...

    def _get_llm(self):
        """Get LLM provider instance."""
        from llm.factory import get_llm
        return get_llm(self.config)
