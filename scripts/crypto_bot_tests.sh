#!/bin/bash
# =============================================================================
# Karsa Crypto Bot Functional Tests
# =============================================================================
# Usage: ./scripts/crypto_bot_tests.sh
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
# CRYPTO BOT HEALTH CHECKS
# =============================================================================

section "1. Health Endpoint"

health_response=$(curl -s http://localhost:8444/health 2>/dev/null || echo "FAILED")
if echo "$health_response" | jq -e '.status' > /dev/null 2>&1; then
    status=$(echo "$health_response" | jq -r '.status')
    if [ "$status" = "healthy" ]; then
        pass "Health endpoint returns healthy"
    else
        fail "Health endpoint returns: $status"
    fi
else
    fail "Health endpoint not responding: $health_response"
fi

section "2. Metrics Endpoint"

metrics_response=$(curl -s http://localhost:8444/metrics 2>/dev/null | head -20 || echo "FAILED")
if echo "$metrics_response" | grep -q "# HELP"; then
    pass "Metrics endpoint returns Prometheus format"
else
    fail "Metrics endpoint not returning Prometheus format"
fi

section "3. Positions Endpoint"

positions_response=$(curl -s http://localhost:8444/positions 2>/dev/null || echo "FAILED")
if echo "$positions_response" | jq -e '.' > /dev/null 2>&1; then
    position_count=$(echo "$positions_response" | jq 'length')
    pass "Positions endpoint returns valid JSON ($position_count positions)"
else
    fail "Positions endpoint not returning valid JSON"
fi

section "4. Portfolio Endpoint"

portfolio_response=$(curl -s http://localhost:8444/portfolio 2>/dev/null || echo "FAILED")
if echo "$portfolio_response" | jq -e '.equity' > /dev/null 2>&1; then
    equity=$(echo "$portfolio_response" | jq -r '.equity')
    pass "Portfolio endpoint returns equity: $equity"
else
    fail "Portfolio endpoint not returning valid data"
fi

section "5. Kill Switch"

# Activate kill switch
activate_response=$(curl -s -X POST http://localhost:8444/kill-switch/activate \
    -H "Content-Type: application/json" \
    -d '{"reason": "integration_test"}' 2>/dev/null || echo "FAILED")

if echo "$activate_response" | jq -e '.active' > /dev/null 2>&1; then
    active=$(echo "$activate_response" | jq -r '.active')
    if [ "$active" = "true" ]; then
        pass "Kill switch activated successfully"
    else
        fail "Kill switch activation failed"
    fi
else
    fail "Kill switch endpoint not responding"
fi

# Check kill switch status
sleep 1
status_response=$(curl -s http://localhost:8444/kill-switch/status 2>/dev/null || echo "FAILED")
if echo "$status_response" | jq -e '.active' > /dev/null 2>&1; then
    active=$(echo "$status_response" | jq -r '.active')
    if [ "$active" = "true" ]; then
        pass "Kill switch status shows active"
    else
        fail "Kill switch status shows inactive"
    fi
else
    fail "Kill switch status endpoint not responding"
fi

# Deactivate kill switch
deactivate_response=$(curl -s -X POST http://localhost:8444/kill-switch/deactivate 2>/dev/null || echo "FAILED")
if echo "$deactivate_response" | jq -e '.active' > /dev/null 2>&1; then
    active=$(echo "$deactivate_response" | jq -r '.active')
    if [ "$active" = "false" ]; then
        pass "Kill switch deactivated successfully"
    else
        fail "Kill switch deactivation failed"
    fi
else
    fail "Kill switch deactivation endpoint not responding"
fi

section "6. Universe Scanner"

# Trigger universe refresh
refresh_response=$(curl -s -X POST http://localhost:8444/universe/refresh 2>/dev/null || echo "FAILED")
if echo "$refresh_response" | jq -e '.' > /dev/null 2>&1; then
    pass "Universe refresh triggered"
else
    warn "Universe refresh endpoint not responding (may not be implemented)"
fi

section "7. Reconciliation"

# Trigger reconciliation
recon_response=$(curl -s -X POST http://localhost:8444/reconciliation/run 2>/dev/null || echo "FAILED")
if echo "$recon_response" | jq -e '.' > /dev/null 2>&1; then
    pass "Reconciliation triggered"
else
    warn "Reconciliation endpoint not responding (may not be implemented)"
fi

section "8. Recent Logs"

# Check for errors in recent logs
error_count=$(docker-compose logs --tail=100 karsa-crypto-bot 2>/dev/null | grep -i "error\|exception\|critical" | wc -l || echo "0")
if [ "$error_count" -eq 0 ]; then
    pass "No errors in last 100 log lines"
elif [ "$error_count" -lt 5 ]; then
    warn "Found $error_count errors in last 100 log lines"
else
    fail "Found $error_count errors in last 100 log lines"
fi

# Check for universe refresh logs
universe_logs=$(docker-compose logs --tail=50 karsa-crypto-bot 2>/dev/null | grep "universe_refresh" | wc -l || echo "0")
if [ "$universe_logs" -gt 0 ]; then
    pass "Universe refresh logs found ($universe_logs entries)"
else
    warn "No universe refresh logs found"
fi

# Check for risk gate logs
risk_logs=$(docker-compose logs --tail=50 karsa-crypto-bot 2>/dev/null | grep "risk_gate" | wc -l || echo "0")
if [ "$risk_logs" -gt 0 ]; then
    pass "Risk gate logs found ($risk_logs entries)"
else
    warn "No risk gate logs found"
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
