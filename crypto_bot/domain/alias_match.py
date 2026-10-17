"""Pure helpers: same token / different tickers (no I/O)."""

from __future__ import annotations

import re
from typing import Any

_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_ADDR_RE = re.compile(r"^(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")


def norm_addr(raw: str | None) -> str | None:
    text = str(raw or "").strip()
    if not text or text.lower() in ("native", "null", "-"):
        return None
    if _EVM_RE.match(text):
        return text.lower()
    if _ADDR_RE.match(text):
        return text
    return None


def score_dex_pair(query: str, pair: dict[str, Any]) -> float:
    """Higher = more likely this DEX pair is the token the user asked for."""
    q = query.strip().upper()
    base = pair.get("baseToken") or {}
    if not isinstance(base, dict):
        return 0.0
    sym = str(base.get("symbol") or "").strip().upper()
    name = str(base.get("name") or "").strip().upper()
    liq = 0.0
    try:
        liq = float(((pair.get("liquidity") or {}).get("usd")) or 0)
    except (TypeError, ValueError):
        liq = 0.0
    score = 0.0
    if sym == q:
        score += 100
    elif q and (q in sym or sym in q) and min(len(q), len(sym)) >= 4:
        score += 45
    if q and q in name:
        score += 35
    if name and name in q:
        score += 20
    score += min(liq, 50_000) / 5_000
    return score


def pick_dex_addresses(query: str, pairs: list[Any], *, limit: int = 2) -> list[str]:
    ranked: list[tuple[float, str]] = []
    seen: set[str] = set()
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        sc = score_dex_pair(query, pair)
        if sc < 35:
            continue
        addr = norm_addr((pair.get("baseToken") or {}).get("address"))
        if not addr or addr in seen:
            continue
        seen.add(addr)
        ranked.append((sc, addr))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [a for _, a in ranked[:limit]]


def dex_symbols_for_addresses(pairs: list[Any], addresses: set[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        base = pair.get("baseToken") or {}
        addr = norm_addr(base.get("address") if isinstance(base, dict) else None)
        if addr not in addresses:
            continue
        sym = str(base.get("symbol") or "").strip().upper()
        if not sym or sym in seen or len(sym) > 20:
            continue
        seen.add(sym)
        out.append(sym)
    return out[:4]
