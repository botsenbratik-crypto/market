"""Telegram delivery for CONTRCT mode — isolated from ticker /get."""

from __future__ import annotations

import structlog
from aiogram.types import Message

from crypto_bot.domain.contract_query import ContractQuery
from crypto_bot.services.contract_formatter import format_contract_report
from crypto_bot.services.contract_lookup import build_contract_report

logger = structlog.get_logger(__name__)


async def reply_contract(message: Message, query: ContractQuery) -> None:
    try:
        report = await build_contract_report(query)
        text = format_contract_report(report)
        await message.answer(text, disable_web_page_preview=True)
    except Exception:
        logger.exception("contract_lookup_failed", addr=query.address)
        await message.answer("❌ Error. Please try again.")
