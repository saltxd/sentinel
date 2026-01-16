"""Baseline tracking for container metrics."""

import math
from dataclasses import dataclass

import structlog

from sentinel.storage import MetricsDB

logger = structlog.get_logger()

# Minimum stddev floor to prevent division by near-zero
# Without this, ultra-stable metrics trigger false positives
MIN_STDDEV = 0.01


@dataclass
class Baseline:
    """Statistical baseline for a metric."""

    container_name: str
    metric_name: str
    mean: float
    stddev: float
    sample_count: int
    min_value: float
    max_value: float

    @property
    def has_enough_samples(self) -> bool:
        """Check if we have enough samples for reliable detection."""
        return self.sample_count >= 10

    def z_score(self, value: float) -> float:
        """Calculate z-score (standard deviations from mean)."""
        # Use floor to prevent division by near-zero for ultra-stable metrics
        effective_stddev = max(self.stddev, MIN_STDDEV)
        return (value - self.mean) / effective_stddev


class BaselineTracker:
    """Track rolling baselines for container metrics."""

    def __init__(self, db: MetricsDB, window_hours: int = 24, min_samples: int = 10):
        self.db = db
        self.window_hours = window_hours
        self.min_samples = min_samples

    def get_baseline(self, container_name: str, metric_name: str) -> Baseline | None:
        """Calculate baseline for a container metric from stored data."""
        values = self.db.get_metrics_for_baseline(
            container_name=container_name,
            metric_name=metric_name,
            hours=self.window_hours,
        )

        if len(values) < self.min_samples:
            return None

        mean = sum(values) / len(values)

        # Calculate standard deviation
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        stddev = math.sqrt(variance)

        return Baseline(
            container_name=container_name,
            metric_name=metric_name,
            mean=mean,
            stddev=stddev,
            sample_count=len(values),
            min_value=min(values),
            max_value=max(values),
        )

    def check_anomaly(
        self,
        container_name: str,
        metric_name: str,
        current_value: float,
        threshold: float = 3.0,
    ) -> tuple[bool, Baseline | None, float]:
        """Check if current value is anomalous compared to baseline.

        Returns:
            Tuple of (is_anomaly, baseline, z_score)
        """
        baseline = self.get_baseline(container_name, metric_name)

        if baseline is None:
            # Not enough data for baseline
            return False, None, 0.0

        if not baseline.has_enough_samples:
            return False, baseline, 0.0

        z_score = baseline.z_score(current_value)

        # Consider it anomalous if z-score exceeds threshold (in either direction)
        is_anomaly = abs(z_score) > threshold

        if is_anomaly:
            logger.info(
                "anomaly_detected",
                container=container_name,
                metric=metric_name,
                value=current_value,
                mean=baseline.mean,
                stddev=baseline.stddev,
                z_score=z_score,
            )

        return is_anomaly, baseline, z_score
