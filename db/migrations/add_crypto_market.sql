-- Migration: Add CRYPTO market to all CHECK constraints
-- Run this against the trading database after deployment

BEGIN;

-- portfolio_state: add CRYPTO to market constraint
ALTER TABLE portfolio_state DROP CONSTRAINT IF EXISTS ck_portfolio_market;
ALTER TABLE portfolio_state ADD CONSTRAINT ck_portfolio_market
    CHECK (market IN ('IDX', 'US', 'ETF', 'CRYPTO'));

-- signals: add CRYPTO to market constraint
ALTER TABLE signals DROP CONSTRAINT IF EXISTS ck_signal_market;
ALTER TABLE signals ADD CONSTRAINT ck_signal_market
    CHECK (market IN ('IDX', 'US', 'ETF', 'CRYPTO'));

-- paper_positions: add CRYPTO to market constraint
ALTER TABLE paper_positions DROP CONSTRAINT IF EXISTS ck_paper_market;
ALTER TABLE paper_positions ADD CONSTRAINT ck_paper_market
    CHECK (market IN ('IDX', 'US', 'ETF', 'CRYPTO'));

-- closed_paper_trades: add CRYPTO to market constraint
ALTER TABLE closed_paper_trades DROP CONSTRAINT IF EXISTS ck_closed_market;
ALTER TABLE closed_paper_trades ADD CONSTRAINT ck_closed_market
    CHECK (market IN ('IDX', 'US', 'ETF', 'CRYPTO'));

-- ohlcv_cache: add CRYPTO to market constraint
ALTER TABLE ohlcv_cache DROP CONSTRAINT IF EXISTS ck_ohlcv_market;
ALTER TABLE ohlcv_cache ADD CONSTRAINT ck_ohlcv_market
    CHECK (market IN ('IDX', 'US', 'ETF', 'CRYPTO'));

-- audit_logs: add CRYPTO_AGENT to component constraint
ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS ck_audit_component;
ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_component
    CHECK (component IN ('ORCHESTRATOR', 'IDX_AGENT', 'US_AGENT', 'ETF_AGENT', 'CRYPTO_AGENT', 'RISK_AGENT', 'TELEGRAM', 'BROKER'));

COMMIT;
