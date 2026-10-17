"""Warm HTTP pool + periodic keep-alive to avoid cold starts after AFK."""

from __future__ import annotations

import asyncio
import logging

from market_bot.config import get_settings
from market_bot.parsers.bingx_symbols import prefetch_bingx_symbols
from market_bot.parsers.hyperliquid_symbols import prefetch_hyperliquid
from market_bot.parsers.kraken_symbols import prefetch_kraken_pairs
from market_bot.parsers.prices import prefetch_kraken_futures
from market_bot.parsers.wallets import refresh_warm_caches
from market_bot.utils.http import get_json, init_session, ping_get, post_json

logger = logging.getLogger(__name__)

KEEPALIVE_INTERVAL_SEC = 300.0  # 5 min — inside 4–6 min window

# Lightweight pings — one per host, keeps TCP/TLS warm.
_PINGS: list[tuple[str, str, dict[str, str] | None]] = [
    ("binance", "https://api.binance.com/api/v3/ticker/price", {"symbol": "BTCUSDT"}),
    ("bybit", "https://api.bybit.com/v5/market/tickers", {"category": "spot", "symbol": "BTCUSDT"}),
    ("gate", "https://api.gateio.ws/api/v4/spot/tickers", {"currency_pair": "BTC_USDT"}),
    ("mexc", "https://api.mexc.com/api/v3/ticker/price", {"symbol": "BTCUSDT"}),
    ("bitget", "https://api.bitget.com/api/v2/spot/market/tickers", {"symbol": "BTCUSDT"}),
    ("okx", "https://www.okx.com/api/v5/market/ticker", {"instId": "BTC-USDT"}),
    ("kucoin", "https://api.kucoin.com/api/v1/market/orderbook/level1", {"symbol": "BTC-USDT"}),
    ("bingx", "https://open-api.bingx.com/openApi/spot/v1/ticker/24hr", {"symbol": "BTC-USDT"}),
    ("kraken", "https://api.kraken.com/0/public/Ticker", {"pair": "XBTUSDT"}),
    ("coinbase", "https://api.exchange.coinbase.com/products/BTC-USD/ticker", None),
    ("kraken-fut", "https://futures.kraken.com/derivatives/api/v3/tickers", None),
    ("aster", "https://www.asterdex.com/api/v1/ticker/price", {"symbol": "BTCUSDT"}),
    ("binance-dw", "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll", None),
    ("dexscreener", "https://api.dexscreener.com/latest/dex/tokens/0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", None),
]


async def _ping_hosts() -> None:
    tasks = [ping_get(url, params=params, timeout=1.2) for _, url, params in _PINGS]
    tasks.append(
        post_json(
            "https://api.hyperliquid.xyz/info",
            {"type": "metaAndAssetCtxs"},
            timeout=1.2,
        )
    )
    cmc_key = get_settings().cmc_api_key
    if cmc_key:
        tasks.append(
            get_json(
                "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest",
                params={"symbol": "BTC"},
                headers={"X-CMC_PRO_API_KEY": cmc_key},
                timeout=1.5,
            )
        )
    await asyncio.gather(*tasks, return_exceptions=True)


async def warm_all(*, sample_report=None) -> None:
    """Full warm-up: session, caches, TCP pool, optional sample /get pipeline."""
    await init_session()
    await asyncio.gather(
        refresh_warm_caches(),
        prefetch_bingx_symbols(),
        prefetch_kraken_pairs(),
        prefetch_kraken_futures(),
        prefetch_hyperliquid(),
        _ping_hosts(),
        return_exceptions=True,
    )
    if sample_report is not None:
        try:
            await sample_report("BTC")
        except Exception:
            logger.exception("sample report warm failed")


async def keepalive_loop(interval: float = KEEPALIVE_INTERVAL_SEC) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await warm_all()
            logger.debug("keepalive ok")
        except Exception:
            logger.exception("keepalive failed")
