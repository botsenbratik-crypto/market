"""Start and help handlers."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="start")


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 <b>Crypto Price Bot</b>\n\n"
        "Send a ticker: <code>sol</code>, <code>btc</code>, <code>ethusdt</code>\n"
        "— spot &amp; futures, funding, D/W, contracts\n\n"
        "Repeat within 60s — served from cache.",
    )
