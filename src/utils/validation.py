"""Karsa Trading System - Shared Input Validation"""

import re

VALID_MARKETS = {"IDX", "US", "ETF"}
TICKER_PATTERN = re.compile(r'^[A-Z0-9.]{1,20}$')


def validate_ticker(ticker: str) -> bool:
    """Validate ticker format — alphanumeric + dots, max 20 chars."""
    return bool(TICKER_PATTERN.match(ticker))


def validate_market(market: str) -> bool:
    """Validate market is one of the allowed values."""
    return market in VALID_MARKETS


def sanitize_for_prompt(text: str, max_len: int = 20) -> str:
    """Sanitize user input before including in LLM prompts."""
    return ''.join(c for c in text if c.isalnum() or c in '.-_')[:max_len]
