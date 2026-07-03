-- Migration: Add autonomous session audit table

BEGIN;

CREATE TABLE IF NOT EXISTS crypto_auto_sessions (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    config JSONB NOT NULL DEFAULT '{}',
    starting_equity NUMERIC(18, 2),
    ending_equity NUMERIC(18, 2),
    total_trades INT DEFAULT 0,
    wins INT DEFAULT 0,
    losses INT DEFAULT 0,
    realized_pnl NUMERIC(18, 2) DEFAULT 0,
    unrealized_pnl NUMERIC(18, 2) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'RUNNING',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_sessions_status ON crypto_auto_sessions(status);
CREATE INDEX IF NOT EXISTS idx_auto_sessions_started ON crypto_auto_sessions(started_at);

COMMIT;
