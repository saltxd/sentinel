# Sentinel Resilience Improvements

This document describes the improvements made to prevent Sentinel from hanging without crashing.

## Problem Analysis

The monitoring service was hanging without crashing, likely due to:

1. **Blocking Docker API calls** - Docker stats collection using async generators could hang indefinitely
2. **No timeout protection** - Individual operations and the entire check cycle had no timeouts
3. **No watchdog mechanism** - If the monitoring loop hung, there was no detection or recovery
4. **No health check** - Docker couldn't detect that the service had stopped functioning
5. **Insufficient logging** - Hard to diagnose where hangs occurred

## Implemented Fixes

### 1. Watchdog/Heartbeat Mechanism (`main.py`)

**Changes:**
- Added heartbeat file at `/tmp/sentinel_heartbeat` that's updated after each successful check cycle
- Tracks cycle count and last update timestamp
- Health check script verifies heartbeat is recent (< 5 minutes old)

**Key code:**
```python
def _update_heartbeat(self) -> None:
    """Update the heartbeat file for health checks."""
    self._last_heartbeat = datetime.now(timezone.utc)
    WATCHDOG_FILE.write_text(
        f"{self._last_heartbeat.isoformat()}\n{self._check_cycle_count}\n"
    )
```

### 2. Check Cycle Timeout Protection (`main.py`)

**Changes:**
- Entire check cycle wrapped in `asyncio.wait_for()` with 5-minute timeout
- If timeout occurs, error is logged and emergency Discord alert is sent
- Service continues running (next cycle might succeed)
- Enhanced logging at each stage to identify where hangs occur

**Key code:**
```python
await asyncio.wait_for(
    self._run_check_cycle(),
    timeout=CHECK_CYCLE_TIMEOUT,  # 300 seconds
)
```

### 3. Docker Collector Timeouts (`docker_collector.py`)

**Changes:**
- Added timeouts to all Docker API operations:
  - `container.show()`: 10s timeout
  - `container.stats()`: 30s timeout (with nested 10s timeouts on generator)
  - `container.log()`: 10s timeout
- Each async generator operation individually wrapped with timeout
- Proper cleanup even when timeouts occur

**Key timeouts:**
```python
DOCKER_STATS_TIMEOUT = 30  # seconds
DOCKER_LOGS_TIMEOUT = 10   # seconds
DOCKER_INFO_TIMEOUT = 10   # seconds
```

**Critical fix in `_get_container_stats()`:**
```python
# The generator itself can hang
stats1 = await asyncio.wait_for(
    stats_generator.__anext__(),
    timeout=10,
)
stats2 = await asyncio.wait_for(
    stats_generator.__anext__(),
    timeout=10,
)

# Even cleanup can hang
await asyncio.wait_for(
    stats_generator.aclose(),
    timeout=2,
)
```

### 4. Anomaly Handling Timeouts (`main.py`)

**Changes:**
- Claude API explanation call already had 30s timeout
- Added 30s timeout to Discord notification
- Wrapped entire anomaly handling in try/except with timeout protection

### 5. Docker Health Check

**New files:**
- `healthcheck.py` - Standalone health check script
- Updated `Dockerfile` with HEALTHCHECK directive
- Updated `docker-compose.yml` with healthcheck configuration

**Health check behavior:**
- Runs every 60 seconds
- 90-second startup grace period (allows baseline collection)
- 10-second timeout per check
- 3 retries before marking unhealthy
- Docker will restart container if health check fails

**Checks performed:**
1. Heartbeat file exists and is recent (< 5 minutes old)
2. Database file exists and is accessible

### 6. Enhanced Logging

**Changes:**
- Added cycle number tracking to all log messages
- Log at the start of each major operation (collect, store, analyze, escalate, cleanup)
- Log cycle completion with duration
- Better error context (cycle number, heartbeat age, etc.)

**Example log output:**
```
[info] check_cycle_started cycle_number=42
[debug] collecting_docker_metrics
[debug] docker_collection_complete container_count=15
[debug] storing_metrics
[debug] analyzing_anomalies
[debug] cleaning_old_data
[info] check_cycle_completed cycle_number=42 duration=8.23s
```

## How It Works Together

1. **Normal operation:**
   - Each check cycle completes within timeout
   - Heartbeat file updated after successful cycle
   - Health check sees recent heartbeat → reports healthy
   - Docker sees healthy → no action needed

2. **Temporary hang (< 5 minutes):**
   - Operation hangs (e.g., Docker stats collection)
   - Timeout fires after 30s, logs error, continues
   - Next cycle may succeed
   - Health check still sees recent enough heartbeat → healthy

3. **Prolonged hang (> 5 minutes):**
   - Check cycle times out after 5 minutes
   - Emergency Discord alert sent
   - Heartbeat not updated for 5+ minutes
   - Health check fails → Docker restarts container
   - Service recovers automatically

## Testing

To test the resilience improvements:

```bash
# 1. Rebuild with new changes
cd /home/admin/sentinel
docker-compose build

# 2. Start service
docker-compose up -d

# 3. Watch logs
docker-compose logs -f

# 4. Check health status
docker inspect sentinel --format='{{.State.Health.Status}}'

# 5. View health check history
docker inspect sentinel --format='{{json .State.Health}}' | jq

# 6. Manually run health check
docker exec sentinel python /app/healthcheck.py

# 7. Check heartbeat file
docker exec sentinel cat /tmp/sentinel_heartbeat
```

## Monitoring Recommendations

1. **Watch for timeout errors** in logs:
   - `check_cycle_timeout` - Entire cycle took too long
   - `stats_fetch_timeout` - Docker stats hung
   - `container_metrics_timeout` - Container info collection hung
   - `anomaly_handling_timeout` - AI explanation or Discord notification hung

2. **Monitor health check status:**
   ```bash
   docker inspect sentinel --format='{{.State.Health.Status}}'
   ```

3. **Check cycle performance:**
   - Look for `check_cycle_completed` messages with duration
   - Normal cycles should complete in < 30 seconds
   - > 60 seconds indicates performance issues

4. **Heartbeat monitoring:**
   - External monitoring can read `/tmp/sentinel_heartbeat` from host
   - Parse timestamp and verify it's recent

## Recovery Behavior

- **Timeout in Docker collector:** Skips that container, continues with others
- **Timeout in anomaly handling:** Logs error, continues to next anomaly
- **Timeout in entire cycle:** Logs error, sends alert, tries again next cycle
- **Health check failure:** Docker restarts container after 3 consecutive failures (3 minutes)

## Configuration

Timeout values can be adjusted in the code:

**`main.py`:**
```python
CHECK_CYCLE_TIMEOUT = 300  # 5 minutes for entire check cycle
```

**`docker_collector.py`:**
```python
DOCKER_STATS_TIMEOUT = 30  # seconds
DOCKER_LOGS_TIMEOUT = 10   # seconds
DOCKER_INFO_TIMEOUT = 10   # seconds
```

**`healthcheck.py`:**
```python
MAX_HEARTBEAT_AGE = 300  # 5 minutes
```

**`docker-compose.yml`:**
```yaml
healthcheck:
  interval: 60s      # How often to check
  timeout: 10s       # How long to wait for check
  retries: 3         # How many failures before unhealthy
  start_period: 90s  # Grace period on startup
```

## Performance Impact

- **Minimal overhead:** Heartbeat file write is trivial (~1ms)
- **Health check:** Runs every 60s, takes ~10ms
- **Logging:** Structured logging is very efficient
- **Timeouts:** No overhead unless triggered

## Future Improvements

Potential enhancements:

1. **Metrics export** - Expose Prometheus metrics for external monitoring
2. **Adaptive timeouts** - Adjust based on historical performance
3. **Circuit breaker** - Temporarily disable problematic collectors
4. **Detailed timing** - Track time spent in each operation
5. **Alert escalation** - Page on-call if timeouts persist
