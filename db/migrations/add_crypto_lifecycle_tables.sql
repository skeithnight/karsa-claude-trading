-- Migration: Add crypto lifecycle management tables and extend crypto_positions
-- Phase 1 of QWEN_DESIGN_1.md

BEGIN;

-- 1. Extend crypto_positions with lifecycle management columns
ALTER TABLE crypto_positions ADD COLUMN IF NOT EXISTS trailing_stop_price NUMERIC(18, 4);
ALTER TABLE crypto_positions ADD COLUMN IF NOT EXISTS highest_price NUMERIC(18, 4);
ALTER TABLE crypto_positions ADD COLUMN IF NOT EXISTS entry_funding_rate NUMERIC(12, 8);
ALTER TABLE crypto_positions ADD COLUMN IF NOT EXISTS regime_at_entry VARCHAR(20);
ALTER TABLE crypto_positions ADD COLUMN IF NOT EXISTS signal_source VARCHAR(50);
ALTER TABLE crypto_positions ADD COLUMN IF NOT EXISTS partial_exits_taken INT DEFAULT 0;
ALTER TABLE crypto_positions ADD COLUMN IF NOT EXISTS last_management_check TIMESTAMP;

-- 2. Trailing stop audit trail
CREATE TABLE IF NOT EXISTS crypto_trailing_stops (
    id BIGSERIAL PRIMARY KEY,
    position_id BIGINT NOT NULL REFERENCES crypto_positions(id),
    old_price NUMERIC(18, 4),
    new_price NUMERIC(18, 4) NOT NULL,
    trigger_price NUMERIC(18, 4),
    reason VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trailing_stops_position ON crypto_trailing_stops(position_id);
CREATE INDEX IF NOT EXISTS idx_trailing_stops_created ON crypto_trailing_stops(created_at);

-- 3. Partial exit history
CREATE TABLE IF NOT EXISTS crypto_partial_exits (
    id BIGSERIAL PRIMARY KEY,
    position_id BIGINT NOT NULL REFERENCES crypto_positions(id),
    exit_pct INT NOT NULL,
    exit_price NUMERIC(18, 4) NOT NULL,
    exit_qty NUMERIC(18, 8) NOT NULL,
    pnl_usdt NUMERIC(18, 4),
    reason VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_partial_exits_position ON crypto_partial_exits(position_id);

-- 4. Circuit breaker event log
CREATE TABLE IF NOT EXISTS crypto_circuit_breaker_events (
    id BIGSERIAL PRIMARY KEY,
    breaker_type VARCHAR(20) NOT NULL CHECK (breaker_type IN ('DAILY_DD', 'VOLATILITY', 'CORRELATION')),
    severity VARCHAR(10) NOT NULL CHECK (severity IN ('WARNING', 'HALT')),
    details JSONB,
    triggered_at TIMESTAMP DEFAULT NOW(),
    resolved_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cb_events_type ON crypto_circuit_breaker_events(breaker_type);
CREATE INDEX IF NOT EXISTS idx_cb_events_triggered ON crypto_circuit_breaker_events(triggered_at);

-- 5. Reconciliation log
CREATE TABLE IF NOT EXISTS crypto_reconciliation_logs (
    id BIGSERIAL PRIMARY KEY,
    position_id BIGINT,
    drift_type VARCHAR(20) NOT NULL CHECK (drift_type IN ('PHANTOM', 'MISSING', 'SIZE_DRIFT')),
    exchange_state JSONB,
    db_state JSONB,
    resolution VARCHAR(50),
    detected_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_recon_logs_type ON crypto_reconciliation_logs(drift_type);
CREATE INDEX IF NOT EXISTS idx_recon_logs_detected ON crypto_reconciliation_logs(detected_at);

COMMIT;
