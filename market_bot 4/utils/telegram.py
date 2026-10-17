"""Telegram message helpers."""

from __future__ import annotations

TELEGRAM_TEXT_LIMIT = 4096


def split_message(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Split HTML report into Telegram-sized chunks on paragraph boundaries."""
    text = text.strip()
    if len(text) <= limit:
        return [text]

    blocks = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for block in blocks:
        extra = len("\n\n") if current else 0
        if current_len + extra + len(block) > limit and current:
            chunks.append("\n\n".join(current))
            current = [block]
            current_len = len(block)
            continue
        if current:
            current_len += extra + len(block)
        else:
            current_len = len(block)
        current.append(block)

    if current:
        chunks.append("\n\n".join(current))

    # Fallback: split oversized single blocks line-by-line.
    out: list[str] = []
    for chunk in chunks:
        if len(chunk) <= limit:
            out.append(chunk)
            continue
        lines = chunk.split("\n")
        part: list[str] = []
        part_len = 0
        for line in lines:
            extra = 1 if part else 0
            if part_len + extra + len(line) > limit and part:
                out.append("\n".join(part))
                part = [line]
                part_len = len(line)
            else:
                if part:
                    part_len += extra + len(line)
                else:
                    part_len = len(line)
                part.append(line)
        if part:
            out.append("\n".join(part))
    return out
