import logging

from aiogram import Router
from aiogram.types import ErrorEvent

router = Router(name="errors")


@router.errors()
async def on_error(event: ErrorEvent) -> None:
    logging.getLogger(__name__).exception("telegram_error: %s", event.exception)
