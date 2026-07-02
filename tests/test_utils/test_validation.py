"""Tests for src/utils/validation.py"""

import pytest
from src.utils.validation import validate_ticker, validate_market, sanitize_for_prompt


# ── validate_ticker ────────────────────────────────────────────────

class TestValidateTicker:
    """validate_ticker matches ^[A-Z0-9.]{1,20}$"""

    def test_uppercase_letters(self):
        assert validate_ticker("BBCA") is True

    def test_numbers(self):
        assert validate_ticker("123") is True

    def test_dots(self):
        assert validate_ticker("BRK.A") is True

    def test_alphanumeric_mixed(self):
        assert validate_ticker("ETH3L") is True

    def test_max_length_20(self):
        assert validate_ticker("A" * 20) is True

    def test_single_char(self):
        assert validate_ticker("A") is True

    def test_all_dots(self):
        assert validate_ticker("...") is True

    # --- invalid ---

    def test_lowercase_rejected(self):
        assert validate_ticker("bbca") is False

    def test_mixed_case_rejected(self):
        assert validate_ticker("Bbca") is False

    def test_space_rejected(self):
        assert validate_ticker("BBCA IJ") is False

    def test_hyphen_rejected(self):
        assert validate_ticker("BRK-A") is False

    def test_special_chars_rejected(self):
        for ch in "!@#$%^&*()+=[]{}|\\:;\"'<>,?/":
            assert validate_ticker(f"A{ch}B") is False, f"char {ch!r} should be rejected"

    def test_empty_string_rejected(self):
        assert validate_ticker("") is False

    def test_exceeds_20_chars(self):
        assert validate_ticker("A" * 21) is False

    def test_underscore_rejected(self):
        assert validate_ticker("BTC_USDT") is False


# ── validate_market ────────────────────────────────────────────────

class TestValidateMarket:
    """validate_market checks membership in {'IDX', 'US', 'ETF'}"""

    @pytest.mark.parametrize("market", ["IDX", "US", "ETF"])
    def test_valid_markets(self, market):
        assert validate_market(market) is True

    def test_crypto_rejected(self):
        assert validate_market("CRYPTO") is False

    def test_empty_string_rejected(self):
        assert validate_market("") is False

    def test_lowercase_idx_rejected(self):
        assert validate_market("idx") is False

    def test_lowercase_us_rejected(self):
        assert validate_market("us") is False

    def test_lowercase_etf_rejected(self):
        assert validate_market("etf") is False

    def test_partial_match_rejected(self):
        assert validate_market("I") is False
        assert validate_market("ID") is False
        assert validate_market("IDXX") is False

    def test_whitespace_rejected(self):
        assert validate_market(" IDX") is False
        assert validate_market("IDX ") is False
        assert validate_market(" IDX ") is False


# ── sanitize_for_prompt ────────────────────────────────────────────

class TestSanitizeForPrompt:
    """sanitize_for_prompt keeps alphanumeric + '.-_', truncates to max_len"""

    def test_alphanumeric_passthrough(self):
        assert sanitize_for_prompt("BBCA") == "BBCA"

    def test_allowed_specials_preserved(self):
        assert sanitize_for_prompt("BRK.A") == "BRK.A"
        assert sanitize_for_prompt("a-b") == "a-b"
        assert sanitize_for_prompt("a_b") == "a_b"

    def test_strips_disallowed_chars(self):
        assert sanitize_for_prompt("BBCA IJ") == "BBCAIJ"
        assert sanitize_for_prompt("a@b#c") == "abc"
        assert sanitize_for_prompt("a!b$c%") == "abc"
        assert sanitize_for_prompt("a&b(c)") == "abc"

    def test_truncation_default_20(self):
        long = "A" * 50
        assert len(sanitize_for_prompt(long)) == 20

    def test_truncation_custom_max_len(self):
        result = sanitize_for_prompt("ABCDEFGHIJK", max_len=5)
        assert result == "ABCDE"

    def test_empty_string(self):
        assert sanitize_for_prompt("") == ""

    def test_all_special_chars_stripped(self):
        assert sanitize_for_prompt("!@#$%^&*()") == ""

    def test_numbers_preserved(self):
        assert sanitize_for_prompt("12345") == "12345"

    def test_max_len_one(self):
        assert sanitize_for_prompt("ABCD", max_len=1) == "A"

    def test_max_len_zero(self):
        assert sanitize_for_prompt("ABCD", max_len=0) == ""

    def test_stripping_then_truncation(self):
        # "A B C D E F" strips spaces -> "ABCDEF", truncated to 3 -> "ABC"
        assert sanitize_for_prompt("A B C D E F", max_len=3) == "ABC"

    def test_mixed_content(self):
        assert sanitize_for_prompt("BTC-USDT_perp.v2") == "BTC-USDT_perp.v2"
