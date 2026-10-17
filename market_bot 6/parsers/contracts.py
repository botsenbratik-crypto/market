"""Contract addresses — CMC primary + exchange validation."""

from __future__ import annotations

import asyncio

from market_bot.config import get_settings
from market_bot.models import Contract
from market_bot.utils.cache import TtlCache
from market_bot.utils.http import get_json

_contracts_cache = TtlCache[list[Contract]](300.0)
_binance_coins_cache: list[dict] | None = None

_DISPLAY_NETWORKS = frozenset(
    {
        "BSC",
        "ETH",
        "SOL",
        "BASE",
        "ARBITRUM",
        "OPTIMISM",
        "POLYGON",
        "MANTLE",
        "STARKNET",
        "ZKSYNCERA",
        "LINEA",
        "SCROLL",
        "TRON",
        "AVAX",
        "TON",
        "APT",
        "SUI",
        "ROBINHOOD",
        "HYPERLIQUID",
    }
)

_CHAIN_MAP = {
    "BNB Smart Chain (BEP20)": "BSC",
    "BNB Smart Chain": "BSC",
    "BSC": "BSC",
    "Ethereum": "ETH",
    "ETH": "ETH",
    "Solana": "SOL",
    "SOL": "SOL",
    "Base": "BASE",
    "Arbitrum": "ARBITRUM",
    "Arbitrum One": "ARBITRUM",
    "ARBITRUM": "ARBITRUM",
    "Optimism": "OPTIMISM",
    "Polygon": "POLYGON",
    "Mantle": "MANTLE",
    "Starknet": "STARKNET",
    "zkSync Era": "ZKSYNCERA",
    "Linea": "LINEA",
    "Scroll": "SCROLL",
    "Tron20": "TRON",
    "TRON": "TRON",
    "Avalanche C-Chain": "AVAX",
    "AVAX": "AVAX",
    "TON": "TON",
    "Aptos": "APT",
    "Sui": "SUI",
    "Robinhood": "ROBINHOOD",
    "Robinhood Chain": "ROBINHOOD",
    "ROBINHOOD": "ROBINHOOD",
    "ROBINHOOD CHAIN": "ROBINHOOD",
    "HYPERLIQUID": "HYPERLIQUID",
    "HYPEREVM": "HYPERLIQUID",
}

# Native tickers → extra CMC symbols with multi-chain contracts
_WRAPPED_SYMBOLS: dict[str, list[str]] = {
    "BTC": ["WBTC", "BTCB"],
    "ETH": ["WETH"],
}

_SLUG_MAP = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
_SKIP_DEX_SEARCH = frozenset({"BTC", "ETH", "SOL", "BNB", "USDT", "USDC"})
_DUMMY_PREFIXES = ("0xeee", "0x000", "0xdead")


def is_dummy_address(addr: str) -> bool:
    a = (addr or "").strip().lower()
    if not a or len(a) < 10:
        return True
    return any(a.startswith(p) for p in _DUMMY_PREFIXES)


def _norm_platform(platform: str) -> str:
    return _CHAIN_MAP.get(platform, platform.upper().replace(" ", ""))


def _contracts_from_entry(entry: dict) -> list[Contract]:
    out: list[Contract] = []
    for item in entry.get("contract_address") or []:
        platform = item.get("platform", {}).get("name", "")
        net = _norm_platform(platform)
        addr = (item.get("contract_address") or "").strip()
        if is_dummy_address(addr):
            continue
        if net not in _DISPLAY_NETWORKS:
            continue
        out.append(Contract(net, addr))
    return out


def _pick_primary_entry(
    entries: list[dict],
    symbol: str,
    exchange_addrs: set[str],
    mcap_by_id: dict[int, float],
) -> dict:
    sym = symbol.upper()
    if sym in _SLUG_MAP:
        want_slug = _SLUG_MAP[sym]
        for entry in entries:
            if str(entry.get("slug", "")).lower() == want_slug:
                return entry

    best: dict | None = None
    best_score = -1.0
    for entry in entries:
        contracts = _contracts_from_entry(entry)
        addr_hits = sum(1 for c in contracts if c.address.lower() in exchange_addrs)
        mcap = float(mcap_by_id.get(int(entry.get("id") or 0), 0) or 0)
        # Exchange-listed address match beats market cap (e.g. AI → Sleepless, not Gensyn).
        score = addr_hits * 1_000_000_000_000 + mcap
        if contracts and score > best_score:
            best_score = score
            best = entry

    if best is not None:
        return best
    for entry in entries:
        if entry.get("contract_address"):
            return entry
    for entry in entries:
        if str(entry.get("symbol", "")).upper() == sym:
            return entry
    return entries[0]


async def _binance_coins() -> list[dict]:
    global _binance_coins_cache
    if _binance_coins_cache is not None:
        return _binance_coins_cache
    data = await get_json(
        "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll",
        timeout=0.8,
    )
    _binance_coins_cache = (data or {}).get("data") or [] if isinstance(data, dict) else []
    return _binance_coins_cache


async def _exchange_contract_addrs(symbol: str) -> set[str]:
    sym = symbol.upper()
    addrs: set[str] = set()
    for coin in await _binance_coins():
        if str(coin.get("coin", "")).upper() != sym:
            continue
        for net in coin.get("networkList") or []:
            addr = str(net.get("contractAddress") or "").strip().lower()
            if addr and len(addr) >= 10:
                addrs.add(addr)
    return addrs


async def _cmc_market_caps(symbol: str, api_key: str) -> dict[int, float]:
    data = await get_json(
        "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest",
        params={"symbol": symbol.upper()},
        headers={"X-CMC_PRO_API_KEY": api_key},
        timeout=0.7,
    )
    items = (data or {}).get("data", {}).get(symbol.upper()) or []
    out: dict[int, float] = {}
    for item in items:
        cid = item.get("id")
        if cid is None:
            continue
        mcap = (item.get("quote") or {}).get("USD", {}).get("market_cap")
        if mcap is not None:
            out[int(cid)] = float(mcap)
    return out


async def _cmc_entries(symbol: str, api_key: str) -> list[dict]:
    data = await get_json(
        "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info",
        params={"symbol": symbol.upper()},
        headers={"X-CMC_PRO_API_KEY": api_key},
        timeout=0.7,
    )
    if not data or not data.get("data"):
        return []
    entries = data["data"].get(symbol.upper()) or next(iter(data["data"].values()), [])
    return entries if isinstance(entries, list) else []


async def _cmc_contracts(
    symbol: str,
    api_key: str,
    exchange_addrs: set[str],
    mcap_by_id: dict[int, float],
) -> list[Contract]:
    entries = await _cmc_entries(symbol, api_key)
    if not entries:
        return []
    primary = _pick_primary_entry(entries, symbol, exchange_addrs, mcap_by_id)
    return _contracts_from_entry(primary)


_PREF_NETS = (
    "ETH",
    "BSC",
    "SOL",
    "BASE",
    "ARBITRUM",
    "OPTIMISM",
    "POLYGON",
    "AVAX",
    "HYPERLIQUID",
)


def slim_for_display(items: list[Contract]) -> list[Contract]:
    """One contract per chain; first source wins (CEX listings before DEX search)."""
    by_net: dict[str, Contract] = {}
    for c in items:
        if is_dummy_address(c.address):
            continue
        prev = by_net.get(c.network)
        if prev is None:
            by_net[c.network] = c
            continue
        if prev.address.lower() == c.address.lower() and prev.price is None and c.price:
            by_net[c.network] = c
    ordered: list[Contract] = []
    seen: set[str] = set()
    for net in _PREF_NETS:
        if net in by_net:
            ordered.append(by_net[net])
            seen.add(net)
    rest = sorted((n for n in by_net if n not in seen))
    for net in rest:
        ordered.append(by_net[net])
    return ordered[:8]


def _merge_contracts(chunks: list[list[Contract]]) -> list[Contract]:
    return slim_for_display([c for chunk in chunks for c in chunk])



_DEX_CHAIN = {
    "ethereum": "ETH",
    "eth": "ETH",
    "bsc": "BSC",
    "solana": "SOL",
    "base": "BASE",
    "arbitrum": "ARBITRUM",
    "optimism": "OPTIMISM",
    "polygon": "POLYGON",
    "avalanche": "AVAX",
    "linea": "LINEA",
    "scroll": "SCROLL",
    "zksync": "ZKSYNCERA",
    "mantle": "MANTLE",
    "ton": "TON",
    "sui": "SUI",
    "tron": "TRON",
    "aptos": "APT",
    "robinhood": "ROBINHOOD",
    "hyperevm": "HYPERLIQUID",
    "hyperliquid": "HYPERLIQUID",
}


async def _dex_search_contracts(symbol: str) -> list[Contract]:
    """Public DexScreener search — covers DEX-only / CMC-unlisted tickers."""
    if symbol.upper() in _SKIP_DEX_SEARCH:
        return []
    data = await get_json(
        "https://api.dexscreener.com/latest/dex/search",
        params={"q": symbol},
        timeout=0.7,
    )
    pairs = (data or {}).get("pairs") or []
    scored: dict[tuple[str, str], tuple[float, float, str]] = {}
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        base = pair.get("baseToken") or {}
        base_sym = str(base.get("symbol") or "").upper()
        q = symbol.upper()
        if base_sym != q:
            continue
        addr = str(base.get("address") or "").strip()
        if is_dummy_address(addr):
            continue
        net = _DEX_CHAIN.get(str(pair.get("chainId") or "").lower(), "")
        if net not in _DISPLAY_NETWORKS:
            continue
        if net == "HYPERLIQUID" and not (
            addr.startswith("0x") and len(addr) == 42
        ):
            continue
        try:
            liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        try:
            vol24 = float((pair.get("volume") or {}).get("h24") or 0)
        except (TypeError, ValueError):
            vol24 = 0.0
        try:
            px = float(pair.get("priceUsd") or 0)
        except (TypeError, ValueError):
            px = 0.0
        key = (net, addr.lower())
        prev = scored.get(key)
        min_liq = 50_000.0 if len(q) <= 4 else 1_000.0
        # Exact ticker + volume: keep even if DexScreener omits liquidity (TREAD on ETH).
        if liq < min_liq and vol24 < 200:
            continue
        # Spoofed pools: huge "liquidity", ~zero volume (ANSEM copycats).
        if liq >= 80_000 and vol24 * 50 < liq:
            continue
        score = vol24 if vol24 > 0 else liq
        if prev is None or score >= prev[0]:
            scored[key] = (score, px, addr)
    ranked = sorted(scored.items(), key=lambda kv: kv[1][0], reverse=True)
    out: list[Contract] = []
    seen_net: set[str] = set()
    for (net, _addr), (_liq, px, orig) in ranked:
        if net in seen_net:
            continue
        seen_net.add(net)
        out.append(Contract(net, orig, price=px or None))
        if len(out) >= 6:
            break
    return out


async def _mexc_listed_contracts(symbol: str) -> list[Contract]:
    try:
        from market_bot.parsers.wallets import _load_mexc_config
    except Exception:
        return []
    rows = await _load_mexc_config(get_settings())
    out: list[Contract] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if str(row.get("coin") or "").upper() != symbol.upper():
            continue
        for net in row.get("networkList") or []:
            if not isinstance(net, dict):
                continue
            addr = str(net.get("contract") or net.get("contractAddress") or "").strip()
            chain = _norm_platform(str(net.get("netWork") or net.get("network") or ""))
            if is_dummy_address(addr) or chain not in _DISPLAY_NETWORKS:
                continue
            if chain in seen:
                continue
            seen.add(chain)
            out.append(Contract(chain, addr))
    return out


async def _hl_listed_contracts(symbol: str) -> list[Contract]:
    try:
        from market_bot.parsers.hyperliquid_symbols import hl_listed_contracts
    except Exception:
        return []
    try:
        return await hl_listed_contracts(symbol)
    except Exception:
        return []


async def fetch_contracts(symbol: str) -> list[Contract]:
    sym = symbol.upper()
    hit = _contracts_cache.get(sym)
    if hit is not None:
        return hit

    key = get_settings().cmc_api_key
    jobs: list = [
        _mexc_listed_contracts(sym),
        _binance_fallback(sym),
        _hl_listed_contracts(sym),
        _dex_search_contracts(sym),
    ]
    if key:
        jobs.append(_cmc_fast(sym, key))

    async def _timed(job):
        try:
            return await job
        except Exception:
            return []

    results = await asyncio.gather(
        *[_timed(job) for job in jobs],
        return_exceptions=True,
    )
    chunks = [r for r in results if isinstance(r, list)]
    contracts = _merge_contracts(chunks)
    if contracts:
        _contracts_cache.set(sym, contracts)
    return contracts


async def _cmc_fast(symbol: str, api_key: str) -> list[Contract]:
    wrapped = _WRAPPED_SYMBOLS.get(symbol, [])
    extras = await asyncio.gather(
        _exchange_contract_addrs(symbol),
        _cmc_market_caps(symbol, api_key),
        _cmc_entries(symbol, api_key),
        *[_cmc_entries(s, api_key) for s in wrapped],
        return_exceptions=True,
    )
    exchange_addrs = extras[0] if isinstance(extras[0], set) else set()
    mcap_by_id = extras[1] if isinstance(extras[1], dict) else {}
    chunks: list[list[Contract]] = []
    entry_groups = extras[2:]
    symbols = [symbol, *wrapped]
    for sym, entries in zip(symbols, entry_groups):
        if not isinstance(entries, list) or not entries:
            continue
        addrs = exchange_addrs if sym == symbol else set()
        primary = _pick_primary_entry(entries, sym, addrs, mcap_by_id)
        chunks.append(_contracts_from_entry(primary))
    return _merge_contracts(chunks)


async def _binance_fallback(symbol: str) -> list[Contract]:
    out: list[Contract] = []
    seen: set[str] = set()
    for coin in await _binance_coins():
        if str(coin.get("coin", "")).upper() != symbol.upper():
            continue
        for net in coin.get("networkList") or []:
            addr = str(net.get("contractAddress") or "").strip()
            chain = _norm_platform(str(net.get("network") or ""))
            if is_dummy_address(addr) or chain not in _DISPLAY_NETWORKS:
                continue
            if chain in seen:
                continue
            seen.add(chain)
            out.append(Contract(chain, addr))
    return out
