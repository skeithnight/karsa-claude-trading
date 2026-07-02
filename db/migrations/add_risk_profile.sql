-- Migration: Add Risk Profile Support
-- Run after db/init.sql

-- 1. Risk profile audit trail (immutable)
CREATE TABLE IF NOT EXISTS risk_profile_audit (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    previous_profile VARCHAR(20) NOT NULL,
    new_profile VARCHAR(20) NOT NULL,
    changed_by VARCHAR(100) NOT NULL,
    reason TEXT,
    CHECK (previous_profile IN ('conservative', 'semi_aggressive', 'aggressive')),
    CHECK (new_profile IN ('conservative', 'semi_aggressive', 'aggressive')),
    CHECK (previous_profile != new_profile)
);

CREATE INDEX IF NOT EXISTS idx_risk_profile_audit_ts ON risk_profile_audit(timestamp DESC);

COMMENT ON TABLE risk_profile_audit IS 'Immutable audit trail of risk profile changes';

-- 2. Add risk profile tracking to signals table
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS risk_profile_at_generation VARCHAR(20) DEFAULT 'conservative';

CREATE INDEX IF NOT EXISTS idx_signals_risk_profile ON signals(risk_profile_at_generation);
