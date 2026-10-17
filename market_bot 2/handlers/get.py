"""Ticker lookup — plain text (btc, sol) or leftover /get."""

from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from market_bot.handlers.contract import reply_contract
from market_bot.parsers.contract_query import parse_contract_query
from market_bot.services.market import MarketService
from market_bot.utils.telegram import split_message

logger = logging.getLogger(__name__)
router = Router(name="get")


def setup_get_router(service: MarketService) -> Router:
    async def _lookup(message: Message, raw: str) -> None:
        token = raw.strip().split()[0] if raw else ""
        contract = parse_contract_query(token)
        if contract is not None:
            await reply_contract(message, contract)
            return

        symbol = token.upper().split("-")[0]
        if not symbol:
            await message.answer("Send a ticker: <code>btc</code>")
            return
        t0 = time.perf_counter()
        try:
            text = None
            async for chunk, done in service.iter_report(symbol):
                if done:
                    text = chunk
            t_build = int((time.perf_counter() - t0) * 1000)
            if not text:
                await message.answer("❌ Error. Try again.")
                return
            chunks = split_message(text)
            total = len(chunks)
            for i, part in enumerate(chunks):
                if total > 1:
                    if i == 0:
                        part = f"{part}\n\n<i>— 1/{total} —</i>"
                    else:
                        part = f"<i>{symbol} — continued ({i + 1}/{total})</i>\n\n{part}"
                await message.answer(part, disable_web_page_preview=True)
            logger.info(
                "get_ok %s build=%dms total=%dms",
                symbol,
                t_build,
                int((time.perf_counter() - t0) * 1000),
            )
        except Exception:
            logger.exception("get_fail %s", symbol)
            await message.answer("❌ Error. Try again.")

    @router.message(Command("get"))
    async def cmd_get(message: Message, command: CommandObject) -> None:
        if not command.args:
            await message.answer("Send a ticker: <code>btc</code>")
            return
        await _lookup(message, command.args)

    @router.message(F.text)
    async def on_ticker(message: Message) -> None:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return
        await _lookup(message, text)

    return router
