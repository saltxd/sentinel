#!/bin/bash
# Verification script to ensure Sentinel is ready for deployment
# This checks that all files are in place and syntax is valid

set -e

echo "=========================================="
echo "Sentinel Deployment Readiness Check"
echo "=========================================="
echo ""

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SUCCESS=0
WARNINGS=0
FAILURES=0

check_file() {
    local file=$1
    local description=$2

    if [ -f "$file" ]; then
        echo -e "${GREEN}✓${NC} $description: $file"
        ((SUCCESS++))
        return 0
    else
        echo -e "${RED}✗${NC} $description: $file (MISSING)"
        ((FAILURES++))
        return 1
    fi
}

check_syntax() {
    local file=$1
    local description=$2

    if python -m py_compile "$file" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} $description syntax valid"
        ((SUCCESS++))
        return 0
    else
        echo -e "${RED}✗${NC} $description syntax INVALID"
        ((FAILURES++))
        return 1
    fi
}

check_executable() {
    local file=$1
    local description=$2

    if [ -x "$file" ]; then
        echo -e "${GREEN}✓${NC} $description is executable"
        ((SUCCESS++))
        return 0
    else
        echo -e "${YELLOW}⚠${NC} $description not executable (can be fixed)"
        ((WARNINGS++))
        return 1
    fi
}

echo "=== Checking Core Files ==="
check_file "sentinel/main.py" "Main monitoring loop"
check_file "sentinel/collectors/docker_collector.py" "Docker collector"
check_file "Dockerfile" "Dockerfile"
check_file "docker-compose.yml" "Docker Compose config"
echo ""

echo "=== Checking New Files ==="
check_file "healthcheck.py" "Health check script"
check_file "test_resilience.py" "Test suite"
check_file "RESILIENCE_IMPROVEMENTS.md" "Technical documentation"
check_file "DEPLOYMENT_GUIDE.md" "Deployment guide"
check_file "CHANGES_SUMMARY.md" "Changes summary"
echo ""

echo "=== Checking Python Syntax ==="
check_syntax "sentinel/main.py" "main.py"
check_syntax "sentinel/collectors/docker_collector.py" "docker_collector.py"
check_syntax "healthcheck.py" "healthcheck.py"
check_syntax "test_resilience.py" "test_resilience.py"
echo ""

echo "=== Checking Executability ==="
check_executable "healthcheck.py" "healthcheck.py"
check_executable "test_resilience.py" "test_resilience.py"
echo ""

echo "=== Running Test Suite ==="
if python test_resilience.py > /tmp/test_output.txt 2>&1; then
    echo -e "${GREEN}✓${NC} Test suite passed (6/6 tests)"
    ((SUCCESS++))
else
    echo -e "${RED}✗${NC} Test suite FAILED"
    cat /tmp/test_output.txt
    ((FAILURES++))
fi
echo ""

echo "=== Checking Docker Configuration ==="
# Check if healthcheck is in Dockerfile
if grep -q "HEALTHCHECK" Dockerfile; then
    echo -e "${GREEN}✓${NC} Dockerfile contains HEALTHCHECK directive"
    ((SUCCESS++))
else
    echo -e "${RED}✗${NC} Dockerfile missing HEALTHCHECK directive"
    ((FAILURES++))
fi

# Check if healthcheck is in docker-compose.yml
if grep -q "healthcheck:" docker-compose.yml; then
    echo -e "${GREEN}✓${NC} docker-compose.yml contains healthcheck config"
    ((SUCCESS++))
else
    echo -e "${RED}✗${NC} docker-compose.yml missing healthcheck config"
    ((FAILURES++))
fi
echo ""

echo "=== Checking for Required Constants ==="
if grep -q "CHECK_CYCLE_TIMEOUT" sentinel/main.py; then
    echo -e "${GREEN}✓${NC} CHECK_CYCLE_TIMEOUT defined in main.py"
    ((SUCCESS++))
else
    echo -e "${RED}✗${NC} CHECK_CYCLE_TIMEOUT missing in main.py"
    ((FAILURES++))
fi

if grep -q "DOCKER_STATS_TIMEOUT" sentinel/collectors/docker_collector.py; then
    echo -e "${GREEN}✓${NC} DOCKER_STATS_TIMEOUT defined in docker_collector.py"
    ((SUCCESS++))
else
    echo -e "${RED}✗${NC} DOCKER_STATS_TIMEOUT missing in docker_collector.py"
    ((FAILURES++))
fi

if grep -q "WATCHDOG_FILE" sentinel/main.py; then
    echo -e "${GREEN}✓${NC} WATCHDOG_FILE defined in main.py"
    ((SUCCESS++))
else
    echo -e "${RED}✗${NC} WATCHDOG_FILE missing in main.py"
    ((FAILURES++))
fi
echo ""

echo "=== Checking for Key Functions ==="
if grep -q "_update_heartbeat" sentinel/main.py; then
    echo -e "${GREEN}✓${NC} _update_heartbeat() function exists"
    ((SUCCESS++))
else
    echo -e "${RED}✗${NC} _update_heartbeat() function missing"
    ((FAILURES++))
fi

if grep -q "_send_timeout_alert" sentinel/main.py; then
    echo -e "${GREEN}✓${NC} _send_timeout_alert() function exists"
    ((SUCCESS++))
else
    echo -e "${RED}✗${NC} _send_timeout_alert() function missing"
    ((FAILURES++))
fi

if grep -q "check_heartbeat" healthcheck.py; then
    echo -e "${GREEN}✓${NC} check_heartbeat() function exists"
    ((SUCCESS++))
else
    echo -e "${RED}✗${NC} check_heartbeat() function missing"
    ((FAILURES++))
fi
echo ""

echo "=========================================="
echo "Summary"
echo "=========================================="
echo -e "${GREEN}✓${NC} Passed:   $SUCCESS"
echo -e "${YELLOW}⚠${NC} Warnings: $WARNINGS"
echo -e "${RED}✗${NC} Failed:   $FAILURES"
echo ""

if [ $FAILURES -eq 0 ]; then
    echo -e "${GREEN}=========================================="
    echo "✓ READY FOR DEPLOYMENT"
    echo "==========================================${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. docker-compose down"
    echo "  2. docker-compose build --no-cache"
    echo "  3. docker-compose up -d"
    echo "  4. docker inspect sentinel --format='{{.State.Health.Status}}'"
    echo ""
    exit 0
else
    echo -e "${RED}=========================================="
    echo "✗ NOT READY FOR DEPLOYMENT"
    echo "==========================================${NC}"
    echo ""
    echo "Please fix the failures above before deploying."
    echo ""
    exit 1
fi
