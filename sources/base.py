"""Abstract source connector interface."""
from abc import ABC, abstractmethod


class AbstractSourceConnector(ABC):
    """Base class for all news source connectors."""
    name: str = "base"

    def __init__(self, config: dict = None):
        self.config = config or {}

    @abstractmethod
    def fetch(self) -> list[dict]:
        """Fetch articles from this source.

        Returns:
            List of article dicts with keys:
            - title: str
            - url: str (unique identifier)
            - content: str
            - summary: str (optional)
            - published_at: datetime (optional)
            - metadata: dict (optional, author, tags, etc.)
        """
        ...
