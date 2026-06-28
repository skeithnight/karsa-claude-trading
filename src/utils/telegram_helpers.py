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


def _split_html_safe(text: str, limit: int) -> list[str]:
    """Split text into chunks respecting HTML tag boundaries.

    Tracks open <pre>, <b>, <i>, <code> tags across chunk boundaries.
    Closes open tags at end of chunk, reopens them at start of next chunk.
    """
    import re
    lines = text.split('\n')
    chunks = []
    current_chunk = []
    current_len = 0
    open_tags = []  # stack of open tags

    tag_pattern = re.compile(r'<(/?)(pre|b|i|code|u|s|a)([^>]*)>')

    for line in lines:
        if current_len + len(line) + 1 > limit and current_chunk:
            # Close any open tags at chunk boundary
            closing = ""
            for tag in reversed(open_tags):
                closing += f"</{tag}>"
            chunks.append('\n'.join(current_chunk) + closing)

            # Reopen tags at start of next chunk
            reopening = ""
            for tag in open_tags:
                reopening += f"<{tag}>"
            current_chunk = [reopening + line] if reopening else [line]
            current_len = len(current_chunk[0])
        else:
            current_chunk.append(line)
            current_len += len(line) + 1

        # Track open/close tags in this line
        for match in tag_pattern.finditer(line):
            is_close = match.group(1) == '/'
            tag_name = match.group(2)
            if tag_name in ('pre', 'b', 'i', 'code', 'u', 's'):
                if is_close and tag_name in open_tags:
                    open_tags.remove(tag_name)
                elif not is_close:
                    open_tags.append(tag_name)

    if current_chunk:
        chunks.append('\n'.join(current_chunk))

    return chunks


async def send_long_message(update: Update, text: str, parse_mode: str = "HTML", reply_markup=None):
    """Sends a message, splitting it into chunks if it exceeds Telegram's 4096 limit."""
    limit = 4000  # Leave buffer for parse tags

    if len(text) <= limit:
        if update.callback_query:
            await update.callback_query.message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        elif update.message:
            await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return

    chunks = _split_html_safe(text, limit)

    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        markup = reply_markup if is_last else None
        try:
            if update.callback_query:
                await update.callback_query.message.edit_text(chunk, parse_mode=parse_mode, reply_markup=markup)
            elif update.message:
                await update.message.reply_text(chunk, parse_mode=parse_mode, reply_markup=markup)
        except Exception:
            # Fallback: send as plain text if HTML parsing fails
            plain = re.sub(r'<[^>]+>', '', chunk)
            if update.callback_query:
                await update.callback_query.message.edit_text(plain, reply_markup=markup)
            elif update.message:
                await update.message.reply_text(plain, reply_markup=markup)



def build_nav_keyboard(buttons: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Builds inline keyboard from a list of rows of (text, callback_data) tuples."""
    keyboard = []
    for row in buttons:
        keyboard.append([InlineKeyboardButton(text, callback_data=data) for text, data in row])
    return InlineKeyboardMarkup(keyboard)
