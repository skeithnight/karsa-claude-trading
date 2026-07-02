-- PGVector Memory: trade history embeddings for RAG context
-- Requires pgvector extension (use pgvector/pgvector:pg15 Docker image)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS trade_memory (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    regime VARCHAR(30) NOT NULL,
    strategy VARCHAR(100),
    trade_thesis TEXT NOT NULL,
    outcome VARCHAR(20) NOT NULL CHECK (outcome IN ('WIN', 'LOSS', 'BREAKEVEN')),
    pnl_pct DECIMAL(10, 2),
    reasoning TEXT,
    embedding vector(384),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trade_memory_ticker ON trade_memory(ticker);
CREATE INDEX IF NOT EXISTS idx_trade_memory_regime ON trade_memory(regime);
CREATE INDEX IF NOT EXISTS idx_trade_memory_embedding ON trade_memory USING hnsw (embedding vector_cosine_ops);
