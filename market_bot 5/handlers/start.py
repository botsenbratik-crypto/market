from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="start")


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 <b>Market Bot</b>\n\n"
        "Send a ticker: <code>btc</code>, <code>eth</code>, <code>sol</code>\n"
        "— 12 exchanges, contracts, D/W",
    )
