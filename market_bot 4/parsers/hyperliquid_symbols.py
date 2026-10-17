"""Hyperliquid spot (HIP-1) + native perps + HIP-3 dex perps.

Indexes are warmed on keepalive so a ticker query only hits l2Book.
"""

from __future__ import annotations

import asyncio
from typing import Any

from market_bot.models import Contract
from market_bot.utils.cache import TtlCache
from market_bot.utils.http import post_json

_HL_INFO = "https://api.hyperliquid.xyz/info"
_spot_pairs = TtlCache[dict[str, str]](1800.0)
_perp_coins = TtlCache[dict[str, str]](1800.0)
_spot_evm = TtlCache[dict[str, str]](1800.0)
_main_meta = TtlCache[list](120.0)
_lock = asyncio.Lock()


def _l2_mid(book: Any) -> float | None:
    lvls = (book or {}).get("levels") if isinstance(book, dict) else None
    if not isinstance(lvls, list) or len(lvls) < 2:
        return None
    try:
        bid = float(lvls[0][0]["px"])
        ask = float(lvls[1][0]["px"])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0


def _evm_addr(token: dict) -> str | None:
    evm = token.get("evmContract") or {}
    if isinstance(evm, dict):
        addr = str(evm.get("address") or "").strip()
        if addr.startswith("0x") and len(addr) == 42:
            return addr
    tid = str(token.get("tokenId") or "").strip()
    if tid.startswith("0x") and len(tid) == 42:
        return tid
    return None


async def _load_indexes() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    spot = _spot_pairs.get("all")
    perp = _perp_coins.get("all")
    evm = _spot_evm.get("all")
    if spot is not None and perp is not None and evm is not None:
        return spot, perp, evm
    async with _lock:
        spot = _spot_pairs.get("all")
        perp = _perp_coins.get("all")
        evm = _spot_evm.get("all")
        if spot is not None and perp is not None and evm is not None:
            return spot, perp, evm
        return await _fetch_indexes()


async def _fetch_indexes() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    main = _main_meta.get("all")
    jobs: list = [
        post_json(_HL_INFO, {"type": "spotMeta"}, timeout=0.8),
        post_json(_HL_INFO, {"type": "perpDexs"}, timeout=0.8),
    ]
    if main is None:
        jobs.append(post_json(_HL_INFO, {"type": "metaAndAssetCtxs"}, timeout=0.8))
    raw = await asyncio.gather(*jobs, return_exceptions=True)
    spot_j = raw[0] if not isinstance(raw[0], BaseException) else None
    dex_j = raw[1] if not isinstance(raw[1], BaseException) else None
    if main is None and len(raw) > 2 and isinstance(raw[2], list) and len(raw[2]) >= 2:
        main = raw[2]
        _main_meta.set("all", main)

    spot_map: dict[str, str] = {}
    evm_map: dict[str, str] = {}
    if isinstance(spot_j, dict):
        tokens = {
            int(t["index"]): t
            for t in (spot_j.get("tokens") or [])
            if isinstance(t, dict) and t.get("index") is not None
        }
        for tok in tokens.values():
            name = str(tok.get("name") or "").upper()
            addr = _evm_addr(tok)
            if name and addr:
                evm_map[name] = addr
        for u in spot_j.get("universe") or []:
            if not isinstance(u, dict):
                continue
            toks = u.get("tokens") or []
            if not toks:
                continue
            tok = tokens.get(int(toks[0]))
            name = str((tok or {}).get("name") or "").upper()
            pair = str(u.get("name") or "")
            if name and pair:
                spot_map[name] = pair

    perp_map: dict[str, str] = {}
    if isinstance(main, list) and main:
        for a in (main[0] or {}).get("universe") or []:
            n = str((a or {}).get("name") or "")
            if n and ":" not in n:
                perp_map[n.upper()] = n
    if isinstance(dex_j, list):
        ranked: list[tuple[float, str, str]] = []
        for d in dex_j:
            if not isinstance(d, dict):
                continue
            for row in d.get("assetToStreamingOiCap") or []:
                if not isinstance(row, (list, tuple)) or len(row) < 1:
                    continue
                text = str(row[0] or "")
                if ":" not in text:
                    continue
                coin = text.split(":", 1)[1].upper()
                try:
                    cap = float(row[1]) if len(row) > 1 else 0.0
                except (TypeError, ValueError):
                    cap = 0.0
                ranked.append((cap, coin, text))
        ranked.sort(reverse=True)
        for _cap, coin, text in ranked:
            perp_map.setdefault(coin, text)

    _spot_pairs.set("all", spot_map)
    _perp_coins.set("all", perp_map)
    _spot_evm.set("all", evm_map)
    return spot_map, perp_map, evm_map


async def prefetch_hyperliquid() -> None:
    try:
        await _load_indexes()
    except Exception:
        pass


async def hl_listed_contracts(symbol: str) -> list[Contract]:
    _spot, _perp, evm = await _load_indexes()
    addr = evm.get(symbol.upper())
    if not addr:
        return []
    return [Contract("HYPERLIQUID", addr)]


async def hl_books(base: str) -> tuple[float | None, float | None, str | None, str | None]:
    """Spot mid, perp mid, spot URL, futures URL."""
    sym = base.upper()
    spot_map, perp_map, _evm = await _load_indexes()
    spot_coin = spot_map.get(sym)
    perp_coin = perp_map.get(sym)
    labels: list[str] = []
    jobs: list = []
    if spot_coin:
        labels.append("spot")
        jobs.append(post_json(_HL_INFO, {"type": "l2Book", "coin": spot_coin}, timeout=0.55))
    if perp_coin:
        labels.append("fut")
        jobs.append(post_json(_HL_INFO, {"type": "l2Book", "coin": perp_coin}, timeout=0.55))
    books = await asyncio.gather(*jobs, return_exceptions=True) if jobs else []
    by = {lab: book for lab, book in zip(labels, books)}
    spot_px = _l2_mid(by.get("spot") if isinstance(by.get("spot"), dict) else None)
    fut_px = _l2_mid(by.get("fut") if isinstance(by.get("fut"), dict) else None)
    spot_url = f"https://app.hyperliquid.xyz/trade/{sym}" if spot_px else None
    fut_url = (
        f"https://app.hyperliquid.xyz/trade/{perp_coin}" if fut_px and perp_coin else None
    )
    return spot_px, fut_px, spot_url, fut_url
