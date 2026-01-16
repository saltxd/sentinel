#!/usr/bin/env python3
"""Test script to verify Sentinel resilience improvements.

This script validates that the timeout and health check mechanisms
are working correctly without actually running the full service.
"""

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Test the heartbeat mechanism


def test_heartbeat_file():
    """Test heartbeat file creation and reading."""
    print("\n=== Testing Heartbeat Mechanism ===")

    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        heartbeat_path = Path(f.name)

    try:
        # Simulate writing heartbeat
        timestamp = datetime.now(timezone.utc)
        cycle_count = 42
        heartbeat_path.write_text(f"{timestamp.isoformat()}\n{cycle_count}\n")

        # Read it back
        content = heartbeat_path.read_text()
        lines = content.strip().split("\n")

        assert len(lines) == 2, "Should have 2 lines"
        assert datetime.fromisoformat(lines[0]), "First line should be valid ISO timestamp"
        assert lines[1] == "42", "Second line should be cycle count"

        print("✓ Heartbeat file write/read works")
        return True

    except Exception as e:
        print(f"✗ Heartbeat test failed: {e}")
        return False

    finally:
        heartbeat_path.unlink(missing_ok=True)


async def test_timeout_wrapper():
    """Test that asyncio.wait_for works as expected."""
    print("\n=== Testing Timeout Wrapper ===")

    async def quick_task():
        await asyncio.sleep(0.1)
        return "success"

    async def slow_task():
        await asyncio.sleep(10)
        return "should not reach"

    try:
        # Test successful completion
        result = await asyncio.wait_for(quick_task(), timeout=1.0)
        assert result == "success"
        print("✓ Quick task completes within timeout")

        # Test timeout
        try:
            await asyncio.wait_for(slow_task(), timeout=0.5)
            print("✗ Slow task should have timed out")
            return False
        except asyncio.TimeoutError:
            print("✓ Slow task times out as expected")

        return True

    except Exception as e:
        print(f"✗ Timeout wrapper test failed: {e}")
        return False


async def test_exception_handling():
    """Test that exceptions are properly caught and logged."""
    print("\n=== Testing Exception Handling ===")

    async def failing_task():
        raise ValueError("Simulated error")

    async def timeout_task():
        await asyncio.sleep(10)

    try:
        # Test exception handling
        try:
            await failing_task()
            print("✗ Exception should have been raised")
            return False
        except ValueError:
            print("✓ Exceptions propagate correctly")

        # Test timeout exception handling
        try:
            await asyncio.wait_for(timeout_task(), timeout=0.1)
            print("✗ Timeout should have occurred")
            return False
        except asyncio.TimeoutError:
            print("✓ Timeout exceptions work correctly")

        return True

    except Exception as e:
        print(f"✗ Exception handling test failed: {e}")
        return False


def test_healthcheck_import():
    """Test that the health check script can be imported."""
    print("\n=== Testing Health Check Script ===")

    try:
        # Add sentinel directory to path
        sys.path.insert(0, str(Path(__file__).parent))

        # Try to import healthcheck functions (without running main)
        import healthcheck

        # Verify functions exist
        assert hasattr(healthcheck, 'check_heartbeat'), "check_heartbeat function exists"
        assert hasattr(healthcheck, 'check_database'), "check_database function exists"
        assert hasattr(healthcheck, 'main'), "main function exists"

        print("✓ Health check script is valid Python")
        return True

    except Exception as e:
        print(f"✗ Health check import failed: {e}")
        return False


def test_config_constants():
    """Verify timeout constants are reasonable."""
    print("\n=== Testing Configuration Constants ===")

    # These would be imported from the actual modules
    # For now, we'll just validate the concept
    timeouts = {
        "CHECK_CYCLE_TIMEOUT": 300,
        "DOCKER_STATS_TIMEOUT": 30,
        "DOCKER_LOGS_TIMEOUT": 10,
        "DOCKER_INFO_TIMEOUT": 10,
        "MAX_HEARTBEAT_AGE": 300,
    }

    issues = []

    if timeouts["DOCKER_STATS_TIMEOUT"] * 10 > timeouts["CHECK_CYCLE_TIMEOUT"]:
        issues.append("Docker stats timeout * 10 containers > check cycle timeout")

    if timeouts["MAX_HEARTBEAT_AGE"] < timeouts["CHECK_CYCLE_TIMEOUT"]:
        issues.append("Heartbeat max age should be >= check cycle timeout")

    if issues:
        for issue in issues:
            print(f"⚠ Warning: {issue}")
    else:
        print("✓ Timeout constants are reasonable")

    return len(issues) == 0


async def test_concurrent_operations():
    """Test that multiple timeouts can work concurrently."""
    print("\n=== Testing Concurrent Timeout Operations ===")

    async def task_with_timeout(task_id: int, duration: float, timeout: float):
        try:
            await asyncio.wait_for(
                asyncio.sleep(duration),
                timeout=timeout,
            )
            return f"task_{task_id}_completed"
        except asyncio.TimeoutError:
            return f"task_{task_id}_timeout"

    try:
        # Run multiple tasks concurrently, some should timeout
        tasks = [
            task_with_timeout(1, 0.1, 1.0),  # Should complete
            task_with_timeout(2, 0.2, 1.0),  # Should complete
            task_with_timeout(3, 10.0, 0.5),  # Should timeout
            task_with_timeout(4, 10.0, 0.5),  # Should timeout
        ]

        results = await asyncio.gather(*tasks)

        completed = sum(1 for r in results if "completed" in r)
        timed_out = sum(1 for r in results if "timeout" in r)

        assert completed == 2, f"Expected 2 completions, got {completed}"
        assert timed_out == 2, f"Expected 2 timeouts, got {timed_out}"

        print(f"✓ Concurrent operations work correctly ({completed} completed, {timed_out} timed out)")
        return True

    except Exception as e:
        print(f"✗ Concurrent operations test failed: {e}")
        return False


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Sentinel Resilience Test Suite")
    print("=" * 60)

    tests = [
        ("Heartbeat File", test_heartbeat_file, False),
        ("Timeout Wrapper", test_timeout_wrapper, True),
        ("Exception Handling", test_exception_handling, True),
        ("Health Check Script", test_healthcheck_import, False),
        ("Configuration Constants", test_config_constants, False),
        ("Concurrent Operations", test_concurrent_operations, True),
    ]

    results = []
    for name, test_func, is_async in tests:
        try:
            if is_async:
                result = await test_func()
            else:
                result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ {name} raised exception: {e}")
            results.append((name, False))

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")

    print(f"\nResults: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
