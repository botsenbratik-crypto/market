"""BingX API symbols vs user-facing tickers (displayName).

Several contracts share displayName MEME-USDT. Native symbol always wins:
MEME-USDT is Memecoin (~$0.00055); AMEMECOIN-USDT is A Meme Coin (~$0.09).
"""

from __future__ import annotations

import asyncio

from market_bot.utils.cache import TtlCache
from market_bot.utils.http import get_json

_cache = TtlCache[tuple[dict[str, str], dict[str, set[str]]]](3600.0)
_load_lock = asyncio.Lock()
_load_task: asyncio.Task | None = None


def _split_pair(raw: str) -> tuple[str, str] | None:
    text = str(raw or "").upper()
    if "-" not in text:
        return None
    base, quote = text.split("-", 1)
    if not base or not quote:
        return None
    return base, quote


async def _load() -> tuple[dict[str, str], dict[str, set[str]]]:
    hit = _cache.get("all")
    if hit is not None:
        return hit
    async with _load_lock:
        hit = _cache.get("all")
        if hit is not None:
            return hit

        pair_map: dict[str, str] = {}
        coin_map: dict[str, set[str]] = {}

        def remember_coin(display_base: str, asset: str) -> None:
            keys = {display_base, asset} if asset else {display_base}
            for key in keys:
                if not key:
                    continue
                coin_map.setdefault(key, set()).add(asset or display_base)

        swap_j = await get_json(
            "https://open-api.bingx.com/openApi/swap/v2/quote/contracts",
            timeout=1.5,
        )
        for row in (swap_j or {}).get("data") or []:
            if not isinstance(row, dict):
                continue
            if row.get("status") not in (1, "1") and row.get("apiStateOpen") != "true":
                continue
            sym = str(row.get("symbol") or "")
            display = str(row.get("displayName") or "")
            asset = str(row.get("asset") or "").upper()
            native = _split_pair(sym)
            shown = _split_pair(display)
            if not native:
                continue
            nb, nq = native
            pair_map[f"{nb}:{nq}"] = sym
            if shown:
                pair_map.setdefault(f"{shown[0]}:{shown[1]}", sym)
            remember_coin(nb, asset)

        spot_j = await get_json(
            "https://open-api.bingx.com/openApi/spot/v1/common/symbols",
            timeout=1.5,
        )
        for row in (spot_j or {}).get("data", {}).get("symbols") or []:
            if not isinstance(row, dict) or row.get("status") != 1:
                continue
            sym = str(row.get("symbol") or "")
            display = str(row.get("displayName") or "")
            native = _split_pair(sym)
            shown = _split_pair(display)
            if not native:
                continue
            nb, nq = native
            pair_map[f"{nb}:{nq}"] = sym
            if shown:
                pair_map.setdefault(f"{shown[0]}:{shown[1]}", sym)
            remember_coin(nb, nb)

        _cache.set("all", (pair_map, coin_map))
        return pair_map, coin_map


async def prefetch_bingx_symbols() -> None:
    try:
        await _load()
    except Exception:
        pass


def _kick_load() -> None:
    global _load_task
    hit = _cache.get("all")
    if hit is not None:
        return
    if _load_task is not None and not _load_task.done():
        return
    _load_task = asyncio.get_running_loop().create_task(_load())


async def bingx_pair(base: str, quote: str) -> str:
    b, q = base.upper(), quote.upper()
    hit = _cache.get("all")
    if hit is None:
        _kick_load()
        return f"{b}-{q}"
    pair_map, _ = hit
    return pair_map.get(f"{b}:{q}", f"{b}-{q}")


async def bingx_wallet_coins(base: str) -> set[str]:
    b = base.upper()
    _, coin_map = await _load()
    out = {b}
    out.update(coin_map.get(b) or set())
    return out
