-- Migration: Add Dynamic Universe History
-- Tracks each universe regeneration for audit and analysis

CREATE TABLE IF NOT EXISTS universe_history (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    universe_json JSONB NOT NULL,
    coin_count INT NOT NULL,
    selection_criteria TEXT,
    risk_profile VARCHAR(20) DEFAULT 'conservative',
    refresh_duration_ms INT
);

CREATE INDEX IF NOT EXISTS idx_universe_history_ts ON universe_history(timestamp DESC);

COMMENT ON TABLE universe_history IS 'Audit trail of dynamic crypto universe selections';
