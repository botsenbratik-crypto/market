"""HTML for contract / multichain mode. Independent from ticker formatter."""

from __future__ import annotations

import asyncio
import html
from dataclasses import replace

from market_bot.models import ExchangeReport
from market_bot.parsers import prices, wallets
from market_bot.parsers.exchanges import DW_EXCHANGES, EXCHANGES
from market_bot.services.contract_lookup import ContractReport, OnchainRow
from market_bot.utils.formatter import _exchange_line, _network_block


def _fmt_price(value: float) -> str:
    if value >= 1000:
        s = f"{value:,.2f}"
    elif value >= 1:
        s = f"{value:.4f}"
    elif value >= 0.01:
        s = f"{value:.6f}"
    else:
        s = f"{value:.8f}"
    return f"${s.rstrip('0').rstrip('.')}"


async def format_contract_report(report: ContractReport) -> str:
    onchain = [r for r in report.onchain if r.address]
    if not report.name and not report.symbol and not onchain:
        return "❌ No data for this contract."

    lines: list[str] = []
    title: list[str] = []
    if report.name:
        title.append(html.escape(report.name))
    if report.symbol:
        title.append(f"<code>{html.escape(report.symbol.upper())}</code>")
    if title:
        lines.append(" · ".join(title))
        lines.append("")

    if onchain:
        lines.append(f"<b>On-chain ({len(onchain)}):</b>")
        for row in onchain:
            lines.append(_onchain_line(row))
            lines.append(f"  <code>{html.escape(row.address)}</code>")
        lines.append("")

    exchanges = await _listed_exchanges(report.symbol)
    if exchanges:
        lines.append(f"<b>Exchanges ({len(exchanges)}):</b>")
        lines.append("")
        for ex in exchanges:
            view = _without_funding(ex)
            header = _exchange_line(view, report.symbol or "", "USDT")
            if header:
                lines.append(header)
            lines.extend(_network_block(view))
            lines.append("")

    text = "\n".join(lines).strip()
    if len(text) > 4096:
        text = text[:4080] + "\n\n<i>…trimmed</i>"
    return text


def _onchain_line(row: OnchainRow) -> str:
    net = html.escape(row.network)
    parts = [f"• <b>{net}</b>"]
    if row.ticker:
        parts.append(f" · {html.escape(row.ticker)}")
    if row.price is not None:
        parts.append(f" · {html.escape(_fmt_price(row.price))}")
        if row.liquidity < 1000:
            parts.append(" <i>low liq</i>")
    return "".join(parts)


def _without_funding(ex: ExchangeReport) -> ExchangeReport:
    fut = ex.futures
    if fut is None:
        return ex
    if fut.funding_pct is None and fut.next_funding_ms is None:
        return ex
    stripped = replace(fut, funding_pct=None, next_funding_ms=None)
    return replace(ex, futures=stripped)


async def _listed_exchanges(symbol: str | None) -> list[ExchangeReport]:
    base = (symbol or "").strip().upper()
    if not base:
        return []
    raw, wallet_map = await asyncio.gather(
        prices.fetch_all_prices(base, "USDT"),
        wallets.fetch_all_networks(base),
    )
    if not isinstance(raw, list):
        return []
    if not isinstance(wallet_map, dict):
        wallet_map = {}
    by_key: dict[str, ExchangeReport] = {}
    for ex in raw:
        if not isinstance(ex, ExchangeReport) or not ex.has_data:
            continue
        view = ex
        if ex.key in DW_EXCHANGES:
            nets = wallet_map.get(ex.key) or []
            if nets and nets is not ex.networks:
                view = replace(ex, networks=list(nets))
        by_key[ex.key] = view
    return [by_key[m.key] for m in EXCHANGES if m.key in by_key]
