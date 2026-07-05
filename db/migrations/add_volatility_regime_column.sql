-- Migration: Add missing columns to crypto_regime_history
-- Fixes: asyncpg.exceptions.UndefinedColumnError in regime_perf_query
-- Columns added: volatility_regime, size_multiplier

BEGIN;

ALTER TABLE crypto_regime_history ADD COLUMN IF NOT EXISTS volatility_regime VARCHAR(20);
ALTER TABLE crypto_regime_history ADD COLUMN IF NOT EXISTS size_multiplier NUMERIC(3,2);

COMMIT;
