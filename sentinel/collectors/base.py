"""Base collector interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ContainerMetrics:
    """Metrics for a single container."""

    container_id: str
    container_name: str
    image: str
    status: str
    cpu_percent: float
    memory_bytes: int
    memory_limit: int
    memory_percent: float
    restart_count: int
    uptime_seconds: float
    labels: dict[str, str] = field(default_factory=dict)
    recent_logs: list[str] = field(default_factory=list)


@dataclass
class CollectorResult:
    """Result from a collection cycle."""

    timestamp: datetime
    source: str
    containers: list[ContainerMetrics]
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls, source: str) -> "CollectorResult":
        return cls(
            timestamp=datetime.now(timezone.utc),
            source=source,
            containers=[],
        )


class BaseCollector(ABC):
    """Abstract base class for metric collectors."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Collector name for logging and identification."""
        ...

    @abstractmethod
    async def collect(self) -> CollectorResult:
        """Collect metrics from the source."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the collector can connect to its source."""
        ...

    async def start(self) -> None:
        """Optional startup hook."""
        pass

    async def stop(self) -> None:
        """Optional cleanup hook."""
        pass
