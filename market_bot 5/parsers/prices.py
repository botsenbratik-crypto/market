"""Spot / futures / funding fetchers."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from dataclasses import replace

from market_bot.models import ExchangeReport, Ticker
from market_bot.parsers.bingx_symbols import bingx_pair
from market_bot.parsers.hyperliquid_symbols import hl_books
from market_bot.parsers.kraken_symbols import kraken_ticker_pairs
from market_bot.parsers.exchanges import EXCHANGES, ExchangeMeta
from market_bot.utils import links
from market_bot.utils.cache import TtlCache
from market_bot.utils.http import get_json

_last_good = TtlCache[ExchangeReport](180.0)
_kraken_futures_cache = TtlCache[list[dict]](60.0)
_bg_px: set[asyncio.Task] = set()


def _f(v: Any) -> float | None:
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def _rate(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _funding_pct(rate: float | None) -> float | None:
    return None if rate is None else rate * 100.0


def _norm_ts_ms(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        ts = int(raw)
    except (TypeError, ValueError):
        return None
    return ts if ts > 1_000_000_000_000 else ts * 1000


def _estimate_next_funding_ms(interval_hours: int = 8) -> int:
    """Fallback countdown when exchange API omits next funding time."""
    period = interval_hours * 3600
    now = int(time.time())
    return ((now // period) + 1) * period * 1000


async def _binance(base: str, quote: str) -> ExchangeReport:
    sym = f"{base}{quote}"
    spot_j, fut_j = await asyncio.gather(
        get_json("https://api.binance.com/api/v3/ticker/price", params={"symbol": sym}),
        get_json("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": sym}),
    )
    spot = fut = None
    if spot_j and spot_j.get("price"):
        spot = Ticker(float(spot_j["price"]), url=links.spot_url("binance", base, quote))
    if fut_j and fut_j.get("markPrice"):
        fut = Ticker(
            float(fut_j["markPrice"]),
            url=links.futures_url("binance", base, quote),
            funding_pct=_funding_pct(_rate(fut_j.get("lastFundingRate"))),
            next_funding_ms=_norm_ts_ms(fut_j.get("nextFundingTime")),
        )
    return ExchangeReport("binance", "Binance", spot=spot, futures=fut)


async def _bybit(base: str, quote: str) -> ExchangeReport:
    sym = f"{base}{quote}"
    spot_j, fut_j = await asyncio.gather(
        get_json("https://api.bybit.com/v5/market/tickers", params={"category": "spot", "symbol": sym}),
        get_json("https://api.bybit.com/v5/market/tickers", params={"category": "linear", "symbol": sym}),
    )
    spot = fut = None
    if spot_j and spot_j.get("result", {}).get("list"):
        row = spot_j["result"]["list"][0]
        if row.get("lastPrice"):
            spot = Ticker(float(row["lastPrice"]), url=links.spot_url("bybit", base, quote))
    if fut_j and fut_j.get("result", {}).get("list"):
        row = fut_j["result"]["list"][0]
        if row.get("lastPrice"):
            fut = Ticker(
                float(row["lastPrice"]),
                url=links.futures_url("bybit", base, quote),
                funding_pct=_funding_pct(_rate(row.get("fundingRate"))),
                next_funding_ms=_norm_ts_ms(row.get("nextFundingTime")),
            )
    return ExchangeReport("bybit", "Bybit", spot=spot, futures=fut)


_EXCHANGE_TIMEOUTS: dict[str, float] = {
    "hyperliquid": 0.55,
    "kraken": 0.82,
    "coinbase": 0.55,
}
_DEFAULT_EXCHANGE_TIMEOUT = 0.55
_FETCH_ALL_WAIT_SEC = 0.82


async def _gate(base: str, quote: str) -> ExchangeReport:
    pair = f"{base}_{quote}"
    spot_j, fut_j, contract_j = await asyncio.gather(
        get_json("https://api.gateio.ws/api/v4/spot/tickers", params={"currency_pair": pair}),
        get_json("https://api.gateio.ws/api/v4/futures/usdt/tickers", params={"contract": pair}),
        get_json(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{pair}", timeout=0.5),
    )
    spot = fut = None
    if isinstance(spot_j, list) and spot_j and spot_j[0].get("last"):
        spot = Ticker(float(spot_j[0]["last"]), url=links.spot_url("gate", base, quote))
    if isinstance(fut_j, list) and fut_j and fut_j[0].get("last"):
        fr = _rate(fut_j[0].get("funding_rate"))
        nft = _estimate_next_funding_ms(8)
        if isinstance(contract_j, dict):
            fr = fr or _rate(contract_j.get("funding_rate"))
            nft = _norm_ts_ms(contract_j.get("funding_next_apply")) or nft
        fut = Ticker(
            float(fut_j[0]["last"]),
            url=links.futures_url("gate", base, quote),
            funding_pct=_funding_pct(fr),
            next_funding_ms=nft,
        )
    return ExchangeReport("gate", "Gate.io", spot=spot, futures=fut)


async def _mexc(base: str, quote: str) -> ExchangeReport:
    sym = f"{base}{quote}"
    pair = f"{base}_{quote}"
    spot_j, fut_j, fund_j = await asyncio.gather(
        get_json("https://api.mexc.com/api/v3/ticker/price", params={"symbol": sym}),
        get_json("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol": pair}),
        get_json(f"https://contract.mexc.com/api/v1/contract/funding_rate/{pair}"),
    )
    spot = fut = None
    if spot_j and spot_j.get("price"):
        spot = Ticker(float(spot_j["price"]), url=links.spot_url("mexc", base, quote))
    if fut_j and fut_j.get("data"):
        row = fut_j["data"]
        px = _f(row.get("lastPrice") or row.get("fairPrice"))
        fr = _rate(row.get("fundingRate"))
        nft = None
        if fund_j and fund_j.get("data"):
            fd = fund_j["data"]
            fr = fr or _rate(fd.get("fundingRate"))
            nft = _norm_ts_ms(fd.get("nextSettleTime"))
        if nft is None:
            nft = _estimate_next_funding_ms(8)
        if px:
            fut = Ticker(
                px,
                url=links.futures_url("mexc", base, quote),
                funding_pct=_funding_pct(fr),
                next_funding_ms=nft,
            )
    return ExchangeReport("mexc", "MEXC", spot=spot, futures=fut)


async def _bitget(base: str, quote: str) -> ExchangeReport:
    sym = f"{base}{quote}"
    spot_j, fut_j, fund_j = await asyncio.gather(
        get_json("https://api.bitget.com/api/v2/spot/market/tickers", params={"symbol": sym}),
        get_json(
            "https://api.bitget.com/api/v2/mix/market/ticker",
            params={"symbol": sym, "productType": "USDT-FUTURES"},
        ),
        get_json(
            "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
            params={"symbol": sym, "productType": "USDT-FUTURES"},
        ),
    )
    spot = fut = None
    if spot_j and spot_j.get("data"):
        rows = spot_j["data"]
        row = rows[0] if isinstance(rows, list) and rows else rows
        if isinstance(row, dict) and row.get("lastPr"):
            spot = Ticker(float(row["lastPr"]), url=links.spot_url("bitget", base, quote))
    fr = nft = None
    if fund_j and fund_j.get("data"):
        fr_row = fund_j["data"][0] if isinstance(fund_j["data"], list) else fund_j["data"]
        if isinstance(fr_row, dict):
            fr = _rate(fr_row.get("fundingRate"))
            nft = _norm_ts_ms(fr_row.get("nextUpdate"))
    if fut_j and fut_j.get("data"):
        rows = fut_j["data"]
        row = rows[0] if isinstance(rows, list) and rows else rows
        if isinstance(row, dict) and row.get("lastPr"):
            fut = Ticker(
                float(row["lastPr"]),
                url=links.futures_url("bitget", base, quote),
                funding_pct=_funding_pct(fr),
                next_funding_ms=nft,
            )
    return ExchangeReport("bitget", "Bitget", spot=spot, futures=fut)


async def _okx(base: str, quote: str) -> ExchangeReport:
    inst = f"{base}-{quote}"
    swap = f"{inst}-SWAP"
    spot_j, fut_j, fund_j = await asyncio.gather(
        get_json("https://www.okx.com/api/v5/market/ticker", params={"instId": inst}),
        get_json("https://www.okx.com/api/v5/market/ticker", params={"instId": swap}),
        get_json("https://www.okx.com/api/v5/public/funding-rate", params={"instId": swap}),
    )
    spot = fut = None
    if spot_j and spot_j.get("data"):
        row = spot_j["data"][0]
        if row.get("last"):
            spot = Ticker(float(row["last"]), url=links.spot_url("okx", base, quote))
    if fut_j and fut_j.get("data"):
        row = fut_j["data"][0]
        fr = nft = None
        if fund_j and fund_j.get("data"):
            fr_row = fund_j["data"][0]
            fr = _rate(fr_row.get("fundingRate"))
            nft = _norm_ts_ms(fr_row.get("fundingTime") or fr_row.get("nextFundingTime"))
        if row.get("last"):
            fut = Ticker(
                float(row["last"]),
                url=links.futures_url("okx", base, quote),
                funding_pct=_funding_pct(fr),
                next_funding_ms=nft,
            )
    return ExchangeReport("okx", "OKX", spot=spot, futures=fut)


async def _kucoin(base: str, quote: str) -> ExchangeReport:
    sym = f"{base}-{quote}"
    fut_sym = "XBTUSDTM" if base.upper() == "BTC" else f"{base}{quote}M"
    spot_j, fut_j, fund_j = await asyncio.gather(
        get_json("https://api.kucoin.com/api/v1/market/orderbook/level1", params={"symbol": sym}),
        get_json("https://api-futures.kucoin.com/api/v1/ticker", params={"symbol": fut_sym}),
        get_json(f"https://api-futures.kucoin.com/api/v1/funding-rate/{fut_sym}/current"),
    )
    spot = fut = None
    spot_data = (spot_j or {}).get("data")
    if isinstance(spot_data, dict) and spot_data.get("price"):
        spot = Ticker(float(spot_data["price"]), url=links.spot_url("kucoin", base, quote))
    fr = nft = None
    if fund_j and fund_j.get("data"):
        fr = _rate(fund_j["data"].get("value"))
        nft = _norm_ts_ms(fund_j["data"].get("fundingTime"))
    fut_data = (fut_j or {}).get("data")
    if isinstance(fut_data, dict):
        px = _f(fut_data.get("price"))
        if px:
            fut = Ticker(
                px,
                url=links.futures_url("kucoin", base, quote),
                funding_pct=_funding_pct(fr),
                next_funding_ms=nft,
            )
    return ExchangeReport("kucoin", "KuCoin", spot=spot, futures=fut)


async def _bingx(base: str, quote: str) -> ExchangeReport:
    sym = await bingx_pair(base, quote)
    api_base = (sym.split("-")[0] if "-" in (sym or "") else base).upper()
    spot_j, fut_j, prem_j = await asyncio.gather(
        get_json(
            "https://open-api.bingx.com/openApi/spot/v1/ticker/24hr",
            params={"symbol": sym},
            timeout=0.50,
        ),
        get_json(
            "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
            params={"symbol": sym},
            timeout=0.50,
        ),
        get_json(
            "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex",
            params={"symbol": sym},
            timeout=0.50,
        ),
    )
    spot = fut = None
    if spot_j and spot_j.get("data"):
        row = spot_j["data"]
        if isinstance(row, list):
            row = row[0] if row else {}
        px = row.get("lastPrice") or row.get("price") if isinstance(row, dict) else None
        if px:
            spot = Ticker(float(px), url=links.spot_url("bingx", api_base, quote))
    fr = nft = None
    if prem_j and prem_j.get("data"):
        pd = prem_j["data"]
        fr = _rate(pd.get("lastFundingRate"))
        nft = _norm_ts_ms(pd.get("nextFundingTime"))
    if fut_j and fut_j.get("data") and fut_j["data"].get("lastPrice"):
        row = fut_j["data"]
        if fr is None:
            fr = _rate(row.get("fundingRate"))
        if nft is None:
            nft = _estimate_next_funding_ms(8)
        fut = Ticker(
            float(row["lastPrice"]),
            url=links.futures_url("bingx", api_base, quote),
            funding_pct=_funding_pct(fr),
            next_funding_ms=nft,
        )
    listed = api_base if api_base != base.upper() else None
    return ExchangeReport("bingx", "BingX", spot=spot, futures=fut, listed_as=listed)


async def prefetch_kraken_futures() -> None:
    await _kraken_futures_tickers()


async def _hyperliquid(base: str, quote: str) -> ExchangeReport:
    spot_px, fut_px, spot_url, fut_url = await hl_books(base)
    spot = Ticker(spot_px, url=spot_url) if spot_px else None
    fut = Ticker(fut_px, url=fut_url) if fut_px else None
    return ExchangeReport(
        "hyperliquid",
        "Hyperliquid",
        spot=spot,
        futures=fut,
        futures_only=spot is None and fut is not None,
    )


async def _kraken_futures_tickers() -> list[dict]:
    hit = _kraken_futures_cache.get("tickers")
    if hit is not None:
        return hit
    fut_j = await get_json("https://futures.kraken.com/derivatives/api/v3/tickers", timeout=0.7)
    tickers = (fut_j or {}).get("tickers") or []
    if tickers:
        _kraken_futures_cache.set("tickers", tickers)
    return tickers


def _kraken_pair_quote(pair_key: str, default: str) -> str:
    p = pair_key.upper().replace("/", "")
    if "USDT" in p:
        return "USDT"
    if p.endswith("USD") or "ZUSD" in p:
        return "USD"
    return default


def _kraken_spot_from_ticker(spot_j: Any, pairs: list[str], base: str, quote: str) -> Ticker | None:
    result = (spot_j or {}).get("result") or {}
    if not result:
        return None
    by_key = {k.upper().replace("/", ""): (k, v) for k, v in result.items()}
    for pair in pairs:
        key = pair.upper().replace("/", "")
        hit = by_key.get(key)
        if not hit:
            for rk, rv in by_key.items():
                if key in rk or rk in key:
                    hit = (rk, rv)
                    break
        if not hit:
            continue
        pair_key, row = hit
        px = _f((row.get("c") or [None])[0])
        if px:
            pq = _kraken_pair_quote(pair_key, quote)
            return Ticker(px, url=links.spot_url("kraken", base, pq))
    row = next(iter(result.values()))
    pair_key = next(iter(result.keys()))
    px = _f((row.get("c") or [None])[0])
    if not px:
        return None
    return Ticker(px, url=links.spot_url("kraken", base, _kraken_pair_quote(pair_key, quote)))


async def _kraken(base: str, quote: str) -> ExchangeReport:
    usd_pair = "XBTUSD" if base.upper() == "BTC" else f"{base.upper()}USD"
    kick = asyncio.create_task(kraken_ticker_pairs(base, quote))
    _bg_px.add(kick)
    kick.add_done_callback(_bg_px.discard)
    fut_task = asyncio.create_task(_kraken_futures_tickers())
    spot_j = await get_json(
        "https://api.kraken.com/0/public/Ticker",
        params={"pair": usd_pair},
        timeout=0.80,
    )
    spot = _kraken_spot_from_ticker(spot_j, [usd_pair], base, quote)
    if fut_task.done() and not fut_task.cancelled():
        try:
            tickers = fut_task.result()
        except Exception:
            tickers = []
    else:
        _bg_px.add(fut_task)
        fut_task.add_done_callback(_bg_px.discard)
        tickers = list(_kraken_futures_cache.get("tickers") or [])
    if not isinstance(tickers, list):
        tickers = []

    pf = f"PF_{'XBT' if base.upper() == 'BTC' else base.upper()}USD"
    fut = None
    for row in tickers:
        if row.get("symbol") == pf and row.get("markPrice"):
            rate = row.get("fundingRate") or row.get("fundingRatePrediction")
            rel = None
            if rate and row.get("markPrice"):
                rel = float(rate) / float(row["markPrice"])
            fut = Ticker(
                float(row["markPrice"]),
                url=links.futures_url("kraken", base, quote),
                funding_pct=_funding_pct(rel),
                next_funding_ms=_norm_ts_ms(row.get("nextFundingRateTime"))
                or _estimate_next_funding_ms(1),
            )
            break
    return ExchangeReport("kraken", "Kraken", spot=spot, futures=fut)


async def _coinbase(base: str, quote: str) -> ExchangeReport:
    b = base.upper()
    quotes: list[str] = []
    for cq in ("USD", "USDT", quote.upper(), "USDC"):
        if cq not in quotes:
            quotes.append(cq)
    quotes = quotes[:2]

    async def _ticker(cq: str):
        return cq, await get_json(
            f"https://api.exchange.coinbase.com/products/{b}-{cq}/ticker",
            timeout=0.55,
        )

    jobs = [asyncio.create_task(_ticker(cq)) for cq in quotes]
    try:
        for done in asyncio.as_completed(jobs):
            try:
                cq, row = await done
            except Exception:
                continue
            px = _f((row or {}).get("price") if isinstance(row, dict) else None)
            if px:
                for job in jobs:
                    if not job.done():
                        job.cancel()
                return ExchangeReport(
                    "coinbase",
                    "Coinbase",
                    spot=Ticker(px, url=links.spot_url("coinbase", base, cq)),
                )
    except Exception:
        pass
    return ExchangeReport("coinbase", "Coinbase")


async def _aster(base: str, quote: str) -> ExchangeReport:
    sym = f"{base}{quote}"
    spot_j, fut_j = await asyncio.gather(
        get_json("https://www.asterdex.com/api/v1/ticker/price", params={"symbol": sym}),
        get_json("https://www.asterdex.com/fapi/v1/premiumIndex", params={"symbol": sym}),
    )
    spot = fut = None
    if spot_j and spot_j.get("price"):
        spot = Ticker(float(spot_j["price"]), url=links.spot_url("aster", base, quote))
    if fut_j and fut_j.get("markPrice"):
        fut = Ticker(
            float(fut_j["markPrice"]),
            url=links.futures_url("aster", base, quote),
            funding_pct=_funding_pct(_rate(fut_j.get("lastFundingRate"))),
            next_funding_ms=_norm_ts_ms(fut_j.get("nextFundingTime")),
        )
    return ExchangeReport("aster", "AsterDEX", spot=spot, futures=fut)


_FETCHERS = {
    "binance": _binance,
    "bybit": _bybit,
    "gate": _gate,
    "mexc": _mexc,
    "bitget": _bitget,
    "okx": _okx,
    "kucoin": _kucoin,
    "bingx": _bingx,
    "kraken": _kraken,
    "coinbase": _coinbase,
    "aster": _aster,
    "hyperliquid": _hyperliquid,
}


def _venue_key(exchange: str, base: str, quote: str) -> str:
    return f"{exchange}:{base.upper()}:{quote.upper()}"


def _reuse_last(meta: ExchangeMeta, base: str, quote: str, *, timed_out: bool) -> ExchangeReport:
    hit = _last_good.get(_venue_key(meta.key, base, quote))
    if hit is not None and hit.has_data:
        return replace(hit, timed_out=timed_out)
    return ExchangeReport(
        meta.key, meta.name, futures_only=meta.futures_only, timed_out=timed_out
    )


async def fetch_exchange(meta: ExchangeMeta, base: str, quote: str) -> ExchangeReport:
    fn = _FETCHERS.get(meta.key)
    if not fn:
        return ExchangeReport(meta.key, meta.name, futures_only=meta.futures_only)
    try:
        report = await fn(base, quote)
    except Exception:
        return _reuse_last(meta, base, quote, timed_out=True)
    if isinstance(report, ExchangeReport) and report.has_data:
        _last_good.set(_venue_key(meta.key, base, quote), report)
        return report
    reused = _reuse_last(meta, base, quote, timed_out=False)
    if reused.has_data:
        return reused
    return report if isinstance(report, ExchangeReport) else ExchangeReport(
        meta.key, meta.name, futures_only=meta.futures_only
    )


async def fetch_all_prices(base: str, quote: str = "USDT") -> list[ExchangeReport]:
    tasks = [
        asyncio.create_task(fetch_exchange(meta, base, quote)) for meta in EXCHANGES
    ]
    await asyncio.wait(tasks, timeout=_FETCH_ALL_WAIT_SEC)
    late = [t for t in tasks if not t.done()]
    if late:
        await asyncio.wait(late, timeout=0.10)
    out: list[ExchangeReport] = []
    for meta, task in zip(EXCHANGES, tasks):
        if task.done() and not task.cancelled():
            try:
                res = task.result()
            except Exception:
                res = _reuse_last(meta, base, quote, timed_out=True)
        else:
            _bg_px.add(task)
            task.add_done_callback(_bg_px.discard)
            res = _reuse_last(meta, base, quote, timed_out=True)
        if isinstance(res, ExchangeReport):
            out.append(res)
        else:
            out.append(ExchangeReport(meta.key, meta.name, futures_only=meta.futures_only))
    return out
