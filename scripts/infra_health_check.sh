#!/bin/bash
# =============================================================================
# Karsa Infrastructure Health Check Script
# =============================================================================
# Usage: ./scripts/infra_health_check.sh
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Counters
PASS=0
FAIL=0
WARN=0

# Helper functions
pass() {
    echo -e "${GREEN}✓ PASS${NC}: $1"
    ((PASS++))
}

fail() {
    echo -e "${RED}✗ FAIL${NC}: $1"
    ((FAIL++))
}

warn() {
    echo -e "${YELLOW}⚠ WARN${NC}: $1"
    ((WARN++))
}

section() {
    echo ""
    echo "=========================================="
    echo "  $1"
    echo "=========================================="
}

# =============================================================================
# INFRASTRUCTURE CHECKS
# =============================================================================

section "1. Docker Containers"

# Check if docker-compose is running
if docker-compose ps > /dev/null 2>&1; then
    pass "docker-compose is running"
else
    fail "docker-compose is not running"
fi

# Check individual containers
for container in karsa-redis karsa-postgres karsa-warp karsa-crypto-bot karsa-crypto-orchestrator karsa-prometheus karsa-grafana karsa-alertmanager; do
    status=$(docker-compose ps --format json 2>/dev/null | jq -r ".[] | select(.Name == \"$container\") | .State" 2>/dev/null || echo "not found")
    if [ "$status" = "running" ]; then
        pass "$container is running"
    else
        fail "$container is not running (state: $status)"
    fi
done

section "2. Redis Health"

# Ping Redis
redis_ping=$(docker-compose exec -T redis redis-cli -a "${REDIS_PASSWORD}" ping 2>/dev/null || echo "FAILED")
if [ "$redis_ping" = "PONG" ]; then
    pass "Redis ping successful"
else
    fail "Redis ping failed: $redis_ping"
fi

# Check Redis memory
redis_memory=$(docker-compose exec -T redis redis-cli -a "${REDIS_PASSWORD}" info memory 2>/dev/null | grep "used_memory_human" | cut -d: -f2 | tr -d '[:space:]')
if [ -n "$redis_memory" ]; then
    pass "Redis memory usage: $redis_memory"
else
    warn "Could not get Redis memory info"
fi

section "3. PostgreSQL Health"

# Ping PostgreSQL
pg_ping=$(docker-compose exec -T postgres pg_isready -U trader -d trading 2>/dev/null || echo "FAILED")
if echo "$pg_ping" | grep -q "accepting connections"; then
    pass "PostgreSQL is accepting connections"
else
    fail "PostgreSQL is not accepting connections: $pg_ping"
fi

# Check pgvector extension
pgvector=$(docker-compose exec -T postgres psql -U trader -d trading -t -c "SELECT 1 FROM pg_extension WHERE extname='vector';" 2>/dev/null | tr -d '[:space:]')
if [ "$pgvector" = "1" ]; then
    pass "pgvector extension is installed"
else
    fail "pgvector extension is NOT installed"
fi

# Check table count
table_count=$(docker-compose exec -T postgres psql -U trader -d trading -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>/dev/null | tr -d '[:space:]')
if [ "$table_count" -gt 20 ]; then
    pass "Database has $table_count tables"
else
    warn "Database has only $table_count tables (expected >20)"
fi

section "4. WARP Proxy Health"

# Check WARP proxy connectivity
warp_test=$(curl -s --socks5 localhost:1080 https://api.bybit.com/v5/market/time 2>/dev/null | jq -r '.retCode' 2>/dev/null || echo "FAILED")
if [ "$warp_test" = "0" ]; then
    pass "WARP proxy can reach Bybit API"
else
    fail "WARP proxy cannot reach Bybit API (retCode: $warp_test)"
fi

section "5. Prometheus Health"

# Check Prometheus targets
prom_targets=$(curl -s http://localhost:9090/api/v1/targets 2>/dev/null | jq -r '.data.activeTargets[] | "\(.labels.job): \(.health)"' 2>/dev/null || echo "FAILED")
if [ "$prom_targets" != "FAILED" ]; then
    pass "Prometheus is responding"
    echo "$prom_targets" | while read -r line; do
        job=$(echo "$line" | cut -d: -f1 | tr -d '[:space:]')
        health=$(echo "$line" | cut -d: -f2 | tr -d '[:space:]')
        if [ "$health" = "up" ]; then
            pass "  Target $job is UP"
        else
            fail "  Target $job is DOWN"
        fi
    done
else
    fail "Prometheus is not responding"
fi

section "6. Grafana Health"

# Check Grafana health
grafana_health=$(curl -s http://localhost:3000/api/health 2>/dev/null | jq -r '.database' 2>/dev/null || echo "FAILED")
if [ "$grafana_health" = "ok" ]; then
    pass "Grafana is healthy"
else
    fail "Grafana is not healthy: $grafana_health"
fi

# =============================================================================
# SUMMARY
# =============================================================================

section "SUMMARY"

echo ""
echo -e "Pass: ${GREEN}$PASS${NC}"
echo -e "Fail: ${RED}$FAIL${NC}"
echo -e "Warn: ${YELLOW}$WARN${NC}"
echo ""

if [ $FAIL -gt 0 ]; then
    echo -e "${RED}FAILED${NC}: $FAIL checks failed"
    exit 1
elif [ $WARN -gt 0 ]; then
    echo -e "${YELLOW}PASSED WITH WARNINGS${NC}: $WARN warnings"
    exit 0
else
    echo -e "${GREEN}PASSED${NC}: All checks passed"
    exit 0
fi
