"""Claude API client for generating alert explanations."""

import asyncio

import anthropic
import structlog

from sentinel.analysis.anomaly import Anomaly
from sentinel.config import EscalationConfig

logger = structlog.get_logger()

SYSTEM_PROMPT = """You are a senior infrastructure engineer helping a homelabber understand monitoring alerts. Be direct and practical:

- Explain what's actually happening in plain English
- Tell them if they should worry or not (and why)
- Give specific, actionable remediation steps
- Reference the specific container, metrics, and values provided
- If it's likely a false positive or expected behavior, say so clearly

Keep responses concise but complete - aim for 150-250 words."""


class ClaudeClient:
    """Client for Claude API to generate alert explanations."""

    def __init__(self, config: EscalationConfig):
        self.config = config
        self.client = anthropic.Anthropic()
        # Rate limiter: max 2 concurrent API calls
        self._rate_limiter = asyncio.Semaphore(2)

    async def explain_anomaly(self, anomaly: Anomaly, timeout: float = 30.0) -> str:
        """Generate an explanation for an anomaly."""
        prompt = self._build_prompt(anomaly)

        async with self._rate_limiter:
            try:
                # Run sync API call in thread pool with timeout
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.client.messages.create,
                        model=self.config.model,
                        max_tokens=self.config.max_tokens,
                        system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": prompt}],
                    ),
                    timeout=timeout,
                )

                explanation = response.content[0].text

                logger.info(
                    "claude_explanation_generated",
                    container=anomaly.container_name,
                    metric=anomaly.metric_name,
                    tokens_used=response.usage.input_tokens + response.usage.output_tokens,
                )

                return explanation

            except asyncio.TimeoutError:
                logger.error("claude_api_timeout", container=anomaly.container_name)
                return f"Anomaly detected in {anomaly.container_name}: {anomaly.metric_name} at {anomaly.current_value:.2f} (z-score: {anomaly.z_score:.2f}). AI explanation timed out."

            except anthropic.APIError as e:
                logger.error("claude_api_error", error=str(e))
                return f"Unable to generate explanation: {e}"

    def _build_prompt(self, anomaly: Anomaly) -> str:
        """Build the prompt for Claude."""
        # Format recent logs
        logs_section = ""
        if anomaly.recent_logs:
            log_lines = anomaly.recent_logs[-15:]  # Last 15 lines
            logs_section = f"""
Recent container logs:
```
{chr(10).join(log_lines)}
```
"""

        # Build context section
        context_lines = []
        if self.config.cluster_context:
            context_lines.append(f"Cluster context: {self.config.cluster_context}")

        context_lines.extend([
            f"Container image: {anomaly.context.get('image', 'unknown')}",
            f"Container status: {anomaly.context.get('status', 'unknown')}",
            f"Uptime: {anomaly.context.get('uptime_seconds', 0):.0f} seconds",
        ])

        if anomaly.context.get("memory_limit"):
            mem_used_mb = anomaly.context.get("memory_bytes", 0) / 1024 / 1024
            mem_limit_mb = anomaly.context.get("memory_limit", 0) / 1024 / 1024
            context_lines.append(
                f"Memory: {mem_used_mb:.1f}MB / {mem_limit_mb:.1f}MB"
            )

        context_section = "\n".join(context_lines)

        prompt = f"""An anomaly was detected in my homelab infrastructure:

**Container:** {anomaly.container_name}
**Metric:** {anomaly.metric_name}
**Current Value:** {anomaly.current_value:.2f}
**Baseline Mean:** {anomaly.baseline_mean:.2f}
**Baseline StdDev:** {anomaly.baseline_stddev:.2f}
**Z-Score:** {anomaly.z_score:.2f} (threshold: 3.0)
**Severity:** {anomaly.severity.value}

{context_section}
{logs_section}

Explain this anomaly. What's happening, should I be concerned, and what should I do?"""

        return prompt

    def extract_recommendations(self, explanation: str) -> list[str]:
        """Extract actionable recommendations from the explanation."""
        recommendations = []

        # Look for numbered items or bullet points
        lines = explanation.split("\n")
        for line in lines:
            line = line.strip()
            # Match "1.", "2.", "-", "*", etc.
            if (
                line
                and len(line) > 5
                and (
                    line[0].isdigit()
                    or line.startswith("-")
                    or line.startswith("*")
                    or line.startswith("•")
                )
            ):
                # Clean up the line
                clean = line.lstrip("0123456789.-*• ").strip()
                if clean and len(clean) > 10:
                    recommendations.append(clean)

        return recommendations[:5]  # Max 5 recommendations
