"""Abstract LLM provider interface."""
from abc import ABC, abstractmethod
from typing import Any


class AbstractLLMProvider(ABC):
    """Base class for all LLM providers."""

    def __init__(self, config: dict):
        self.config = config
        self.model = config.get("LLM_MODEL", "deepseek-v4-flash")
        self.temperature = config.get("LLM_TEMPERATURE", 0.1)
        self.max_tokens = config.get("LLM_MAX_TOKENS", 4096)

    @abstractmethod
    def complete(self, system_prompt: str, user_message: str, **kwargs) -> str:
        """Send a single-turn completion and return the raw text response."""
        ...

    @abstractmethod
    def complete_json(self, system_prompt: str, user_message: str, **kwargs) -> dict:
        """Send a completion, parse JSON response, validate, return dict."""
        ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for a list of texts."""
        ...

    def chat(self, messages: list[dict], **kwargs) -> str:
        """Send a multi-turn chat completion."""
        ...

    def _local_embed(self, texts: list[str]) -> list[list[float]]:
        """Local embedding via sentence-transformers (fallback for all providers)."""
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)
            embeddings = model.encode(texts, normalize_embeddings=True)
            return embeddings.tolist()
        except Exception:
            import logging
            logging.getLogger("llm.base").warning(
                "Failed to get embeddings, returning zero vectors"
            )
            return [[0.0] * 768 for _ in texts]
