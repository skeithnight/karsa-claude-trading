-- Performance Gate: add bucket tracking and judge metadata to crypto_positions
-- Supports the two-layer exit system (mechanical checkpoints + AI judge)
--
-- Run: docker exec -i karsa-postgres psql -U trader -d trading < db/migrations/add_performance_gate_columns.sql

ALTER TABLE crypto_positions
    ADD COLUMN IF NOT EXISTS bucket VARCHAR(20) DEFAULT 'standard',
    ADD COLUMN IF NOT EXISTS last_judgment JSONB,
    ADD COLUMN IF NOT EXISTS last_judgment_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS judge_escalated BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS dynamic_stop_pct NUMERIC(18,4);

-- Bucket constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_crypto_pos_bucket'
    ) THEN
        ALTER TABLE crypto_positions
            ADD CONSTRAINT ck_crypto_pos_bucket
            CHECK (bucket IN ('meme', 'standard', 'core'));
    END IF;
END $$;

-- Index for performance gate queries
CREATE INDEX IF NOT EXISTS idx_crypto_positions_bucket_status
    ON crypto_positions (bucket, status)
    WHERE status = 'OPEN';

COMMENT ON COLUMN crypto_positions.bucket IS 'Position bucket: meme (aggressive timeline), standard, core (patient)';
COMMENT ON COLUMN crypto_positions.last_judgment IS 'Most recent AI judge decision: {action, confidence, reason}';
COMMENT ON COLUMN crypto_positions.last_judgment_at IS 'Timestamp of last judge evaluation';
COMMENT ON COLUMN crypto_positions.judge_escalated IS 'True if escalated pass (Tier 2) was used';
COMMENT ON COLUMN crypto_positions.dynamic_stop_pct IS 'Dynamic stop-loss percentage set by AI judge or clear-win trailing';
