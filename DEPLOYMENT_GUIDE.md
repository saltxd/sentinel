# Sentinel Deployment Guide - Resilience Fixes

## Quick Start

To deploy the resilience improvements:

```bash
cd /home/admin/sentinel

# 1. Stop current container
docker-compose down

# 2. Rebuild with new fixes
docker-compose build --no-cache

# 3. Start with health checks enabled
docker-compose up -d

# 4. Watch logs for any issues
docker-compose logs -f

# 5. Verify health check is working
docker inspect sentinel --format='{{.State.Health.Status}}'
```

## Verification Steps

### 1. Check Health Status

```bash
# Should show "healthy" after 90 seconds
docker inspect sentinel --format='{{.State.Health.Status}}'

# View detailed health check results
docker inspect sentinel --format='{{json .State.Health}}' | jq
```

### 2. Verify Heartbeat File

```bash
# Check heartbeat is being updated
docker exec sentinel cat /tmp/sentinel_heartbeat

# Wait 30 seconds and check again - should be different
sleep 30
docker exec sentinel cat /tmp/sentinel_heartbeat
```

### 3. Monitor Logs

```bash
# Watch for check cycle messages
docker-compose logs -f | grep check_cycle

# Look for timeout warnings (should be rare)
docker-compose logs | grep timeout
```

### 4. Test Health Check Manually

```bash
# Run health check inside container
docker exec sentinel python /app/healthcheck.py

# Should output:
# ✓ Heartbeat: Heartbeat OK (age: Xs, cycles: Y)
# ✓ Database: Database accessible (Z bytes)
# Health check PASSED
```

## What Changed

### Files Modified

1. **`sentinel/main.py`** - Core monitoring loop
   - Added heartbeat tracking
   - Added check cycle timeout (5 minutes)
   - Added emergency timeout alerts
   - Enhanced logging with cycle numbers
   - Wrapped anomaly handling in timeout

2. **`sentinel/collectors/docker_collector.py`** - Docker metrics collection
   - Added timeouts to all Docker API calls
   - Protected async generator operations
   - Better error handling for hung operations

3. **`Dockerfile`** - Container image
   - Added `healthcheck.py` to image
   - Added HEALTHCHECK directive

4. **`docker-compose.yml`** - Service configuration
   - Added healthcheck configuration

### Files Added

1. **`healthcheck.py`** - Health check script
   - Verifies heartbeat file is recent
   - Checks database accessibility
   - Returns exit code for Docker

2. **`test_resilience.py`** - Test suite
   - Validates timeout mechanisms
   - Tests heartbeat file operations
   - Verifies exception handling

3. **`RESILIENCE_IMPROVEMENTS.md`** - Technical documentation
   - Detailed explanation of changes
   - Configuration options
   - Troubleshooting guide

4. **`DEPLOYMENT_GUIDE.md`** - This file

## Expected Behavior

### Normal Operation

- Check cycle completes in 5-30 seconds (depends on container count)
- Heartbeat updated after each cycle
- Health check passes every 60 seconds
- No timeout errors in logs

### Timeout Scenarios

#### Docker Stats Timeout (30s)

```
[warning] stats_fetch_timeout container=mycontainer timeout=30
```

**What it means:** Docker stats collection for one container hung
**Impact:** That container skipped this cycle, others proceed normally
**Action:** Usually recovers automatically, monitor if persistent

#### Check Cycle Timeout (5 minutes)

```
[error] check_cycle_timeout timeout=300 cycle_count=42
```

**What it means:** Entire monitoring cycle took too long
**Impact:** Emergency alert sent, cycle aborted, next cycle attempted
**Action:** Investigate which operation is slow

#### Health Check Failure

```
✗ Heartbeat: Heartbeat too old: 320s (max 300s)
Health check FAILED
```

**What it means:** Monitoring loop stopped updating heartbeat
**Impact:** After 3 failures (3 minutes), Docker restarts container
**Action:** Container will auto-recover via restart

## Monitoring

### Key Log Messages

**Normal operation:**
```
[info] check_cycle_started cycle_number=42
[info] check_cycle_completed cycle_number=42 duration=8.23s
```

**Timeout occurred:**
```
[warning] stats_fetch_timeout container=nginx timeout=30
[error] check_cycle_timeout timeout=300 cycle_count=42
```

**Emergency alert:**
```
[info] Sending timeout alert to Discord
```

### Health Check Commands

```bash
# Current health status
docker inspect sentinel --format='{{.State.Health.Status}}'

# Number of consecutive failures
docker inspect sentinel --format='{{.State.Health.FailingStreak}}'

# Last 5 health check results
docker inspect sentinel --format='{{json .State.Health.Log}}' | jq '.[-5:]'
```

### Performance Metrics

```bash
# Average cycle duration (from logs)
docker logs sentinel 2>&1 | grep "check_cycle_completed" | tail -20

# Count of timeout errors
docker logs sentinel 2>&1 | grep -c "timeout"

# Heartbeat age
docker exec sentinel cat /tmp/sentinel_heartbeat | head -1
```

## Troubleshooting

### Container Status: Unhealthy

**Symptom:** `docker inspect sentinel --format='{{.State.Health.Status}}'` shows "unhealthy"

**Diagnosis:**
```bash
# Check what the health check is reporting
docker exec sentinel python /app/healthcheck.py

# Check if heartbeat file exists
docker exec sentinel ls -la /tmp/sentinel_heartbeat

# Check main process
docker exec sentinel ps aux
```

**Solutions:**
- If heartbeat is old: Monitoring loop is hung, restart will fix
- If heartbeat missing: Service crashed, check logs
- If database missing: Volume mount issue, check docker-compose.yml

### Frequent Timeout Errors

**Symptom:** Many `stats_fetch_timeout` or `check_cycle_timeout` in logs

**Diagnosis:**
```bash
# Check Docker daemon responsiveness
docker stats --no-stream

# Check system load
docker exec sentinel top -bn1

# Count containers being monitored
docker ps | wc -l
```

**Solutions:**
- Docker daemon slow: Restart Docker daemon
- Too many containers: Increase timeouts or reduce monitored containers
- System overload: Add resources or reduce check frequency

### Service Keeps Restarting

**Symptom:** Container restart count increasing

**Diagnosis:**
```bash
# Check restart count
docker inspect sentinel --format='{{.RestartCount}}'

# Check why it's restarting
docker logs sentinel --tail 100

# Check health check logs
docker inspect sentinel --format='{{json .State.Health.Log}}' | jq
```

**Solutions:**
- Health check too strict: Increase `MAX_HEARTBEAT_AGE` in healthcheck.py
- Service crashing: Fix underlying issue from logs
- Resource constraints: Increase container limits

### Emergency Discord Alerts

**Symptom:** Receiving timeout alerts in Discord

**Diagnosis:**
```bash
# Check which operation is timing out
docker logs sentinel | grep -A5 -B5 "check_cycle_timeout"

# Check if it's recovering
docker logs sentinel | grep "check_cycle_completed" | tail -5
```

**Solutions:**
- Occasional: Normal, can happen under load
- Persistent: Investigate slow operations, may need to increase timeouts
- After every cycle: Critical issue, check Docker daemon and system health

## Configuration Tuning

### Adjusting Timeouts

If you need to adjust timeout values based on your environment:

**`sentinel/main.py`:**
```python
CHECK_CYCLE_TIMEOUT = 300  # Default: 5 minutes
# Increase if you have many containers or slow Docker daemon
# Decrease if you want faster failure detection
```

**`sentinel/collectors/docker_collector.py`:**
```python
DOCKER_STATS_TIMEOUT = 30  # Default: 30 seconds
DOCKER_LOGS_TIMEOUT = 10   # Default: 10 seconds
DOCKER_INFO_TIMEOUT = 10   # Default: 10 seconds
# Increase if Docker operations are consistently slow
```

**`healthcheck.py`:**
```python
MAX_HEARTBEAT_AGE = 300  # Default: 5 minutes
# Must be >= CHECK_CYCLE_TIMEOUT
# Increase to reduce false positive health check failures
```

**`docker-compose.yml`:**
```yaml
healthcheck:
  interval: 60s      # How often to run health check
  timeout: 10s       # Max time for health check to run
  retries: 3         # Failures before marking unhealthy
  start_period: 90s  # Grace period on startup
```

After changing timeouts, rebuild and redeploy:
```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

## Rollback Procedure

If you need to rollback to the previous version:

```bash
cd /home/admin/sentinel

# 1. Stop current container
docker-compose down

# 2. Revert code changes
git checkout HEAD~1 sentinel/main.py
git checkout HEAD~1 sentinel/collectors/docker_collector.py
git checkout HEAD~1 Dockerfile
git checkout HEAD~1 docker-compose.yml

# 3. Remove new files
rm healthcheck.py test_resilience.py RESILIENCE_IMPROVEMENTS.md DEPLOYMENT_GUIDE.md

# 4. Rebuild and restart
docker-compose build --no-cache
docker-compose up -d
```

## Success Criteria

After deployment, within 5 minutes you should see:

- ✓ Container status is "healthy"
- ✓ Logs show regular "check_cycle_completed" messages
- ✓ Heartbeat file is being updated regularly
- ✓ No timeout errors in logs (occasional ones are okay)
- ✓ Discord shows startup message (if enabled)

## Support

For issues or questions:

1. Check logs: `docker-compose logs -f`
2. Review technical docs: `RESILIENCE_IMPROVEMENTS.md`
3. Run tests: `python test_resilience.py`
4. Check health: `docker exec sentinel python /app/healthcheck.py`
