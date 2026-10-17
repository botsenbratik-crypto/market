"""Cold TREAD probe: only _build/get_report, no TREAD warmup."""

from __future__ import annotations

import asyncio
import re
import time

from market_bot.services.market import MarketService
from market_bot.utils.formatter import format_report
from market_bot.utils.http import close_session, init_session


def _plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


async def main() -> None:
    await init_session()
    svc = MarketService()
    t0 = time.perf_counter()
    rep = await svc._build("TREAD", "USDT")
    dt = time.perf_counter() - t0
    print(f"_build {dt:.3f}s")
    for e in rep.exchanges:
        print(
            " ex",
            e.key,
            "has",
            e.has_data,
            "spot",
            getattr(e.spot, "price", None),
            "to",
            getattr(e, "timed_out", False),
        )
    for c in rep.contracts:
        print(" ca", c.network, c.price)
    text = format_report(rep.symbol, rep.exchanges, rep.contracts)
    body = _plain(text)
    print(body)
    has_kraken = "Kraken" in body
    ok = (
        has_kraken
        and "MEXC" in body
        and "Hyperliquid" in body
        and bool(re.search(r"ETH[^\n]*\$", body))
        and bool(re.search(r"HYPERLIQUID[^\n]*\$", body))
        and dt <= 1.15
    )
    print(f"CHECK kraken={has_kraken} elapsed={dt:.3f} PASS={ok}")
    await close_session()


if __name__ == "__main__":
    asyncio.run(main())
