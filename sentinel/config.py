"""Configuration management for Sentinel."""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class DockerCollectorConfig(BaseModel):
    enabled: bool = True
    socket: str = "/var/run/docker.sock"
    log_tail_lines: int = 100


class KubernetesCollectorConfig(BaseModel):
    enabled: bool = False
    kubeconfig: str | None = None


class CollectorsConfig(BaseModel):
    docker: DockerCollectorConfig = Field(default_factory=DockerCollectorConfig)
    kubernetes: KubernetesCollectorConfig = Field(default_factory=KubernetesCollectorConfig)


class SuppressRule(BaseModel):
    pattern: str
    context: str | None = None
    action: str = "suppress"
    burst_threshold: int | None = None


class AnalysisConfig(BaseModel):
    baseline_window_hours: int = 24
    min_samples_for_baseline: int = 10
    anomaly_threshold: float = 5.0  # Higher threshold = fewer, more significant alerts
    startup_grace_seconds: int = 300  # Skip anomaly detection for first 5 minutes
    # Minimum absolute thresholds - don't alert unless values exceed these
    min_cpu_percent: float = 80.0  # Only alert if CPU > 80%
    min_memory_percent: float = 85.0  # Only alert if memory > 85%
    alert_on_low_values: bool = False  # Ignore drops (0% CPU is fine, not a problem)
    exclude_self_monitoring: bool = True  # Don't let sentinel alert on itself
    suppress_rules: list[SuppressRule] = Field(default_factory=list)


class EscalationConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 500
    cluster_context: str = ""


class DiscordConfig(BaseModel):
    enabled: bool = False
    webhook_url: str = ""


class SlackConfig(BaseModel):
    enabled: bool = False
    webhook_url: str = ""


class NotificationsConfig(BaseModel):
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)


class StorageConfig(BaseModel):
    database_path: str = "/app/data/sentinel.db"
    retention_hours: int = 48


class SentinelCoreConfig(BaseModel):
    check_interval: int = 60
    escalation_cooldown: int = 300


class Config(BaseModel):
    """Root configuration model."""

    sentinel: SentinelCoreConfig = Field(default_factory=SentinelCoreConfig)
    collectors: CollectorsConfig = Field(default_factory=CollectorsConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} patterns in strings."""
    if isinstance(value, str):
        pattern = re.compile(r'\$\{([^}]+)\}')

        def replacer(match: re.Match) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, "")

        return pattern.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


def load_config(config_path: str | Path = "/app/config.yaml") -> Config:
    """Load configuration from YAML file with environment variable expansion."""
    path = Path(config_path)

    if not path.exists():
        # Try config.example.yaml as fallback
        example_path = path.parent / "config.example.yaml"
        if example_path.exists():
            path = example_path
        else:
            # Return default config
            return Config()

    with open(path) as f:
        raw_config = yaml.safe_load(f)

    if raw_config is None:
        return Config()

    # Expand environment variables
    expanded = _expand_env_vars(raw_config)

    return Config.model_validate(expanded)
