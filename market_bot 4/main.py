"""Entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from market_bot.config import get_settings
from market_bot.handlers import errors, start
from market_bot.handlers.get import setup_get_router
from market_bot.services.market import MarketService
from market_bot.utils.http import init_session
from market_bot.utils.keepalive import keepalive_loop


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    await init_session()
    service = MarketService()
    await service.startup()

    keepalive_task = asyncio.create_task(keepalive_loop())

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(setup_get_router(service))
    dp.include_router(errors.router)

    print("✅ Market Bot ready — send a ticker (btc, sol)", flush=True)
    try:
        await dp.start_polling(bot)
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass
    asyncio.run(main())
