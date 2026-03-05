"""Docker collector - gather container stats and logs via Docker socket."""

import asyncio
from datetime import datetime, timezone
from typing import Any

import aiodocker
import structlog

from sentinel.config import DockerCollectorConfig

from .base import BaseCollector, CollectorResult, ContainerMetrics

logger = structlog.get_logger()


class DockerCollector(BaseCollector):
    """Collect metrics from Docker containers via the Docker socket."""

    def __init__(self, config: DockerCollectorConfig):
        self.config = config
        self._client: aiodocker.Docker | None = None

    @property
    def name(self) -> str:
        return "docker"

    async def start(self) -> None:
        """Initialize Docker client."""
        try:
            self._client = aiodocker.Docker()
            logger.info("docker_collector_started", socket=self.config.socket)
        except Exception as e:
            logger.error("docker_collector_start_failed", error=str(e))
            raise

    async def stop(self) -> None:
        """Close Docker client."""
        if self._client:
            await self._client.close()
            self._client = None

    async def health_check(self) -> bool:
        """Check Docker connectivity."""
        try:
            if not self._client:
                return False
            await self._client.version()
            return True
        except Exception:
            return False

    async def collect(self) -> CollectorResult:
        """Collect metrics from all running containers."""
        if not self._client:
            return CollectorResult.empty(self.name)

        timestamp = datetime.now(timezone.utc)
        containers_metrics: list[ContainerMetrics] = []
        errors: list[str] = []

        try:
            containers = await self._client.containers.list()

            # Collect stats for each container with per-container timeout
            tasks = [
                asyncio.wait_for(
                    self._collect_container_metrics(container),
                    timeout=15.0,
                )
                for container in containers
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for container, result in zip(containers, results):
                if isinstance(result, asyncio.TimeoutError):
                    name = container._id[:12]
                    logger.warning("container_stats_timeout", container=name)
                    errors.append(f"Timeout collecting stats for {name}")
                elif isinstance(result, Exception):
                    errors.append(str(result))
                elif result:
                    containers_metrics.append(result)

        except Exception as e:
            logger.error("docker_collection_failed", error=str(e))
            errors.append(f"Collection failed: {e}")

        result = CollectorResult(
            timestamp=timestamp,
            source=self.name,
            containers=containers_metrics,
            errors=errors,
            metadata={"container_count": len(containers_metrics)},
        )

        logger.debug(
            "docker_collection_complete",
            containers=len(containers_metrics),
            errors=len(errors),
        )

        return result

    async def _collect_container_metrics(
        self, container: aiodocker.containers.DockerContainer
    ) -> ContainerMetrics | None:
        """Collect metrics for a single container."""
        try:
            # Get container info
            info = await container.show()
            container_id = info["Id"][:12]
            container_name = info["Name"].lstrip("/")
            image = info["Config"]["Image"]
            status = info["State"]["Status"]
            labels = info["Config"].get("Labels", {}) or {}

            # Calculate uptime
            started_at = info["State"].get("StartedAt", "")
            uptime_seconds = 0.0
            if started_at and status == "running":
                try:
                    # Parse ISO format timestamp
                    start_time = datetime.fromisoformat(
                        started_at.replace("Z", "+00:00").split(".")[0] + "+00:00"
                    )
                    uptime_seconds = (
                        datetime.now(timezone.utc) - start_time
                    ).total_seconds()
                except Exception:
                    pass

            # Get restart count
            restart_count = info["RestartCount"]

            # Get resource stats (only for running containers)
            cpu_percent = 0.0
            memory_bytes = 0
            memory_limit = 0
            memory_percent = 0.0

            if status == "running":
                try:
                    stats = await self._get_container_stats(container)
                    cpu_percent = stats.get("cpu_percent", 0.0)
                    memory_bytes = stats.get("memory_bytes", 0)
                    memory_limit = stats.get("memory_limit", 0)
                    if memory_limit > 0:
                        memory_percent = (memory_bytes / memory_limit) * 100
                except Exception as e:
                    logger.warning(
                        "stats_fetch_failed",
                        container=container_name,
                        error=str(e),
                    )

            # Get recent logs
            recent_logs = await self._get_container_logs(
                container, self.config.log_tail_lines
            )

            return ContainerMetrics(
                container_id=container_id,
                container_name=container_name,
                image=image,
                status=status,
                cpu_percent=cpu_percent,
                memory_bytes=memory_bytes,
                memory_limit=memory_limit,
                memory_percent=memory_percent,
                restart_count=restart_count,
                uptime_seconds=uptime_seconds,
                labels=labels,
                recent_logs=recent_logs,
            )

        except Exception as e:
            logger.warning(
                "container_metrics_failed",
                container=str(container),
                error=str(e),
            )
            return None

    async def _get_container_stats(
        self, container: aiodocker.containers.DockerContainer
    ) -> dict[str, Any]:
        """Get CPU and memory stats for a container."""
        stats_generator = container.stats(stream=True)

        try:
            stats1 = await asyncio.wait_for(
                stats_generator.__anext__(), timeout=5.0
            )
            stats2 = await asyncio.wait_for(
                stats_generator.__anext__(), timeout=5.0
            )
        finally:
            await stats_generator.aclose()

        # Calculate CPU percentage
        cpu_delta = (
            stats2["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats1["cpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats2["cpu_stats"]["system_cpu_usage"]
            - stats1["cpu_stats"]["system_cpu_usage"]
        )

        cpu_percent = 0.0
        if system_delta > 0:
            num_cpus = stats2["cpu_stats"].get("online_cpus", 1)
            cpu_percent = (cpu_delta / system_delta) * num_cpus * 100

        # Memory stats
        memory_stats = stats2.get("memory_stats", {})
        memory_bytes = memory_stats.get("usage", 0)
        memory_limit = memory_stats.get("limit", 0)

        return {
            "cpu_percent": round(cpu_percent, 2),
            "memory_bytes": memory_bytes,
            "memory_limit": memory_limit,
        }

    async def _get_container_logs(
        self, container: aiodocker.containers.DockerContainer, tail: int
    ) -> list[str]:
        """Get recent log lines from a container."""
        try:
            logs = await container.log(
                stdout=True,
                stderr=True,
                tail=tail,
            )
            # logs is a list of log lines
            return [line.strip() for line in logs if line.strip()]
        except Exception as e:
            logger.warning("log_fetch_failed", error=str(e))
            return []
