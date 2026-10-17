"""Telegram delivery for CONTRCT mode."""

from __future__ import annotations

import logging

from aiogram.types import Message

from market_bot.parsers.contract_query import ContractQuery
from market_bot.services.contract_lookup import build_contract_report
from market_bot.services.contract_report import format_contract_report
from market_bot.utils.telegram import split_message

logger = logging.getLogger(__name__)


async def reply_contract(message: Message, query: ContractQuery) -> None:
    try:
        report = await build_contract_report(query)
        text = await format_contract_report(report)
        for part in split_message(text):
            await message.answer(part, disable_web_page_preview=True)
    except Exception:
        logger.exception("contract_lookup_failed %s", query.address)
        await message.answer("❌ Error. Try again.")
