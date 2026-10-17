"""DEX spot prices via DexScreener (per contract address); links go to OKX DEX."""

from __future__ import annotations

import asyncio

from market_bot.models import Contract
from market_bot.parsers import aliases
from market_bot.utils.http import get_json

_CHAIN_IDS = {
    "ETH": "ethereum",
    "BSC": "bsc",
    "SOL": "solana",
    "BASE": "base",
    "ARBITRUM": "arbitrum",
    "OPTIMISM": "optimism",
    "POLYGON": "polygon",
    "AVAX": "avalanche",
    "LINEA": "linea",
    "SCROLL": "scroll",
    "ZKSYNCERA": "zksync",
    "MANTLE": "mantle",
    "TON": "ton",
    "SUI": "sui",
    "TRON": "tron",
    "ROBINHOOD": "robinhood",
    "HYPERLIQUID": "hyperevm",
}

_NATIVE_PREFIXES = ("0xeeee", "0x0000", "0xdead")


def _is_priced_address(addr: str) -> bool:
    a = (addr or "").strip()
    if not a or len(a) < 10:
        return False
    low = a.lower()
    return not any(low.startswith(p) for p in _NATIVE_PREFIXES)


def _okx_dex_url(network: str, address: str) -> str:
    chain = _CHAIN_IDS.get((network or "").upper()) or (network or "").strip().lower()
    if (network or "").upper() == "HYPERLIQUID":
        return f"https://hyperevmscan.io/token/{address}"
    if not chain:
        chain = "ethereum"
    return f"https://web3.okx.com/token/{chain}/{address}"


def _best_pair(pairs: list[dict], chain_hint: str | None, ref: float | None = None) -> dict | None:
    want = (_CHAIN_IDS.get(chain_hint or "", "") or "").lower()
    scored: list[tuple[float, dict]] = []
    for p in pairs:
        if not isinstance(p, dict):
            continue
        try:
            px = float(p.get("priceUsd") or 0)
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        liq = 0.0
        try:
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            pass
        chain = str(p.get("chainId") or "").lower()
        bonus = 1e12 if want and chain == want else 0.0
        if ref and aliases.price_sane(ref, px, max_ratio=4.0):
            bonus += 1e15
        elif ref and not aliases.price_sane(ref, px, max_ratio=4.0):
            continue
        scored.append((bonus + liq, p))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


async def _fetch_token(address: str) -> list[dict]:
    data = await get_json(
        f"https://api.dexscreener.com/latest/dex/tokens/{address}",
        timeout=0.50,
    )
    return (data or {}).get("pairs") or []


def _with_okx(c: Contract, *, price: float | None = None) -> Contract:
    return Contract(
        network=c.network,
        address=c.address,
        price=c.price if price is None else price,
        url=_okx_dex_url(c.network, c.address) if _is_priced_address(c.address) else c.url,
    )


def attach_explorer_urls(contracts: list[Contract]) -> list[Contract]:
    return [_with_okx(c) for c in contracts]


async def enrich_contracts(
    contracts: list[Contract], ref: float | None = None
) -> list[Contract]:
    """Attach DEX USD price + OKX DEX URL. One DexScreener call per address."""
    if not contracts:
        return contracts

    targets = [c for c in contracts if _is_priced_address(c.address)]
    if not targets:
        return contracts

    unique: dict[str, str] = {}
    for c in targets:
        unique.setdefault(c.address.lower(), c.address)

    addrs = list(unique.values())[:6]
    results = await asyncio.gather(
        *[_fetch_token(addr) for addr in addrs],
        return_exceptions=True,
    )

    by_addr: dict[str, list[dict]] = {}
    for addr, res in zip(addrs, results):
        if not isinstance(res, list):
            continue
        for pair in res:
            if not isinstance(pair, dict):
                continue
            base = (pair.get("baseToken") or {}).get("address") or ""
            key = (base or addr).lower()
            by_addr.setdefault(key, []).append(pair)

    out: list[Contract] = []
    for c in contracts:
        if not _is_priced_address(c.address):
            out.append(c)
            continue
        pairs = by_addr.get(c.address.lower()) or []
        best = _best_pair(pairs, c.network, ref)
        if not best:
            out.append(_with_okx(c))
            continue
        try:
            price = float(best["priceUsd"])
        except (TypeError, ValueError, KeyError):
            out.append(_with_okx(c))
            continue
        out.append(_with_okx(c, price=price))
    return out
