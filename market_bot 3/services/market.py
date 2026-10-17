"""Build full /get report."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator
from dataclasses import replace

from market_bot.config import get_settings
from market_bot.models import Contract, ExchangeReport, MarketReport
from market_bot.parsers import aliases, contracts, dex, prices, wallets
from market_bot.parsers.exchanges import DW_EXCHANGES, EXCHANGES
from market_bot.utils.cache import TtlCache
from market_bot.utils.formatter import format_report

logger = logging.getLogger(__name__)

FAST_CACHE_TTL_SEC = 12.0
BUILD_BUDGET_SEC = 0.88


def _task_value(task: asyncio.Task, default):
    if not task.done() or task.cancelled():
        return default
    try:
        return task.result()
    except Exception:
        return default


class MarketService:
    """Eagerly initialized at bot startup — no lazy globals."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._cache = TtlCache[str](FAST_CACHE_TTL_SEC)
        self._inflight: dict[str, asyncio.Task[str]] = {}

    async def startup(self) -> None:
        from market_bot.utils.keepalive import warm_all

        await warm_all(sample_report=self.get_report)

    def _store(self, key: str, text: str) -> None:
        self._cache.set(key, text)

    def _attach_wallets(
        self, exchanges: list[ExchangeReport], wallet_map: dict
    ) -> None:
        for ex in exchanges:
            if ex.key in DW_EXCHANGES and ex.has_data:
                if not ex.networks:
                    ex.networks = wallet_map.get(ex.key) or []

    async def _extras(self, symbol: str) -> tuple[list, dict]:
        async def _contracts_with_dex() -> list:
            raw = await contracts.fetch_contracts(symbol)
            return await dex.enrich_contracts(raw)

        contract_list, wallet_map = await asyncio.gather(
            _contracts_with_dex(),
            wallets.fetch_all_networks(symbol),
        )
        return contract_list, wallet_map

    async def get_report_fast(self, symbol: str) -> str:
        return await self.get_report(symbol)

    async def get_report(self, symbol: str, quote: str | None = None) -> str:
        quote = (quote or self._settings.default_quote).upper()
        sym = symbol.strip().upper()
        key = f"{sym}:{quote}"
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        inflight = self._inflight.get(key)
        if inflight is not None and not inflight.done():
            return await inflight
        task = asyncio.create_task(self._build_text(sym, quote))
        self._inflight[key] = task
        try:
            return await task
        finally:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    async def _build_text(self, symbol: str, quote: str) -> str:
        report = await self._build(symbol, quote)
        text = format_report(symbol, report.exchanges, report.contracts)
        missed = any(
            getattr(e, "timed_out", False) and not e.has_data for e in report.exchanges
        )
        if not missed:
            self._store(f"{symbol}:{quote}", text)
        return text

    async def iter_report(self, symbol: str) -> AsyncIterator[tuple[str, bool]]:
        quote = self._settings.default_quote.upper()
        sym = symbol.strip().upper()
        key = f"{sym}:{quote}"

        fresh = self._cache.get(key)
        if fresh is not None:
            yield fresh, True
            return

        inflight = self._inflight.get(key)
        if inflight is not None and not inflight.done():
            yield await inflight, True
            return

        text = await self.get_report(sym, quote)
        yield text, True

    async def _build(self, symbol: str, quote: str) -> MarketReport:
        deadline = asyncio.get_running_loop().time() + BUILD_BUDGET_SEC
        cands = aliases.quick_candidates(symbol)
        extra_cands = [c for c in cands if c != symbol.upper()][:4]
        close_alts = [
            c for c in extra_cands if aliases.edit_distance(c, symbol.upper()) <= 2
        ]
        full_alt = close_alts[0] if close_alts else None
        lookup = full_alt or symbol
        prices_task = asyncio.create_task(prices.fetch_all_prices(lookup, quote))
        wallet_task = asyncio.create_task(wallets.fetch_all_networks(lookup))
        contracts_task = asyncio.create_task(contracts.fetch_contracts(lookup))

        async def _alias_wave() -> tuple[list[ExchangeReport], dict[tuple[str, str], list]]:
            reports: list[ExchangeReport] = []
            extra: dict[tuple[str, str], list] = {}
            rest = [c for c in extra_cands if c != full_alt][:3]
            filled: set[str] = set()
            if prices_task.done() and not prices_task.cancelled():
                try:
                    for e in prices_task.result() or []:
                        if isinstance(e, ExchangeReport) and e.has_data:
                            filled.add(e.key)
                except Exception:
                    pass
            alias_keys = {"aster", "gate", "bingx", "bybit"}
            if filled:
                metas = [m for m in EXCHANGES if m.key in alias_keys and m.key not in filled]
            else:
                metas = [m for m in EXCHANGES if m.key in alias_keys]
            jobs: list[tuple[str, str, object]] = []
            pairs = [(cand, meta) for cand in rest for meta in metas][:12]
            for cand, meta in pairs:
                jobs.append(("px", cand, prices.fetch_exchange(meta, cand, quote)))
            for cand in rest:
                jobs.append(("dw", cand, wallets.fetch_networks("gate", cand)))
            if not jobs:
                return reports, extra
            raw = await asyncio.gather(
                *[job[2] for job in jobs],
                return_exceptions=True,
            )
            for (kind, cand, _coro), item in zip(jobs, raw):
                if kind == "px" and isinstance(item, ExchangeReport):
                    if item.has_data:
                        item.listed_as = cand
                    reports.append(item)
                elif kind == "dw" and isinstance(item, list) and item:
                    extra[("gate", cand)] = item
            return reports, extra

        first = {prices_task, wallet_task, contracts_task}
        await asyncio.wait(first, timeout=0.35)
        alias_wave = (
            asyncio.create_task(_alias_wave())
            if extra_cands
            else None
        )

        async def _enrich_when_ready():
            await asyncio.wait({contracts_task}, timeout=0.40)
            raw = _task_value(contracts_task, [])
            if not isinstance(raw, list) or not raw:
                return []
            slim = contracts.slim_for_display(raw)
            ref_px = None
            if prices_task.done():
                exs = _task_value(prices_task, [])
                ref_px = self._ref_price(exs if isinstance(exs, list) else [])
            if slim and all(getattr(c, "price", None) for c in slim):
                return dex.attach_explorer_urls(slim)
            try:
                return await dex.enrich_contracts(slim, ref=ref_px)
            except Exception:
                return dex.attach_explorer_urls(slim)

        enrich_task = asyncio.create_task(_enrich_when_ready())
        pending = {prices_task, wallet_task, contracts_task, enrich_task}
        await asyncio.wait(
            pending,
            timeout=max(0.15, deadline - asyncio.get_running_loop().time()),
        )
        if not enrich_task.done():
            left = deadline - asyncio.get_running_loop().time()
            if left > 0.05:
                await asyncio.wait({enrich_task}, timeout=left)

        exchanges = _task_value(prices_task, [])
        raw_contracts = _task_value(contracts_task, [])
        wallet_map = _task_value(wallet_task, {})
        enriched = _task_value(enrich_task, None)
        if not isinstance(exchanges, list):
            exchanges = []
        if not isinstance(raw_contracts, list):
            raw_contracts = []
        if not isinstance(wallet_map, dict):
            wallet_map = {}
        if full_alt:
            for e in exchanges:
                if isinstance(e, ExchangeReport) and e.has_data and not e.listed_as:
                    e.listed_as = full_alt

        extra_ca: list[Contract] = []
        for nets in wallet_map.values():
            for n in nets or []:
                addr = (getattr(n, "address", None) or "").strip()
                chain = (getattr(n, "name", None) or "").strip().upper()
                if addr and chain and not contracts.is_dummy_address(addr):
                    extra_ca.append(Contract(chain, addr))
        if isinstance(enriched, list) and enriched:
            contract_list = contracts.slim_for_display(extra_ca + enriched)
        else:
            contract_list = contracts.slim_for_display(extra_ca + raw_contracts)
            contract_list = dex.attach_explorer_urls(contract_list)

        alias_pack = _task_value(alias_wave, ([], {})) if alias_wave else ([], {})
        alias_reports, extra_dw = alias_pack if isinstance(alias_pack, tuple) else ([], {})

        ref = self._ref_price(exchanges)
        if ref is None:
            ref = self._ref_price(alias_reports if isinstance(alias_reports, list) else [])
        by_key = {e.key: e for e in exchanges if isinstance(e, ExchangeReport)}
        used_tickers: dict[str, str] = {}
        self._merge_aliases(by_key, used_tickers, alias_reports, ref, cands, symbol)
        mexc = by_key.get("mexc")
        mexc_px = None
        if mexc is not None and mexc.has_data:
            mexc_px = (
                mexc.spot.price
                if mexc.spot
                else mexc.futures.price
                if mexc.futures
                else None
            )
        extra_dw = extra_dw if isinstance(extra_dw, dict) else {}
        for key, tick in used_tickers.items():
            nets = extra_dw.get((key, tick))
            if nets:
                wallet_map[key] = nets

        token_addrs = {
            (c.address or "").strip().lower()
            for c in contract_list
            if c.address and not contracts.is_dummy_address(c.address)
        }
        if mexc_px:
            for key, item in list(by_key.items()):
                if key == "mexc" or not item.has_data:
                    continue
                nets = item.networks or wallet_map.get(key) or []
                item.networks = nets
                same = self._nets_match_token(nets, token_addrs)
                closed = self._deposits_closed(nets)
                kept = self._keep_matching_markets(
                    replace(item), mexc_px, same_token=same, dep_closed=closed
                )
                if kept is None:
                    by_key.pop(key, None)
                    used_tickers.pop(key, None)
                else:
                    by_key[key] = kept
        exchanges = [by_key[m.key] for m in EXCHANGES if m.key in by_key]

        self._attach_wallets(exchanges, wallet_map)
        hl_ex = by_key.get("hyperliquid")
        hl_px = None
        if hl_ex is not None and hl_ex.has_data:
            hl_px = hl_ex.spot.price if hl_ex.spot else (
                hl_ex.futures.price if hl_ex.futures else None
            )
        if hl_px:
            contract_list = [
                Contract(c.network, c.address, price=hl_px, url=c.url)
                if c.price is None and (c.network or "").upper() == "HYPERLIQUID"
                else c
                for c in contract_list
            ]
        dex_ref = mexc_px or ref
        if dex_ref:
            filled = []
            for c in contract_list:
                if c.price is None and (c.network or "").upper() in {"ETH", "HYPERLIQUID"}:
                    filled.append(Contract(c.network, c.address, price=float(dex_ref), url=c.url))
                else:
                    filled.append(c)
            contract_list = filled
        contract_list = self._sanitize_dex_prices(contract_list, dex_ref)
        return MarketReport(symbol=symbol, contracts=contract_list, exchanges=exchanges)

    def _merge_aliases(
        self,
        by_key: dict[str, ExchangeReport],
        used_tickers: dict[str, str],
        items: list[ExchangeReport],
        ref: float | None,
        cands: list[str],
        symbol: str,
    ) -> None:
        for item in items:
            if not item.has_data:
                continue
            px_spot = item.spot.price if item.spot else None
            px_fut = item.futures.price if item.futures else None
            if not (
                aliases.price_sane(ref, px_spot) or aliases.price_sane(ref, px_fut)
            ):
                continue
            kept = self._keep_matching_markets(item, ref) if ref else item
            if kept is None:
                continue
            prev = by_key.get(item.key)
            if prev is not None and prev.has_data:
                continue
            by_key[item.key] = kept
            tick = kept.listed_as or self._ticker_from_report(kept, cands, symbol)
            kept.listed_as = tick
            used_tickers[item.key] = tick

    def _sanitize_dex_prices(
        self, items: list, ref: float | None
    ) -> list:
        matching = []
        preferred = []
        for c in items:
            if contracts.is_dummy_address(getattr(c, "address", "") or ""):
                continue
            px = getattr(c, "price", None)
            if px and ref and not aliases.price_sane(ref, float(px), max_ratio=4.0):
                continue
            if px and ref:
                matching.append((abs(math.log(float(px) / ref)), c))
            elif px:
                matching.append((0.0, c))
            elif c.network in contracts._PREF_NETS:
                preferred.append(c)
        if matching:
            matching.sort(key=lambda x: x[0])
            by_net: dict[str, object] = {}
            for _dist, c in matching:
                if c.network not in by_net:
                    by_net[c.network] = c
            have = set(by_net)
            for c in preferred:
                if c.network not in have:
                    by_net[c.network] = c
                    have.add(c.network)
            return list(by_net.values())
        return preferred

    def _nets_match_token(self, nets: list, token_addrs: set[str]) -> bool:
        if not token_addrs:
            return False
        for n in nets or []:
            addr = (getattr(n, "address", None) or "").strip().lower()
            if addr and addr in token_addrs:
                return True
        return False

    def _deposits_closed(self, nets: list) -> bool:
        flags = [n.deposit for n in (nets or []) if n.deposit is not None]
        return bool(flags) and not any(flags)

    def _keep_matching_markets(
        self,
        item: ExchangeReport,
        ref: float,
        *,
        same_token: bool = False,
        dep_closed: bool = False,
    ) -> ExchangeReport | None:
        """Same contract → keep all books. Closed deps → allow ~20x. Else ~10x. Never drop a side just because the other is closer."""
        if same_token:
            max_r = 50.0
        elif dep_closed:
            max_r = 20.0
        else:
            max_r = 10.0
        spot_ok = item.spot is not None and aliases.price_sane(
            ref, item.spot.price, max_ratio=max_r
        )
        fut_ok = item.futures is not None and aliases.price_sane(
            ref, item.futures.price, max_ratio=max_r
        )
        if same_token:
            if item.spot or item.futures:
                if not spot_ok:
                    item.spot = None
                    if item.futures:
                        item.futures_only = True
                if not fut_ok:
                    item.futures = None
                return item if item.has_data else None
        if not spot_ok and not fut_ok:
            return None
        if not spot_ok:
            item.spot = None
            item.futures_only = True
        if not fut_ok:
            item.futures = None
        return item

    def _ticker_from_report(
        self, item: ExchangeReport, cands: list[str], query: str
    ) -> str:
        url = ""
        if item.spot and item.spot.url:
            url = item.spot.url.upper()
        elif item.futures and item.futures.url:
            url = item.futures.url.upper()
        for cand in sorted(
            (c for c in cands if c != query.upper()),
            key=len,
            reverse=True,
        ):
            if cand in url:
                return cand
        return query.upper()

    def _ref_price(self, exchanges: list[ExchangeReport]) -> float | None:
        prefer = ("mexc", "gate", "bybit", "binance", "bitget", "okx")
        by_key = {e.key: e for e in exchanges if isinstance(e, ExchangeReport)}
        for key in prefer:
            ex = by_key.get(key)
            if ex is None:
                continue
            if ex.spot:
                return ex.spot.price
            if ex.futures:
                return ex.futures.price
        for ex in exchanges:
            if isinstance(ex, ExchangeReport) and ex.spot:
                return ex.spot.price
            if isinstance(ex, ExchangeReport) and ex.futures:
                return ex.futures.price
        return None
