"""Kubernetes collector - placeholder for future implementation."""

from .base import BaseCollector, CollectorResult


class KubernetesCollector(BaseCollector):
    """Collect metrics from Kubernetes API. (Future implementation)"""

    @property
    def name(self) -> str:
        return "kubernetes"

    async def collect(self) -> CollectorResult:
        """Collect metrics from Kubernetes."""
        # TODO: Implement K8s API collection
        return CollectorResult.empty(self.name)

    async def health_check(self) -> bool:
        """Check Kubernetes API connectivity."""
        # TODO: Implement health check
        return False
