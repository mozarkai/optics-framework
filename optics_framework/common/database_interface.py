from abc import ABC, abstractmethod
from typing import Any


class DatabaseInterface(ABC):
    """A database a test reads to check what the application stored."""

    @abstractmethod
    def query(self, statement: str) -> list[dict[str, Any]]:
        """Run a statement that reads and return its rows, each as column name to value.

        The statement is in the engine's own query language and must leave the data unchanged.
        """

    @abstractmethod
    def close(self) -> None:
        """Release every connection."""
