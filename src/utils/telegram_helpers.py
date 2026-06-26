"""Karsa Trading System - Telegram Formatting Helpers

Utilities for creating institutional-grade Telegram messages with
aligned <pre> tables, HTML escaping, and message chunking.
"""

import html as html_module
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup


def format_pre_table(headers: list[str], rows: list[list[str]], align_right: list[int] = None) -> str:
    """
    Formats data into an aligned ASCII table for Telegram <pre> tags.
    :param align_right: List of column indices that should be right-aligned (e.g., numbers).
    """
    if not rows:
        return "No data available."

    align_right = align_right or []

    # Calculate max width for each column
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    # Build header
    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    separator = "─" * len(header_line)

    # Build rows
    table_lines = [header_line, separator]
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            cell_str = str(cell)
            if i in align_right:
                cells.append(cell_str.rjust(col_widths[i]))
            else:
                cells.append(cell_str.ljust(col_widths[i]))
        table_lines.append("  ".join(cells))

    return "\n".join(table_lines)


def escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram."""
    return html_module.escape(str(text)) if text else ""


async def send_long_message(update: Update, text: str, parse_mode: str = "HTML", reply_markup=None):
    """Sends a message, splitting it into chunks if it exceeds Telegram's 4096 limit."""
    limit = 4000  # Leave buffer for parse tags

    if len(text) <= limit:
        if update.callback_query:
            await update.callback_query.message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        elif update.message:
            await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return

    # Simple chunking by lines
    lines = text.split('\n')
    chunks = []
    current_chunk = []
    current_len = 0

    for line in lines:
        if current_len + len(line) + 1 > limit:
            chunks.append('\n'.join(current_chunk))
            current_chunk = [line]
            current_len = len(line)
        else:
            current_chunk.append(line)
            current_len += len(line) + 1

    if current_chunk:
        chunks.append('\n'.join(current_chunk))

    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        markup = reply_markup if is_last else None
        if update.callback_query:
            await update.callback_query.message.edit_text(chunk, parse_mode=parse_mode, reply_markup=markup)
        elif update.message:
            await update.message.reply_text(chunk, parse_mode=parse_mode, reply_markup=markup)


def build_audit_keyboard(tickers: list[str]) -> InlineKeyboardMarkup:
    """Builds inline buttons for auditing specific tickers."""
    keyboard = []
    row = []
    for i, ticker in enumerate(tickers):
        row.append(InlineKeyboardButton(f"🔍 {ticker}", callback_data=f"audit_{ticker}"))
        if (i + 1) % 3 == 0:  # 3 buttons per row
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


def build_nav_keyboard(buttons: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Builds inline keyboard from a list of rows of (text, callback_data) tuples."""
    keyboard = []
    for row in buttons:
        keyboard.append([InlineKeyboardButton(text, callback_data=data) for text, data in row])
    return InlineKeyboardMarkup(keyboard)
