"""Same token, different CEX tickers — resolve via contract, not the name."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from crypto_bot.clients.coinmarketcap import CoinMarketCapClient
from crypto_bot.core.http import get_http
from crypto_bot.domain.alias_match import (
    dex_symbols_for_addresses,
    norm_addr,
    pick_dex_addresses,
)
from crypto_bot.models.market import ContractInfo

logger = structlog.get_logger(__name__)

_CATALOG_TTL = 1800.0
_RESOLVE_TTL = 600.0

_CG_MARKET = {
    "binance": "binance",
    "binance_us": "binance",
    "bybit": "bybit",
    "bybit_spot": "bybit",
    "gate": "gate",
    "gate_io": "gate",
    "gateio": "gate",
    "mexc": "mexc",
    "mxc": "mexc",
    "bitget": "bitget",
    "bitget_futures": "bitget",
    "okx": "okx",
    "okex": "okx",
    "kucoin": "kucoin",
    "bingx": "bingx",
    "kraken": "kraken",
    "hyperliquid": "hyperliquid",
    "aster": "aster",
    "asterdex": "aster",
}

_CMC_SLUG = {
    "binance": "binance",
    "bybit": "bybit",
    "gate-io": "gate",
    "gateio": "gate",
    "mexc": "mexc",
    "bitget": "bitget",
    "okx": "okx",
    "kucoin": "kucoin",
    "bingx": "bingx",
    "kraken": "kraken",
    "hyperliquid": "hyperliquid",
    "aster": "aster",
    "aster-dex": "aster",
}

_CONTRACT_KEYS = (
    "contract",
    "contractAddress",
    "contract_address",
    "tokenAddress",
    "token_address",
    "address",
    "addr",
)


def _extract_contracts(payload: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in _CONTRACT_KEYS:
        addr = norm_addr(payload.get(key))
        if addr:
            found.append(addr)
    for nest_key in ("networkList", "chains", "chainList", "networks"):
        rows = payload.get(nest_key)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    found.extend(_extract_contracts(row))
        elif isinstance(rows, dict):
            for row in rows.values():
                if isinstance(row, dict):
                    found.extend(_extract_contracts(row))
    return found


@dataclass
class AliasResolution:
    query: str
    by_exchange: dict[str, str] = field(default_factory=dict)

    def listed(self, exchange: str, fallback: str) -> str:
        return self.by_exchange.get(exchange, fallback).upper()


class TokenAliasService:
    """Map a user ticker to per-exchange listed symbols of the same contract."""

    def __init__(self) -> None:
        self._http = get_http()
        self._cmc = CoinMarketCapClient()
        self._catalog: tuple[float, dict[str, dict[str, str]]] | None = None
        self._resolve_cache: dict[str, tuple[float, AliasResolution]] = {}

    async def resolve(
        self,
        symbol: str,
        contracts: list[ContractInfo] | None = None,
    ) -> AliasResolution:
        sym = symbol.strip().upper()
        hit = self._resolve_cache.get(sym)
        if hit and time.time() - hit[0] < _RESOLVE_TTL:
            return hit[1]
        try:
            return await self._resolve_inner(sym, contracts)
        except Exception:
            logger.debug("token_alias_resolve_skip", symbol=sym)
            return AliasResolution(query=sym)

    async def _resolve_inner(
        self,
        sym: str,
        contracts: list[ContractInfo] | None,
    ) -> AliasResolution:

        addrs = {a for c in (contracts or []) if (a := norm_addr(c.address))}
        dex_pairs: list[Any] = []
        try:
            dex_pairs = await self._dex_search(sym)
        except Exception:
            logger.debug("dex_alias_search_skip", symbol=sym)

        addrs.update(pick_dex_addresses(sym, dex_pairs))
        extras = set(dex_symbols_for_addresses(dex_pairs, addrs))
        extras.add(sym)

        catalog = await self._ensure_catalog()
        for addr, mapping in catalog.items():
            if any(coin == sym for coin in mapping.values()):
                addrs.add(addr)
        by_ex: dict[str, str] = {}
        for addr in addrs:
            for exchange, coin in (catalog.get(addr) or {}).items():
                if coin and coin != sym:
                    by_ex[exchange] = coin
                    extras.add(coin)

        cg_map, cmc_map = await asyncio.gather(
            self._coingecko_tickers(sym, addrs),
            self._cmc_market_pairs(sym),
            return_exceptions=True,
        )
        if isinstance(cg_map, dict):
            by_ex.update({k: v for k, v in cg_map.items() if v})
            extras.update(cg_map.values())
        if isinstance(cmc_map, dict):
            by_ex.update({k: v for k, v in cmc_map.items() if v})
            extras.update(cmc_map.values())

        if addrs:
            probed = await self._probe_gate(extras, addrs)
            if probed:
                by_ex["gate"] = probed

        by_ex = {k: v.upper() for k, v in by_ex.items() if v and v.upper() != sym}
        result = AliasResolution(query=sym, by_exchange=by_ex)
        self._resolve_cache[sym] = (time.time(), result)
        if by_ex:
            logger.info("token_aliases", symbol=sym, aliases=by_ex)
        return result

    async def _dex_search(self, symbol: str) -> list[Any]:
        data = await self._http.get_json(
            "https://api.dexscreener.com/latest/dex/search",
            params={"q": symbol},
            timeout=2.8,
        )
        if isinstance(data, dict):
            pairs = data.get("pairs") or []
            return pairs if isinstance(pairs, list) else []
        return []

    async def _ensure_catalog(self) -> dict[str, dict[str, str]]:
        now = time.time()
        if self._catalog and now - self._catalog[0] < _CATALOG_TTL:
            return self._catalog[1]
        parts = await asyncio.gather(
            self._load_mexc(),
            self._load_binance(),
            self._load_bitget(),
            self._load_kucoin(),
            return_exceptions=True,
        )
        merged: dict[str, dict[str, str]] = {}
        for part in parts:
            if not isinstance(part, dict):
                continue
            for addr, mapping in part.items():
                merged.setdefault(addr, {}).update(mapping)
        self._catalog = (now, merged)
        return merged

    def _index_coin(self, store: dict[str, dict[str, str]], exchange: str, coin: str, payload: dict[str, Any]) -> None:
        ticker = str(coin or "").strip().upper()
        if not ticker:
            return
        for addr in _extract_contracts(payload):
            store.setdefault(addr, {})[exchange] = ticker

    async def _load_mexc(self) -> dict[str, dict[str, str]]:
        from crypto_bot.clients.dw.mexc import MexcDwClient

        store: dict[str, dict[str, str]] = {}
        client = MexcDwClient(self._http)
        try:
            rows = await client._load_all()
        except Exception:
            return store
        for item in rows:
            if isinstance(item, dict):
                self._index_coin(store, "mexc", str(item.get("coin") or ""), item)
        return store

    async def _load_binance(self) -> dict[str, dict[str, str]]:
        store: dict[str, dict[str, str]] = {}
        data = await self._http.get_json(
            "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll",
            timeout=8,
        )
        coins = (data or {}).get("data") or [] if isinstance(data, dict) else []
        for item in coins:
            if isinstance(item, dict):
                payload = dict(item)
                payload["networkList"] = item.get("networkList") or []
                self._index_coin(store, "binance", str(item.get("coin") or ""), payload)
        return store

    async def _load_bitget(self) -> dict[str, dict[str, str]]:
        store: dict[str, dict[str, str]] = {}
        data = await self._http.get_json(
            "https://api.bitget.com/api/v2/spot/public/coins",
            timeout=8,
        )
        rows = data.get("data") if isinstance(data, dict) else None
        for item in rows or []:
            if isinstance(item, dict):
                self._index_coin(store, "bitget", str(item.get("coin") or ""), item)
        return store

    async def _load_kucoin(self) -> dict[str, dict[str, str]]:
        store: dict[str, dict[str, str]] = {}
        data = await self._http.get_json(
            "https://api.kucoin.com/api/v3/currencies",
            timeout=8,
        )
        rows = data.get("data") if isinstance(data, dict) else None
        for item in rows or []:
            if isinstance(item, dict):
                self._index_coin(
                    store,
                    "kucoin",
                    str(item.get("currency") or item.get("name") or ""),
                    item,
                )
        return store

    async def _probe_gate(self, candidates: set[str], addrs: set[str]) -> str | None:
        query = next(iter(addrs)) if addrs else ""
        names = []
        for cand in candidates:
            c = cand.strip().upper()
            if c and c not in names and 2 <= len(c) <= 20:
                names.append(c)
        names = names[:6]

        async def _one(name: str) -> str | None:
            data = await self._http.get_json(
                "https://api.gateio.ws/api/v4/wallet/currency_chains",
                params={"currency": name},
                timeout=4.5,
            )
            rows = data if isinstance(data, list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                addr = norm_addr(row.get("contract_address") or row.get("addr"))
                if addr and addr in addrs:
                    return name
            return None

        if not names or not addrs:
            return None
        results = await asyncio.gather(*[_one(n) for n in names], return_exceptions=True)
        for item in results:
            if isinstance(item, str) and item:
                return item
        _ = query
        return None

    async def _coingecko_tickers(self, symbol: str, addrs: set[str]) -> dict[str, str]:
        search = await self._http.get_json(
            "https://api.coingecko.com/api/v3/search",
            params={"query": symbol},
            timeout=3.0,
        )
        coin_id = None
        if isinstance(search, dict):
            for coin in search.get("coins") or []:
                if str(coin.get("symbol") or "").upper() == symbol:
                    coin_id = coin.get("id")
                    break
            if not coin_id:
                for coin in (search.get("coins") or [])[:3]:
                    coin_id = coin.get("id")
                    break
        if not coin_id:
            return {}
        detail = await self._http.get_json(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "true",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
            },
            timeout=4.5,
        )
        if not isinstance(detail, dict):
            return {}
        if addrs:
            platforms = detail.get("platforms") or {}
            detail_plats = detail.get("detail_platforms") or {}
            cg_addrs: set[str] = set()
            for val in platforms.values():
                a = norm_addr(val if isinstance(val, str) else None)
                if a:
                    cg_addrs.add(a)
            for info in detail_plats.values():
                if isinstance(info, dict):
                    a = norm_addr(info.get("contract_address"))
                    if a:
                        cg_addrs.add(a)
            if cg_addrs and not (cg_addrs & addrs):
                return {}
        out: dict[str, str] = {}
        for tick in detail.get("tickers") or []:
            if not isinstance(tick, dict):
                continue
            market = str((tick.get("market") or {}).get("identifier") or "").lower()
            exchange = _CG_MARKET.get(market)
            base = str(tick.get("base") or "").strip().upper()
            if not exchange or not base or base.startswith("0X") or len(base) > 20:
                continue
            if exchange not in out:
                out[exchange] = base
        return out

    async def _cmc_market_pairs(self, symbol: str) -> dict[str, str]:
        from crypto_bot.config.settings import get_settings

        if not get_settings().cmc_key():
            return {}
        data = await self._http.get_json(
            "https://pro-api.coinmarketcap.com/v1/cryptocurrency/market-pairs/latest",
            headers=self._cmc._headers(),
            params={"symbol": symbol, "limit": 50, "category": "spot"},
            timeout=3.0,
        )
        if not isinstance(data, dict):
            return {}
        block = data.get("data") or {}
        pairs = block.get("market_pairs") if isinstance(block, dict) else None
        out: dict[str, str] = {}
        for row in pairs or []:
            if not isinstance(row, dict):
                continue
            slug = str((row.get("exchange") or {}).get("slug") or "").lower()
            exchange = _CMC_SLUG.get(slug)
            base = str((row.get("market_pair_base") or {}).get("currency_symbol") or "").strip().upper()
            if not exchange or not base or len(base) > 20:
                continue
            if exchange not in out:
                out[exchange] = base
        return out
