"""Sentinel main entry point - monitoring loop and orchestration."""

import asyncio
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

from sentinel.analysis.anomaly import Anomaly, AnomalyDetector
from sentinel.collectors.docker_collector import DockerCollector
from sentinel.config import Config, load_config
from sentinel.escalation.claude_client import ClaudeClient
from sentinel.escalation.decision import EscalationDecision, EscalationResult
from sentinel.notifications.discord import DiscordNotifier
from sentinel.storage import MetricsDB

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()

# Watchdog file path for health checks
WATCHDOG_FILE = Path("/tmp/sentinel_heartbeat")

# Maximum time allowed for a check cycle before considering it hung
CHECK_CYCLE_TIMEOUT = 300  # 5 minutes


class Sentinel:
    """Main Sentinel monitoring agent."""

    def __init__(self, config: Config):
        self.config = config
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._last_heartbeat = datetime.now(timezone.utc)
        self._check_cycle_count = 0

        # Initialize components
        self.db = MetricsDB(
            db_path=config.storage.database_path,
            retention_hours=config.storage.retention_hours,
        )

        self.docker_collector = DockerCollector(config.collectors.docker)
        self.anomaly_detector = AnomalyDetector(config.analysis, self.db)
        self.escalation_decision = EscalationDecision(config, self.db)
        self.claude_client = ClaudeClient(config.escalation)
        self.discord = DiscordNotifier(config.notifications.discord)

    async def start(self) -> None:
        """Start the monitoring agent."""
        logger.info("sentinel_starting", version="0.1.0")

        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_shutdown)

        # Start components
        if self.config.collectors.docker.enabled:
            await self.docker_collector.start()

        await self.discord.start()

        # Send startup notification
        await self.discord.send_startup_message()

        # Check collector health
        if self.config.collectors.docker.enabled:
            healthy = await self.docker_collector.health_check()
            if not healthy:
                logger.error("docker_collector_unhealthy")
            else:
                logger.info("docker_collector_healthy")

        self._running = True
        logger.info(
            "sentinel_started",
            check_interval=self.config.sentinel.check_interval,
            baseline_window=self.config.analysis.baseline_window_hours,
        )

        # Start monitoring loop
        await self._monitoring_loop()

    async def stop(self) -> None:
        """Stop the monitoring agent."""
        logger.info("sentinel_stopping")
        self._running = False
        self._shutdown_event.set()

        # Stop all components, even if some fail
        results = await asyncio.gather(
            self.docker_collector.stop(),
            self.discord.stop(),
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                logger.error("component_stop_failed", error=str(result))

        logger.info("sentinel_stopped")

    def _handle_shutdown(self) -> None:
        """Handle shutdown signal."""
        logger.info("shutdown_signal_received")
        self._shutdown_event.set()

    async def _monitoring_loop(self) -> None:
        """Main monitoring loop with watchdog protection."""
        while self._running:
            try:
                # Run check cycle with timeout protection
                await asyncio.wait_for(
                    self._run_check_cycle(),
                    timeout=CHECK_CYCLE_TIMEOUT,
                )

                # Update heartbeat after successful cycle
                self._update_heartbeat()

            except asyncio.TimeoutError:
                # Check cycle took too long - log it but don't alert (not actionable)
                logger.warning(
                    "check_cycle_timeout",
                    timeout=CHECK_CYCLE_TIMEOUT,
                    cycle_count=self._check_cycle_count,
                    last_heartbeat_age=(datetime.now(timezone.utc) - self._last_heartbeat).total_seconds(),
                )
                # Update heartbeat so we don't fail health checks
                self._update_heartbeat()
                # Continue running - next cycle might succeed

            except Exception as e:
                logger.exception("check_cycle_error", error=str(e), cycle_count=self._check_cycle_count)
                # Update heartbeat even on errors so we don't fail health checks
                self._update_heartbeat()

            # Wait for next cycle or shutdown
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self.config.sentinel.check_interval,
                )
                # If we get here, shutdown was requested
                break
            except asyncio.TimeoutError:
                # Normal timeout, continue loop
                pass

        await self.stop()

    async def _run_check_cycle(self) -> None:
        """Run a single check cycle."""
        cycle_start = datetime.now(timezone.utc)
        self._check_cycle_count += 1

        logger.info(
            "check_cycle_started",
            cycle_number=self._check_cycle_count,
        )

        # Collect metrics
        if self.config.collectors.docker.enabled:
            logger.debug("collecting_docker_metrics")
            result = await self.docker_collector.collect()
            logger.debug("docker_collection_complete", container_count=len(result.containers))

            if result.errors:
                for error in result.errors:
                    logger.warning("collection_error", error=error)

            # Store metrics for baseline calculation
            logger.debug("storing_metrics")
            await self.anomaly_detector.store_metrics(result)

            # Detect anomalies
            logger.debug("analyzing_anomalies")
            anomalies = self.anomaly_detector.analyze(result)

            # Filter to escalatable anomalies
            to_escalate = self.escalation_decision.filter_anomalies(anomalies)

            # Process escalations
            if to_escalate:
                logger.info("processing_escalations", count=len(to_escalate))
                for anomaly in to_escalate:
                    await self._handle_anomaly(anomaly)

        # Periodic cleanup (run in thread pool to avoid blocking)
        logger.debug("cleaning_old_data")
        await self.db.cleanup_old_data_async()

        cycle_duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.info(
            "check_cycle_completed",
            cycle_number=self._check_cycle_count,
            duration=f"{cycle_duration:.2f}s",
        )

    async def _handle_anomaly(self, anomaly: Anomaly) -> None:
        """Handle a detected anomaly - get AI explanation and notify."""
        logger.info(
            "processing_anomaly",
            container=anomaly.container_name,
            metric=anomaly.metric_name,
            severity=anomaly.severity.value,
        )

        try:
            # Get AI explanation with timeout
            explanation = await self.claude_client.explain_anomaly(anomaly)
            recommendations = self.claude_client.extract_recommendations(explanation)

            # Create escalation result
            result = EscalationResult(
                anomaly=anomaly,
                explanation=explanation,
                recommendations=recommendations,
                should_notify=True,
            )

            # Record the escalation
            self.escalation_decision.record_escalation(anomaly, explanation)

            # Send notification with timeout
            await asyncio.wait_for(
                self.discord.send_alert(result),
                timeout=30,
            )

        except asyncio.TimeoutError:
            logger.error(
                "anomaly_handling_timeout",
                container=anomaly.container_name,
                metric=anomaly.metric_name,
            )
        except Exception as e:
            logger.exception(
                "anomaly_handling_error",
                container=anomaly.container_name,
                error=str(e),
            )

    def _update_heartbeat(self) -> None:
        """Update the heartbeat file for health checks."""
        self._last_heartbeat = datetime.now(timezone.utc)
        try:
            WATCHDOG_FILE.write_text(
                f"{self._last_heartbeat.isoformat()}\n{self._check_cycle_count}\n"
            )
        except Exception as e:
            logger.warning("watchdog_update_failed", error=str(e))

    async def _send_timeout_alert(self) -> None:
        """Send emergency notification when check cycle times out."""
        if not self.discord.config.enabled:
            return

        if not self.discord._session:
            await self.discord.start()

        payload = {
            "embeds": [{
                "title": "🚨 Sentinel Check Cycle Timeout",
                "description": f"A monitoring check cycle exceeded the timeout of {CHECK_CYCLE_TIMEOUT}s. "
                               f"This may indicate a hung operation.\n\n"
                               f"**Cycle:** {self._check_cycle_count}\n"
                               f"**Last Heartbeat:** {self._last_heartbeat.isoformat()}",
                "color": 0xE74C3C,  # Red
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }]
        }

        try:
            import aiohttp
            async with self.discord._session.post(
                self.discord.config.webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 204:
                    logger.warning("timeout_alert_failed", status=response.status)
        except Exception as e:
            logger.warning("timeout_alert_error", error=str(e))


async def main() -> None:
    """Main entry point."""
    # Load configuration
    config = load_config()

    # Create and run sentinel
    sentinel = Sentinel(config)

    try:
        await sentinel.start()
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    finally:
        await sentinel.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
