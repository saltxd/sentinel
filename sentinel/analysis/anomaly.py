"""Anomaly detection and scoring."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from sentinel.collectors.base import CollectorResult, ContainerMetrics
from sentinel.config import AnalysisConfig
from sentinel.storage import MetricsDB

from .baseline import Baseline, BaselineTracker

logger = structlog.get_logger()


class AnomalySeverity(Enum):
    """Severity levels for anomalies."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Anomaly:
    """Detected anomaly with context."""

    timestamp: datetime
    container_name: str
    container_id: str
    metric_name: str
    current_value: float
    baseline_mean: float
    baseline_stddev: float
    z_score: float
    severity: AnomalySeverity
    recent_logs: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def issue_key(self) -> str:
        """Unique key for this type of issue (for cooldown tracking)."""
        return f"{self.container_name}:{self.metric_name}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "container_name": self.container_name,
            "container_id": self.container_id,
            "metric_name": self.metric_name,
            "current_value": self.current_value,
            "baseline_mean": self.baseline_mean,
            "baseline_stddev": self.baseline_stddev,
            "z_score": self.z_score,
            "severity": self.severity.value,
            "recent_logs": self.recent_logs[-20:],  # Last 20 lines
            "context": self.context,
        }


class AnomalyDetector:
    """Detect anomalies in container metrics."""

    # Metrics to monitor
    MONITORED_METRICS = [
        "cpu_percent",
        "memory_percent",
        "restart_count",
    ]

    def __init__(self, config: AnalysisConfig, db: MetricsDB):
        self.config = config
        self.db = db
        self.baseline_tracker = BaselineTracker(
            db=db,
            window_hours=config.baseline_window_hours,
            min_samples=config.min_samples_for_baseline,
        )

    def analyze(self, result: CollectorResult) -> list[Anomaly]:
        """Analyze collection result for anomalies."""
        anomalies: list[Anomaly] = []

        for container in result.containers:
            container_anomalies = self._analyze_container(container, result.timestamp)
            anomalies.extend(container_anomalies)

        if anomalies:
            logger.info(
                "anomalies_found",
                count=len(anomalies),
                containers=[a.container_name for a in anomalies],
            )

        return anomalies

    def _analyze_container(
        self, container: ContainerMetrics, timestamp: datetime
    ) -> list[Anomaly]:
        """Analyze a single container for anomalies."""
        anomalies: list[Anomaly] = []

        # Skip self-monitoring (sentinel alerting on itself creates feedback loops)
        if self.config.exclude_self_monitoring and "sentinel" in container.container_name.lower():
            logger.debug(
                "skipping_self_monitoring",
                container=container.container_name,
            )
            return anomalies

        # Skip anomaly detection for recently-started containers
        # Startup behavior (higher CPU/memory) is expected and would cause false positives
        if container.uptime_seconds < self.config.startup_grace_seconds:
            logger.debug(
                "skipping_startup_container",
                container=container.container_name,
                uptime=container.uptime_seconds,
                grace_period=self.config.startup_grace_seconds,
            )
            return anomalies

        metrics_to_check = {
            "cpu_percent": container.cpu_percent,
            "memory_percent": container.memory_percent,
            "restart_count": float(container.restart_count),
        }

        for metric_name, current_value in metrics_to_check.items():
            is_anomaly, baseline, z_score = self.baseline_tracker.check_anomaly(
                container_name=container.container_name,
                metric_name=metric_name,
                current_value=current_value,
                threshold=self.config.anomaly_threshold,
            )

            if is_anomaly and baseline:
                # Skip low-value alerts (0% CPU/memory is fine, not a problem)
                if not self.config.alert_on_low_values and z_score < 0:
                    logger.debug(
                        "skipping_low_value_anomaly",
                        container=container.container_name,
                        metric=metric_name,
                        value=current_value,
                        z_score=z_score,
                    )
                    continue

                # Check absolute thresholds - only alert if values are actually problematic
                if metric_name == "cpu_percent" and current_value < self.config.min_cpu_percent:
                    logger.debug(
                        "skipping_below_threshold",
                        container=container.container_name,
                        metric=metric_name,
                        value=current_value,
                        threshold=self.config.min_cpu_percent,
                    )
                    continue
                if metric_name == "memory_percent" and current_value < self.config.min_memory_percent:
                    logger.debug(
                        "skipping_below_threshold",
                        container=container.container_name,
                        metric=metric_name,
                        value=current_value,
                        threshold=self.config.min_memory_percent,
                    )
                    continue

                severity = self._calculate_severity(metric_name, z_score, current_value)

                anomaly = Anomaly(
                    timestamp=timestamp,
                    container_name=container.container_name,
                    container_id=container.container_id,
                    metric_name=metric_name,
                    current_value=current_value,
                    baseline_mean=baseline.mean,
                    baseline_stddev=baseline.stddev,
                    z_score=z_score,
                    severity=severity,
                    recent_logs=container.recent_logs,
                    context={
                        "image": container.image,
                        "status": container.status,
                        "uptime_seconds": container.uptime_seconds,
                        "memory_bytes": container.memory_bytes,
                        "memory_limit": container.memory_limit,
                    },
                )
                anomalies.append(anomaly)

        # Also check for restart events (immediate detection, no baseline needed)
        restart_anomaly = self._check_restart_anomaly(container, timestamp)
        if restart_anomaly:
            anomalies.append(restart_anomaly)

        return anomalies

    def _calculate_severity(
        self, metric_name: str, z_score: float, value: float
    ) -> AnomalySeverity:
        """Calculate severity based on metric type and deviation."""
        abs_z = abs(z_score)

        # Critical conditions
        if metric_name == "memory_percent" and value > 95:
            return AnomalySeverity.CRITICAL
        if metric_name == "cpu_percent" and value > 95:
            return AnomalySeverity.CRITICAL

        # Score-based severity
        if abs_z > 5:
            return AnomalySeverity.CRITICAL
        elif abs_z > 4:
            return AnomalySeverity.HIGH
        elif abs_z > 3.5:
            return AnomalySeverity.MEDIUM
        else:
            return AnomalySeverity.LOW

    def _check_restart_anomaly(
        self, container: ContainerMetrics, timestamp: datetime
    ) -> Anomaly | None:
        """Check for unexpected container restart."""
        # Get previous restart count from DB
        # Note: current metrics are stored BEFORE analyze() is called,
        # so we need the second-to-last value (values[-2]) for comparison
        values = self.db.get_metrics_for_baseline(
            container_name=container.container_name,
            metric_name="restart_count",
            hours=1,  # Look at last hour
        )

        if len(values) < 2:
            return None

        # Get the previous value (before current was stored)
        prev_restart_count = values[-2]
        if container.restart_count > prev_restart_count:
            # Container restarted!
            return Anomaly(
                timestamp=timestamp,
                container_name=container.container_name,
                container_id=container.container_id,
                metric_name="container_restart",
                current_value=float(container.restart_count),
                baseline_mean=prev_restart_count,
                baseline_stddev=0,
                z_score=999.0,  # Always significant (finite for JSON serialization)
                severity=AnomalySeverity.HIGH,
                recent_logs=container.recent_logs,
                context={
                    "image": container.image,
                    "status": container.status,
                    "uptime_seconds": container.uptime_seconds,
                    "restart_delta": container.restart_count - int(prev_restart_count),
                },
            )

        return None

    async def store_metrics(self, result: CollectorResult) -> None:
        """Store collected metrics in database for baseline calculation."""
        if not result.containers:
            return

        metrics_batch: list[tuple[str, str, str, float, dict[str, Any] | None]] = []

        for container in result.containers:
            base_labels = {
                "image": container.image,
                "status": container.status,
            }

            metrics_batch.extend([
                (
                    container.container_id,
                    container.container_name,
                    "cpu_percent",
                    container.cpu_percent,
                    base_labels,
                ),
                (
                    container.container_id,
                    container.container_name,
                    "memory_percent",
                    container.memory_percent,
                    base_labels,
                ),
                (
                    container.container_id,
                    container.container_name,
                    "memory_bytes",
                    float(container.memory_bytes),
                    base_labels,
                ),
                (
                    container.container_id,
                    container.container_name,
                    "restart_count",
                    float(container.restart_count),
                    base_labels,
                ),
            ])

        await self.db.store_metrics_batch_async(metrics_batch, result.timestamp)

        logger.debug(
            "metrics_stored",
            containers=len(result.containers),
            metrics=len(metrics_batch),
        )
