"""Escalation decision logic - determine what to escalate and when."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog

from sentinel.analysis.anomaly import Anomaly, AnomalySeverity
from sentinel.config import Config
from sentinel.storage import MetricsDB

logger = structlog.get_logger()


@dataclass
class EscalationResult:
    """Result of an escalation (AI explanation)."""

    anomaly: Anomaly
    explanation: str
    recommendations: list[str]
    should_notify: bool


class EscalationDecision:
    """Decide whether anomalies should be escalated to AI."""

    def __init__(self, config: Config, db: MetricsDB):
        self.config = config
        self.db = db
        self.cooldown_seconds = config.sentinel.escalation_cooldown

    def should_escalate(self, anomaly: Anomaly) -> bool:
        """Determine if an anomaly should be escalated to AI analysis."""
        # Check severity - always escalate critical
        if anomaly.severity == AnomalySeverity.CRITICAL:
            return self._check_cooldown(anomaly)

        # For high severity, check cooldown
        if anomaly.severity == AnomalySeverity.HIGH:
            return self._check_cooldown(anomaly)

        # Medium severity - only if no recent escalation
        if anomaly.severity == AnomalySeverity.MEDIUM:
            return self._check_cooldown(anomaly, multiplier=2)

        # Low severity - very conservative
        if anomaly.severity == AnomalySeverity.LOW:
            return self._check_cooldown(anomaly, multiplier=5)

        return False

    def _check_cooldown(self, anomaly: Anomaly, multiplier: float = 1.0) -> bool:
        """Check if we're in cooldown period for this issue."""
        last_escalation = self.db.get_last_escalation(anomaly.issue_key)

        if last_escalation is None:
            return True

        cooldown = timedelta(seconds=self.cooldown_seconds * multiplier)
        now = datetime.now(timezone.utc)

        if now - last_escalation > cooldown:
            return True

        logger.debug(
            "escalation_in_cooldown",
            issue_key=anomaly.issue_key,
            last_escalation=last_escalation.isoformat(),
            cooldown_remaining=(last_escalation + cooldown - now).seconds,
        )
        return False

    def filter_anomalies(self, anomalies: list[Anomaly]) -> list[Anomaly]:
        """Filter anomalies to only those that should be escalated."""
        escalatable = []

        for anomaly in anomalies:
            # Check suppression rules
            if self._is_suppressed(anomaly):
                logger.debug(
                    "anomaly_suppressed",
                    container=anomaly.container_name,
                    metric=anomaly.metric_name,
                )
                continue

            if self.should_escalate(anomaly):
                escalatable.append(anomaly)

        return escalatable

    def _is_suppressed(self, anomaly: Anomaly) -> bool:
        """Check if anomaly matches any suppression rules."""
        for rule in self.config.analysis.suppress_rules:
            # Check pattern match in container name or logs
            pattern = rule.pattern.lower()

            if pattern in anomaly.container_name.lower():
                return True

            # Check in recent logs
            for log_line in anomaly.recent_logs:
                if pattern in log_line.lower():
                    return True

        return False

    def record_escalation(self, anomaly: Anomaly, explanation: str) -> None:
        """Record that we escalated this anomaly."""
        self.db.record_escalation(
            issue_key=anomaly.issue_key,
            container_name=anomaly.container_name,
            details=explanation[:500],  # Store first 500 chars
        )
