"""Tracked exchanges — fixed display order."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExchangeMeta:
    key: str
    name: str
    futures_only: bool = False


EXCHANGES: tuple[ExchangeMeta, ...] = (
    ExchangeMeta("binance", "Binance"),
    ExchangeMeta("bybit", "Bybit"),
    ExchangeMeta("gate", "Gate.io"),
    ExchangeMeta("mexc", "MEXC"),
    ExchangeMeta("bitget", "Bitget"),
    ExchangeMeta("okx", "OKX"),
    ExchangeMeta("kucoin", "KuCoin"),
    ExchangeMeta("bingx", "BingX"),
    ExchangeMeta("kraken", "Kraken"),
    ExchangeMeta("coinbase", "Coinbase"),
    ExchangeMeta("aster", "AsterDEX"),
    ExchangeMeta("hyperliquid", "Hyperliquid"),
)

EXCHANGE_BY_KEY = {e.key: e for e in EXCHANGES}
DW_EXCHANGES = frozenset(
    {"binance", "bybit", "gate", "mexc", "bitget", "okx", "kucoin", "bingx", "kraken", "coinbase"}
)
