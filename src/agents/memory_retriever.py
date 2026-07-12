from __future__ import annotations

"""Karsa Trading System — Trade Memory Retriever (RAG)

Uses pgvector to find the 3 most similar past trades for a given
ticker and regime, injecting outcomes into the LLM prompt so the
agent learns from its own history.

Flow:
  CryptoAnalyst calls get_relevant_trade_memory(ticker, regime) →
  Returns formatted string injected into system prompt.
"""

from src.utils.logging import get_logger

logger = get_logger("memory_retriever")


async def get_relevant_trade_memory(ticker: str, regime: str, limit: int = 3) -> str:
    """Retrieve the most similar past trades for context injection.

    Returns formatted string for LLM prompt, or empty string if no data.
    """
    try:
        from src.models.database import async_session
        from sqlalchemy import text

        context_text = f"{ticker} in {regime} regime trading perpetual"
        embedding = _generate_embedding(context_text)

        if embedding is None:
            return ""

        async with async_session() as session:
            result = await session.execute(
                text("""
                    SELECT ticker, regime, trade_thesis, outcome, pnl_pct, reasoning,
                           mfe_pct, exit_reason, fees_usdt, hold_duration_hours
                    FROM trade_memory
                    WHERE ticker = :ticker AND regime = :regime
                    ORDER BY embedding <=> :embedding::vector
                    LIMIT :limit
                """),
                {"ticker": ticker, "regime": regime, "embedding": str(embedding), "limit": limit},
            )
            rows = result.fetchall()

        if not rows:
            return ""

        lines = ["PAST SIMILAR TRADES (learn from these):"]
        for row in rows:
            emoji = "✅" if row.outcome == "WIN" else "❌" if row.outcome == "LOSS" else "➖"
            pnl_str = f"{row.pnl_pct:+.1f}%" if row.pnl_pct else "N/A"
            exit_str = f" | Exit: {row.exit_reason}" if row.exit_reason else ""
            mfe_str = f" | MFE: {row.mfe_pct:+.1f}%" if row.mfe_pct else ""
            hold_str = f" | Hold: {row.hold_duration_hours:.1f}h" if row.hold_duration_hours else ""
            lines.append(f"- {emoji} {row.trade_thesis} | Result: {row.outcome} ({pnl_str}){exit_str}{mfe_str}{hold_str}")
            if row.reasoning:
                lines.append(f"  Lesson: {row.reasoning[:200]}")

        return "\n".join(lines)

    except Exception as e:
        logger.warning("memory_retrieval_failed", error=str(e))
        return ""


async def store_trade_memory(
    ticker: str,
    regime: str,
    strategy: str,
    thesis: str,
    outcome: str,
    pnl_pct: float,
    reasoning: str = "",
    mfe_pct: float = None,
    exit_reason: str = None,
    fees_usdt: float = 0.0,
    hold_duration_hours: float = None,
    regime_at_entry: str = None,
) -> None:
    """Store a completed trade in the memory database."""
    try:
        from src.models.database import async_session
        from sqlalchemy import text

        context_text = f"{ticker} in {regime} regime trading perpetual"
        embedding = _generate_embedding(context_text)

        async with async_session() as session:
            await session.execute(
                text("""
                    INSERT INTO trade_memory (
                        ticker, regime, strategy, trade_thesis, outcome, pnl_pct,
                        reasoning, embedding, mfe_pct, exit_reason, fees_usdt,
                        hold_duration_hours, regime_at_entry
                    )
                    VALUES (
                        :ticker, :regime, :strategy, :thesis, :outcome, :pnl,
                        :reasoning, CAST(:embedding AS vector), :mfe_pct, :exit_reason,
                        :fees_usdt, :hold_duration_hours, :regime_at_entry
                    )
                """),
                {
                    "ticker": ticker, "regime": regime, "strategy": strategy,
                    "thesis": thesis, "outcome": outcome, "pnl": pnl_pct,
                    "reasoning": reasoning,
                    "embedding": str(embedding) if embedding else None,
                    "mfe_pct": mfe_pct,
                    "exit_reason": exit_reason,
                    "fees_usdt": fees_usdt or 0.0,
                    "hold_duration_hours": hold_duration_hours,
                    "regime_at_entry": regime_at_entry,
                },
            )
            await session.commit()
            logger.info("trade_memory_stored", ticker=ticker, outcome=outcome)
    except Exception as e:
        logger.warning("trade_memory_store_failed", error=str(e))


def _generate_embedding(text: str) -> list[float] | None:
    """Generate embedding vector. Uses sentence-transformers if available."""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        return model.encode(text).tolist()
    except ImportError:
        return None
    except Exception:
        return None
