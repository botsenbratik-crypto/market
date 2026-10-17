"""Contract-mode lookup: token meta, on-chain venues, CEX spot only."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from crypto_bot.clients.exchanges.market import ExchangeMarketClient
from crypto_bot.core.http import get_http
from crypto_bot.domain.contract_query import ContractQuery
from crypto_bot.domain.exchanges import EXCHANGE_BY_KEY, SPOT_EXCHANGE_DEFS
from crypto_bot.domain.links import spot_url

logger = structlog.get_logger(__name__)

_DEX_TOKENS = "https://api.dexscreener.com/latest/dex/tokens/{addr}"
_CG_CONTRACT = "https://api.coingecko.com/api/v3/coins/{platform}/contract/{addr}"

_CHAIN_LABEL: dict[str, str] = {
    "ethereum": "ETH",
    "eth": "ETH",
    "bsc": "BSC",
    "binance-smart-chain": "BSC",
    "bnb": "BSC",
    "solana": "SOL",
    "sol": "SOL",
    "base": "BASE",
    "arbitrum": "ARBITRUM",
    "arbitrum-one": "ARBITRUM",
    "optimism": "OPTIMISM",
    "optimistic-ethereum": "OPTIMISM",
    "polygon": "POLYGON",
    "polygon-pos": "POLYGON",
    "avalanche": "AVAX",
    "avax": "AVAX",
    "ton": "TON",
    "the-open-network": "TON",
    "hyperevm": "HYPERLIQUID",
    "hyperliquid": "HYPERLIQUID",
    "mantle": "MANTLE",
    "linea": "LINEA",
    "scroll": "SCROLL",
    "zksync": "ZKSYNCERA",
    "zksync-era": "ZKSYNCERA",
    "blast": "BLAST",
    "tron": "TRON",
    "sui": "SUI",
}

_KIND_PLATFORMS: dict[str, tuple[str, ...]] = {
    "evm": ("ethereum", "binance-smart-chain", "base", "arbitrum-one", "optimistic-ethereum", "polygon-pos"),
    "sol": ("solana",),
    "ton": ("the-open-network",),
}

_CG_MARKET_TO_EXCHANGE: dict[str, str] = {
    "binance": "binance",
    "bybit": "bybit",
    "gate.io": "gate",
    "gate": "gate",
    "mexc": "mexc",
    "bitget": "bitget",
    "okx": "okx",
    "kucoin": "kucoin",
    "bingx": "bingx",
    "kraken": "kraken",
    "aster": "aster",
    "asterdex": "aster",
}

_FUT_URL_HINTS = ("futures", "perpetual", "swap", "linear", "/um/", "usdtm")


@dataclass
class OnchainRow:
    network: str
    address: str
    price: float | None = None


@dataclass
class SpotRow:
    key: str
    name: str
    price: float
    url: str | None = None


@dataclass
class ContractReport:
    query: ContractQuery
    name: str | None = None
    symbol: str | None = None
    decimals: int | None = None
    query_network: str | None = None
    onchain: list[OnchainRow] = field(default_factory=list)
    spots: list[SpotRow] = field(default_factory=list)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def _chain_label(raw: str) -> str:
    key = (raw or "").strip().lower().replace("_", "-")
    return _CHAIN_LABEL.get(key, (raw or "").strip().upper()[:16] or "UNKNOWN")


def _is_spot_ticker(row: dict[str, Any]) -> bool:
    url = str(row.get("trade_url") or "").lower()
    return not any(h in url for h in _FUT_URL_HINTS)


def _exchange_key_from_cg(market_name: str) -> str | None:
    n = (market_name or "").strip().lower()
    if n in _CG_MARKET_TO_EXCHANGE:
        return _CG_MARKET_TO_EXCHANGE[n]
    for prefix, key in _CG_MARKET_TO_EXCHANGE.items():
        if n.startswith(prefix):
            return key
    return None


async def build_contract_report(query: ContractQuery) -> ContractReport:
    http = get_http()
    addr = query.address
    dex_task = asyncio.create_task(
        http.get_json(_DEX_TOKENS.format(addr=addr), timeout=2.5)
    )
    cg_task = asyncio.create_task(_coingecko_by_kind(query))
    dex_j, cg_j = await asyncio.gather(dex_task, cg_task, return_exceptions=True)
    if isinstance(dex_j, BaseException):
        logger.debug("contract_dex_skip", err=str(dex_j))
        dex_j = None
    if isinstance(cg_j, BaseException):
        logger.debug("contract_cg_skip", err=str(cg_j))
        cg_j = None

    report = ContractReport(query=query)
    _fill_from_dex(report, dex_j if isinstance(dex_j, dict) else None)
    _fill_from_cg(report, cg_j if isinstance(cg_j, dict) else None)
    await _fill_cex_spots(report, cg_j if isinstance(cg_j, dict) else None)
    return report


async def _coingecko_by_kind(query: ContractQuery) -> dict[str, Any] | None:
    http = get_http()
    platforms = _KIND_PLATFORMS.get(query.kind, ())
    addr = query.address
    if query.kind == "evm":
        addr = addr.lower()

    async def _one(platform: str) -> dict[str, Any] | None:
        try:
            data = await http.get_json(
                _CG_CONTRACT.format(platform=platform, addr=addr),
                timeout=2.2,
            )
        except Exception:
            return None
        return data if isinstance(data, dict) and data.get("id") else None

    jobs = [_one(p) for p in platforms]
    for done in asyncio.as_completed(jobs):
        try:
            hit = await done
        except Exception:
            continue
        if hit:
            return hit
    return None


def _fill_from_dex(report: ContractReport, data: dict[str, Any] | None) -> None:
    pairs = (data or {}).get("pairs") if isinstance(data, dict) else None
    if not isinstance(pairs, list) or not pairs:
        return
    ranked: list[tuple[float, dict[str, Any]]] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        liq = 0.0
        try:
            liq = float(((pair.get("liquidity") or {}).get("usd")) or 0)
        except (TypeError, ValueError):
            liq = 0.0
        ranked.append((liq, pair))
    if not ranked:
        return
    ranked.sort(key=lambda x: x[0], reverse=True)
    best = ranked[0][1]
    base = best.get("baseToken") if isinstance(best.get("baseToken"), dict) else {}
    if not report.name:
        report.name = str(base.get("name") or "").strip() or None
    if not report.symbol:
        report.symbol = str(base.get("symbol") or "").strip() or None
    if not report.query_network:
        report.query_network = _chain_label(str(best.get("chainId") or ""))

    by_net: dict[str, OnchainRow] = {}
    for liq, pair in ranked:
        base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
        addr = str(base.get("address") or "").strip()
        if not addr:
            continue
        net = _chain_label(str(pair.get("chainId") or ""))
        px = _f((pair.get("priceUsd")))
        prev = by_net.get(net)
        if prev is None:
            by_net[net] = OnchainRow(network=net, address=addr, price=px)
        elif prev.price is None and px is not None:
            prev.price = px
    report.onchain = list(by_net.values())


def _fill_from_cg(report: ContractReport, data: dict[str, Any] | None) -> None:
    if not isinstance(data, dict):
        return
    if not report.name:
        report.name = str(data.get("name") or "").strip() or None
    if not report.symbol:
        report.symbol = str(data.get("symbol") or "").strip().upper() or None
    else:
        report.symbol = report.symbol.strip()
    platforms = data.get("detail_platforms") or data.get("platforms") or {}
    if isinstance(platforms, dict):
        for plat, info in platforms.items():
            net = _chain_label(str(plat))
            addr = ""
            decimals = None
            if isinstance(info, dict):
                addr = str(info.get("contract_address") or "").strip()
                try:
                    decimals = int(info["decimal_place"]) if info.get("decimal_place") is not None else None
                except (TypeError, ValueError):
                    decimals = None
            elif isinstance(info, str):
                addr = info.strip()
            if decimals is not None and report.decimals is None:
                q = report.query.address.lower()
                if not addr or addr.lower() == q or report.query.kind != "evm":
                    report.decimals = decimals
            if not addr:
                continue
            existing = next((r for r in report.onchain if r.network == net), None)
            if existing is None:
                report.onchain.append(OnchainRow(network=net, address=addr))
            if report.query_network is None:
                q = report.query.address.lower()
                if addr.lower() == q:
                    report.query_network = net
    if report.decimals is None:
        detail = ((data.get("detail_platforms") or {}).get("ethereum") or {})
        if isinstance(detail, dict) and detail.get("decimal_place") is not None:
            try:
                report.decimals = int(detail["decimal_place"])
            except (TypeError, ValueError):
                pass


async def _fill_cex_spots(report: ContractReport, cg: dict[str, Any] | None) -> None:
    tickers = (cg or {}).get("tickers") if isinstance(cg, dict) else None
    if not isinstance(tickers, list):
        tickers = []
    by_ex: dict[str, SpotRow] = {}
    listed_bases: dict[str, str] = {}
    for row in tickers:
        if not isinstance(row, dict) or not _is_spot_ticker(row):
            continue
        market = row.get("market") if isinstance(row.get("market"), dict) else {}
        key = _exchange_key_from_cg(str(market.get("name") or market.get("identifier") or ""))
        if not key or key not in EXCHANGE_BY_KEY:
            continue
        if EXCHANGE_BY_KEY[key].futures_only:
            continue
        target = str(row.get("target") or "").upper()
        if target not in {"USDT", "USD", "USDC", "BUSD"}:
            continue
        px = _f(((row.get("converted_last") or {}).get("usd")))
        if px is None:
            px = _f(row.get("last"))
        if px is None:
            continue
        base = str(row.get("base") or report.symbol or "").upper()
        listed_bases[key] = base
        name = EXCHANGE_BY_KEY[key].name
        by_ex[key] = SpotRow(key=key, name=name, price=px, url=spot_url(key, base, target or "USDT"))

    if listed_bases:
        client = ExchangeMarketClient()
        jobs = [
            _live_spot(client, key, base)
            for key, base in listed_bases.items()
            if key in {d.key for d in SPOT_EXCHANGE_DEFS}
        ]
        live = await asyncio.gather(*jobs, return_exceptions=True)
        for item in live:
            if isinstance(item, tuple):
                key, px = item
                if px is None or key not in by_ex:
                    continue
                prev = by_ex[key]
                by_ex[key] = SpotRow(key=key, name=prev.name, price=px, url=prev.url)

    report.spots = [by_ex[d.key] for d in SPOT_EXCHANGE_DEFS if d.key in by_ex]


async def _live_spot(client: ExchangeMarketClient, key: str, base: str) -> tuple[str, float | None]:
    try:
        px = await asyncio.wait_for(client.fetch_spot_only(key, base, "USDT"), timeout=1.6)
    except Exception:
        return key, None
    return key, px
