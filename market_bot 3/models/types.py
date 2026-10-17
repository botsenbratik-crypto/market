"""Domain types for market reports."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Ticker:
    price: float
    url: str | None = None
    funding_pct: float | None = None
    next_funding_ms: int | None = None


@dataclass(slots=True)
class NetworkStatus:
    name: str
    deposit: bool | None = None
    withdraw: bool | None = None
    address: str | None = None


@dataclass(slots=True)
class ExchangeReport:
    key: str
    name: str
    spot: Ticker | None = None
    futures: Ticker | None = None
    networks: list[NetworkStatus] = field(default_factory=list)
    futures_only: bool = False
    listed_as: str | None = None
    timed_out: bool = False

    @property
    def has_data(self) -> bool:
        return self.spot is not None or self.futures is not None


@dataclass(slots=True)
class Contract:
    network: str
    address: str
    price: float | None = None
    url: str | None = None


@dataclass(slots=True)
class MarketReport:
    symbol: str
    contracts: list[Contract] = field(default_factory=list)
    exchanges: list[ExchangeReport] = field(default_factory=list)
