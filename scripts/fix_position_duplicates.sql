-- Fix position duplication: Add unique constraint and cleanup duplicates

-- 1. Add unique constraint to prevent future duplicates
-- This constraint ensures only one OPEN position per ticker+side combination
CREATE UNIQUE INDEX IF NOT EXISTS idx_crypto_positions_ticker_side_open 
ON crypto_positions (ticker, side) 
WHERE status = 'OPEN';

-- 2. For existing duplicates, keep only the most recent one per ticker+side
-- Mark older duplicates as CLOSED
WITH ranked AS (
    SELECT id, 
           ROW_NUMBER() OVER (
               PARTITION BY ticker, side 
               ORDER BY opened_at DESC, id DESC
           ) as rn
    FROM crypto_positions 
    WHERE status = 'OPEN'
)
UPDATE crypto_positions 
SET status = 'CLOSED'
FROM ranked 
WHERE crypto_positions.id = ranked.id 
  AND ranked.rn > 1;

-- 3. Verify cleanup
SELECT ticker, side, COUNT(*) as open_count 
FROM crypto_positions 
WHERE status = 'OPEN' 
GROUP BY ticker, side 
HAVING COUNT(*) > 1;
