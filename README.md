# Sentinel

AI-powered homelab monitoring agent. Runs 24/7, filters the noise locally, and only alerts you when something actually matters.

## Features

- **Docker monitoring** - Container stats (CPU, memory, restarts) and logs
- **Statistical anomaly detection** - Rolling baselines with z-score analysis
- **AI-powered explanations** - Claude analyzes anomalies and explains them in plain English
- **Smart alerting** - Cooldown periods, suppression rules, severity-based escalation
- **Discord notifications** - Rich embeds with context and recommendations

## Quick Start

1. **Copy config**
   ```bash
   cp config.example.yaml config.yaml
   ```

2. **Set environment variables**
   ```bash
   export ANTHROPIC_API_KEY="your-key"
   export DISCORD_WEBHOOK="your-webhook-url"
   ```

3. **Run with Docker Compose**
   ```bash
   docker-compose up -d
   ```

## Configuration

See `config.example.yaml` for all options. Key settings:

```yaml
sentinel:
  check_interval: 60        # Seconds between checks
  escalation_cooldown: 300  # Don't re-alert same issue within 5 min

analysis:
  baseline_window_hours: 24  # Rolling baseline window
  anomaly_threshold: 3.0     # Standard deviations for anomaly

  suppress_rules:            # Known false positives
    - pattern: "expected warning"
      action: suppress
```

## How It Works

1. **Collect** - Docker stats and logs every check interval
2. **Store** - Metrics buffered in SQLite (48h retention)
3. **Baseline** - Rolling mean/stddev per container per metric
4. **Detect** - Flag values >3 standard deviations from baseline
5. **Escalate** - Call Claude API for explanation (with cooldown)
6. **Notify** - Send rich Discord embed with AI explanation

## Development

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run locally (requires Docker socket access)
python -m sentinel.main
```

## Architecture

```
sentinel/
├── collectors/       # Data collection (Docker, K8s future)
├── analysis/         # Baseline tracking, anomaly detection
├── escalation/       # AI client, escalation decisions
├── notifications/    # Discord, Slack (future)
└── storage/          # SQLite metrics buffer
```

## Roadmap

- [ ] Kubernetes collector (K8s API)
- [ ] Prometheus scraping
- [ ] Slack notifications
- [ ] Web UI dashboard
- [ ] Custom alert rules
