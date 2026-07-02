-- Migration: Add reasoning_traces and strategy_recommendations tables
-- Phase 2 + Phase 4 of the Dynamic Crypto Research Plan
-- Run this against the trading database after deployment

BEGIN;

-- Reasoning Traces (Phase 2: Agent reasoning capture)
CREATE TABLE IF NOT EXISTS reasoning_traces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id UUID,
    agent_name VARCHAR(50) NOT NULL,
    ticker VARCHAR(20),
    market VARCHAR(10) CHECK (market IN ('IDX', 'US', 'ETF', 'CRYPTO')),
    system_prompt TEXT NOT NULL,
    user_prompt TEXT NOT NULL,
    tools_used JSONB,
    tool_results JSONB,
    llm_response TEXT,
    reasoning_extracted TEXT,
    strategy_used VARCHAR(100),
    regime_at_time VARCHAR(20),
    confidence_score INT,
    iterations INT DEFAULT 1,
    model_used VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_traces_ticker ON reasoning_traces(ticker, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_signal ON reasoning_traces(signal_id);

-- Strategy Recommendations (Phase 4: Self-improvement)
CREATE TABLE IF NOT EXISTS strategy_recommendations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_type VARCHAR(50) NOT NULL,
    priority VARCHAR(10) NOT NULL CHECK (priority IN ('HIGH', 'MEDIUM', 'LOW')),
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    expected_impact VARCHAR(200),
    status VARCHAR(20) DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'APPLIED')),
    metrics_snapshot JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    applied_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_recommendations_status ON strategy_recommendations(status);
CREATE INDEX IF NOT EXISTS idx_recommendations_priority ON strategy_recommendations(priority);

COMMIT;
