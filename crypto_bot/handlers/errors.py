"""Global error handler."""

from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent

logger = structlog.get_logger(__name__)
router = Router(name="errors")


@router.errors()
async def on_error(event: ErrorEvent) -> None:
    exc = event.exception

    # Swallow harmless Telegram errors (deleted message, not modified, etc.)
    if isinstance(exc, TelegramBadRequest):
        err = str(exc).lower()
        if any(s in err for s in ("not modified", "not found", "message to edit")):
            logger.debug("telegram_bad_request_ignored", error=str(exc)[:120])
            return
        logger.warning("telegram_bad_request", error=str(exc)[:120])
        return

    logger.exception("telegram_error", error=str(exc))

    # Try to notify the user
    try:
        update = event.update
        msg = getattr(update, "message", None) or getattr(update, "callback_query", None)
        if msg is not None:
            target = getattr(msg, "answer", None)
            if target:
                await target("⚠️ Something went wrong. Try again.")
    except Exception:
        pass
