-- Scale-In Pyramiding: add scale_in_taken flag to crypto_positions
-- Prevents double-pyramiding on the same position
--
-- Run: docker exec -i karsa-postgres psql -U trader -d trading < db/migrations/add_scale_in_column.sql

ALTER TABLE crypto_positions
    ADD COLUMN IF NOT EXISTS scale_in_taken BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN crypto_positions.scale_in_taken IS 'True if a pyramid add-on has been executed for this position';
