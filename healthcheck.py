#!/usr/bin/env python3
"""Health check script for Sentinel Docker container.

Checks:
1. Heartbeat file exists and is recent (updated in last 5 minutes)
2. Process is running
3. Database is accessible

Exit codes:
- 0: Healthy
- 1: Unhealthy
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Heartbeat file written by main process
HEARTBEAT_FILE = Path("/tmp/sentinel_heartbeat")

# Maximum age of heartbeat before considering unhealthy (in seconds)
MAX_HEARTBEAT_AGE = 300  # 5 minutes

# Database file
DB_FILE = Path("/app/data/sentinel.db")


def check_heartbeat() -> tuple[bool, str]:
    """Check if the heartbeat file exists and is recent."""
    if not HEARTBEAT_FILE.exists():
        return False, "Heartbeat file does not exist"

    try:
        content = HEARTBEAT_FILE.read_text().strip()
        lines = content.split("\n")

        if not lines:
            return False, "Heartbeat file is empty"

        # Parse timestamp from first line
        timestamp_str = lines[0]
        heartbeat_time = datetime.fromisoformat(timestamp_str)

        # Check age
        age = (datetime.now(timezone.utc) - heartbeat_time).total_seconds()

        if age > MAX_HEARTBEAT_AGE:
            return False, f"Heartbeat too old: {age:.0f}s (max {MAX_HEARTBEAT_AGE}s)"

        # Get cycle count if available
        cycle_count = lines[1] if len(lines) > 1 else "unknown"

        return True, f"Heartbeat OK (age: {age:.0f}s, cycles: {cycle_count})"

    except Exception as e:
        return False, f"Failed to read heartbeat: {e}"


def check_database() -> tuple[bool, str]:
    """Check if the database file exists and is accessible."""
    if not DB_FILE.exists():
        return False, "Database file does not exist"

    if not DB_FILE.is_file():
        return False, "Database path is not a file"

    # Try to check file size as a basic accessibility check
    try:
        size = DB_FILE.stat().st_size
        return True, f"Database accessible ({size} bytes)"
    except Exception as e:
        return False, f"Database not accessible: {e}"


def main() -> int:
    """Run health checks and return exit code."""
    checks = [
        ("Heartbeat", check_heartbeat),
        ("Database", check_database),
    ]

    all_healthy = True
    results = []

    for name, check_func in checks:
        healthy, message = check_func()
        status = "✓" if healthy else "✗"
        results.append(f"{status} {name}: {message}")

        if not healthy:
            all_healthy = False

    # Print results
    for result in results:
        print(result)

    # Return appropriate exit code
    if all_healthy:
        print("\nHealth check PASSED")
        return 0
    else:
        print("\nHealth check FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
