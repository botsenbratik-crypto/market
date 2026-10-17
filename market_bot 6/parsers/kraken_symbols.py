"""Kraken spot pair names — cached from public AssetPairs."""

from __future__ import annotations

import asyncio

from market_bot.utils.cache import TtlCache
from market_bot.utils.http import get_json

_cache = TtlCache[dict[tuple[str, str], list[str]]](3600.0)
_BASE_ALIASES = {"XBT": "BTC", "XXBT": "BTC", "XETH": "ETH"}
_load_task: asyncio.Task | None = None


def _norm_asset(code: str) -> str:
    c = (code or "").upper()
    if c in _BASE_ALIASES:
        return _BASE_ALIASES[c]
    if c.startswith("X") and len(c) <= 5:
        stripped = c.lstrip("X")
        if stripped and stripped.isalpha():
            return stripped
    return c


def _norm_quote(code: str) -> str:
    c = (code or "").upper()
    return {"ZUSD": "USD", "ZUSDT": "USDT"}.get(c, c)


def _fallback_pairs(base: str, quote: str) -> list[str]:
    b, q = base.upper(), quote.upper()
    out: list[str] = []
    if b == "BTC":
        out.extend(["XBTUSDT", "XBTUSD", "XXBTZUSD"])
    elif b == "ETH":
        out.extend(["ETHUSDT", "ETHUSD", "XETHZUSD"])
    for cand in (f"{b}USD", f"{b}USDT", f"{b}{q}"):
        if cand not in out:
            out.append(cand)
    return out


async def _load() -> dict[tuple[str, str], list[str]]:
    hit = _cache.get("idx")
    if hit is not None:
        return hit
    ap = await get_json("https://api.kraken.com/0/public/AssetPairs", timeout=1.2)
    idx: dict[tuple[str, str], list[str]] = {}
    for _pid, info in ((ap or {}).get("result") or {}).items():
        if info.get("status") != "online":
            continue
        base = _norm_asset(str(info.get("base") or ""))
        quote = _norm_quote(str(info.get("quote") or ""))
        if not base or not quote:
            continue
        name = str(info.get("altname") or info.get("wsname") or _pid)
        bucket = idx.setdefault((base, quote), [])
        if name not in bucket:
            bucket.append(name)
    if idx:
        _cache.set("idx", idx)
    return idx


def _kick_load() -> None:
    global _load_task
    if _cache.get("idx") is not None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _load_task is None or _load_task.done():
        _load_task = loop.create_task(_load())


async def prefetch_kraken_pairs() -> None:
    try:
        await _load()
    except Exception:
        pass


async def kraken_ticker_pairs(base: str, quote: str) -> list[str]:
    b, q = base.upper(), quote.upper()
    idx = _cache.get("idx")
    if idx is None:
        return _fallback_pairs(b, q)
    pairs: list[str] = []
    seen: set[str] = set()
    for q_try in ("USD", q) if q != "USD" else (q,):
        for name in idx.get((b, q_try), []):
            if name not in seen:
                seen.add(name)
                pairs.append(name)
    return pairs or _fallback_pairs(b, q)
