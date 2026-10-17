"""Same token, different CEX tickers — probe aliases, keep only price-matching hits."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from market_bot.models import Contract
from market_bot.utils.http import get_json

_EVM = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _norm_addr(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text or text.lower() in ("native", "null", "-"):
        return None
    if _EVM.match(text):
        return text.lower()
    return None


def quick_candidates(query: str) -> list[str]:
    """Tickers to try on every exchange. Wrong coins are dropped by price_sane."""
    q = query.strip().upper()
    out: list[str] = []
    if q:
        out.append(q)
    if q.startswith("10000") and len(q) > 7:
        out.append(q[5:])
    elif q.startswith("1000") and len(q) > 6:
        out.append(q[4:])
    if len(q) > 3 and q[0] == "K" and q[1:].isalpha():
        out.append(q[1:])
    if "MEME" in q:
        # BingX lists A Meme Coin as AMEMECOIN (displayName is still MEME).
        out.extend(["AMEMECOIN", "MEMECOIN", "MEME"])
    out.extend(spelling_alts(q))
    compact = re.sub(r"[^A-Z0-9]", "", q)
    if compact != q and 4 <= len(compact) <= 20:
        out.append(compact)
    return _uniq(out)


async def extra_candidates(symbol: str, contracts: list[Contract]) -> list[str]:
    """DEX name/symbol variants (e.g. A Meme Coin → MEMECOIN)."""
    q = symbol.strip().upper()
    addrs = [a for c in contracts if (a := _norm_addr(c.address))]
    if not addrs:
        return []
    try:
        name, dex_sym = await asyncio.wait_for(_dex_meta(addrs[0]), timeout=0.4)
    except (asyncio.TimeoutError, Exception):
        return []
    # Do not glue the full DEX name into a fake ticker (A Meme Coin → AMEMECOIN).
    out: list[str] = []
    sym = (dex_sym or "").strip().upper()
    if 2 <= len(sym) <= 20:
        out.append(sym)
    words = [
        w
        for w in re.findall(r"[A-Z0-9]+", (name or "").upper())
        if w not in {"A", "AN", "THE", "COIN", "TOKEN"}
    ]
    if len(words) == 1 and 4 <= len(words[0]) <= 20:
        out.append(words[0])
    return [c for c in _uniq(out) if c != q]


def spelling_alts(query: str) -> list[str]:
    """One-off typos: memerobinhud → MEMEROBINHOOD."""
    q = query.strip().upper()
    out: list[str] = []
    if len(q) >= 8 and q.endswith("HUD") and not q.endswith("HOOD"):
        out.append(q[: -3] + "HOOD")
    if len(q) >= 8 and q.endswith("HOD") and not q.endswith("HOOD"):
        out.append(q[: -3] + "HOOD")
    if len(q) >= 10 and "HOOD" not in q and "ROBIN" in q:
        out.append(q.replace("ROBINH", "ROBINHO") if "ROBINH" in q else q)
    return [x for x in _uniq(out) if x != q]


def edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 99
    dp = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev = dp[0]
        dp[0] = i
        for j, cb in enumerate(b, 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (ca != cb))
            prev = cur
    return dp[-1]


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        s = item.strip().upper()
        if not s or s in seen or len(s) > 20:
            continue
        seen.add(s)
        out.append(s)
    return out[:8]


async def _dex_meta(address: str) -> tuple[str, str]:
    data = await get_json(
        f"https://api.dexscreener.com/latest/dex/tokens/{address}",
        timeout=0.4,
    )
    pairs = (data or {}).get("pairs") or []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        base = pair.get("baseToken") or {}
        if not isinstance(base, dict):
            continue
        if _norm_addr(base.get("address")) != address.lower():
            continue
        return str(base.get("name") or ""), str(base.get("symbol") or "")
    return "", ""


def price_sane(
    ref: float | None, other: float | None, *, max_ratio: float = 10.0
) -> bool:
    """Same token can trade 10x apart on thin books; drop only another coin (MEME vs ~$0.08)."""
    if not ref or not other or ref <= 0 or other <= 0 or max_ratio <= 1:
        return False
    ratio = other / ref
    return (1.0 / max_ratio) <= ratio <= max_ratio
