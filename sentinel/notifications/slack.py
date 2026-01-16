"""Slack webhook notifications - placeholder for future implementation."""

import structlog

from sentinel.config import SlackConfig
from sentinel.escalation.decision import EscalationResult

logger = structlog.get_logger()


class SlackNotifier:
    """Send notifications to Slack via webhook. (Future implementation)"""

    def __init__(self, config: SlackConfig):
        self.config = config

    async def start(self) -> None:
        """Initialize HTTP session."""
        pass

    async def stop(self) -> None:
        """Close HTTP session."""
        pass

    async def send_alert(self, result: EscalationResult) -> bool:
        """Send an alert to Slack."""
        # TODO: Implement Slack notifications
        logger.warning("slack_notifications_not_implemented")
        return False
