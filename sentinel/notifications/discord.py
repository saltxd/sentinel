"""Discord webhook notifications."""

import asyncio
from datetime import datetime, timezone

import aiohttp
import structlog

from sentinel.analysis.anomaly import Anomaly, AnomalySeverity
from sentinel.config import DiscordConfig
from sentinel.escalation.decision import EscalationResult

logger = structlog.get_logger()

# Default timeout for Discord requests
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


# Severity to Discord embed color mapping
SEVERITY_COLORS = {
    AnomalySeverity.LOW: 0x3498DB,  # Blue
    AnomalySeverity.MEDIUM: 0xF39C12,  # Orange
    AnomalySeverity.HIGH: 0xE74C3C,  # Red
    AnomalySeverity.CRITICAL: 0x9B59B6,  # Purple
}

SEVERITY_EMOJI = {
    AnomalySeverity.LOW: "🔵",
    AnomalySeverity.MEDIUM: "🟠",
    AnomalySeverity.HIGH: "🔴",
    AnomalySeverity.CRITICAL: "🟣",
}


class DiscordNotifier:
    """Send notifications to Discord via webhook."""

    def __init__(self, config: DiscordConfig):
        self.config = config
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Initialize HTTP session."""
        self._session = aiohttp.ClientSession()

    async def stop(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def send_alert(self, result: EscalationResult, max_retries: int = 3) -> bool:
        """Send an alert to Discord with retry logic."""
        if not self.config.enabled:
            logger.debug("discord_notifications_disabled")
            return False

        if not self.config.webhook_url:
            logger.warning("discord_webhook_not_configured")
            return False

        if not self._session:
            await self.start()

        embed = self._build_embed(result)
        payload = {"embeds": [embed]}

        for attempt in range(max_retries):
            try:
                async with self._session.post(
                    self.config.webhook_url,
                    json=payload,
                    timeout=DEFAULT_TIMEOUT,
                ) as response:
                    if response.status == 204:
                        logger.info(
                            "discord_notification_sent",
                            container=result.anomaly.container_name,
                            severity=result.anomaly.severity.value,
                        )
                        return True
                    elif response.status == 429:
                        # Rate limited - wait and retry
                        retry_after = int(response.headers.get("Retry-After", 5))
                        logger.warning(
                            "discord_rate_limited",
                            retry_after=retry_after,
                            attempt=attempt + 1,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    else:
                        body = await response.text()
                        logger.error(
                            "discord_notification_failed",
                            status=response.status,
                            body=body[:200],
                            attempt=attempt + 1,
                        )
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                            continue
                        return False

            except asyncio.TimeoutError:
                logger.warning(
                    "discord_notification_timeout",
                    attempt=attempt + 1,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return False

            except Exception as e:
                logger.error(
                    "discord_notification_error",
                    error=str(e),
                    attempt=attempt + 1,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return False

        return False

    def _build_embed(self, result: EscalationResult) -> dict:
        """Build Discord embed for the alert."""
        anomaly = result.anomaly
        severity = anomaly.severity
        emoji = SEVERITY_EMOJI.get(severity, "⚪")
        color = SEVERITY_COLORS.get(severity, 0x95A5A6)

        # Title
        title = f"{emoji} {severity.value.upper()}: {anomaly.container_name}"

        # Description - the AI explanation (truncated if needed)
        description = result.explanation
        if len(description) > 2000:
            description = description[:1997] + "..."

        # Fields
        fields = [
            {
                "name": "📊 Metric",
                "value": anomaly.metric_name,
                "inline": True,
            },
            {
                "name": "📈 Current Value",
                "value": f"{anomaly.current_value:.2f}",
                "inline": True,
            },
            {
                "name": "📉 Baseline",
                "value": f"{anomaly.baseline_mean:.2f} ± {anomaly.baseline_stddev:.2f}",
                "inline": True,
            },
            {
                "name": "🎯 Z-Score",
                "value": f"{anomaly.z_score:.2f}",
                "inline": True,
            },
        ]

        # Add recommendations if present
        if result.recommendations:
            rec_text = "\n".join(f"• {r}" for r in result.recommendations[:3])
            fields.append({
                "name": "💡 Recommendations",
                "value": rec_text[:1024],
                "inline": False,
            })

        # Footer with container info
        footer_text = f"Container: {anomaly.container_id}"
        if anomaly.context.get("image"):
            footer_text += f" | Image: {anomaly.context['image']}"

        embed = {
            "title": title,
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {"text": footer_text},
            "timestamp": anomaly.timestamp.isoformat(),
        }

        return embed

    async def send_startup_message(self) -> bool:
        """Send a startup notification."""
        if not self.config.enabled or not self.config.webhook_url:
            return False

        if not self._session:
            await self.start()

        payload = {
            "embeds": [
                {
                    "title": "🛡️ Sentinel Started",
                    "description": "AI-powered monitoring agent is now active.",
                    "color": 0x2ECC71,  # Green
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ]
        }

        try:
            async with self._session.post(
                self.config.webhook_url,
                json=payload,
                timeout=DEFAULT_TIMEOUT,
            ) as response:
                return response.status == 204
        except Exception as e:
            logger.error("discord_startup_message_failed", error=str(e))
            return False

    async def send_test_message(self) -> bool:
        """Send a test message to verify webhook is working."""
        if not self._session:
            await self.start()

        payload = {
            "content": "🧪 Sentinel test message - webhook is working!",
        }

        try:
            async with self._session.post(
                self.config.webhook_url,
                json=payload,
                timeout=DEFAULT_TIMEOUT,
            ) as response:
                return response.status == 204
        except Exception as e:
            logger.error("discord_test_message_failed", error=str(e))
            return False
