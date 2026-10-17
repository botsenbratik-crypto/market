"""Telegram HTML report — matches reference layout."""

from __future__ import annotations

import html
import time

from market_bot.models import Contract, ExchangeReport
from market_bot.parsers.contracts import is_dummy_address
from market_bot.parsers.exchanges import EXCHANGES
from market_bot.utils import links

_NOOP_LINK = "tg://search?query=%20"


def _fmt_price(value: float) -> str:
    if value >= 1000:
        s = f"{value:,.2f}"
    elif value >= 1:
        s = f"{value:.4f}"
    else:
        s = f"{value:.6f}"
    return f"${s.rstrip('0').rstrip('.')}"



def _funding_block(fut) -> str:
    if fut.funding_pct is None and fut.next_funding_ms is None:
        return ""
    parts: list[str] = []
    if fut.funding_pct is not None:
        pct = fut.funding_pct
        if abs(pct) < 0.01:
            s = f"{pct:+.5f}".rstrip("0").rstrip(".")
        else:
            s = f"{pct:+.4f}".rstrip("0").rstrip(".")
        if s in ("+0", "-0", "0"):
            s = "+0"
        parts.append(f"{s}%")
    if fut.next_funding_ms:
        left = max(0, int(fut.next_funding_ms / 1000 - time.time()))
        h, rem = divmod(left, 3600)
        m, s = divmod(rem, 60)
        parts.append(f"{h:02d}:{m:02d}:{s:02d}")
    if not parts:
        return ""
    return f"<i>[{' / '.join(html.escape(p) for p in parts)}]</i>"


def _dw_icon(flag: bool | None) -> str:
    if flag is True:
        return "🟢"
    if flag is False:
        return "🔴"
    return "⚪"


def _exchange_name(name: str, spot_url: str | None) -> str:
    label = "Gate&#46;io" if name == "Gate.io" else html.escape(name)
    if spot_url:
        return f'<a href="{html.escape(spot_url)}"><b>{label}</b></a>'
    return f"<b>{label}</b>"


def _exchange_line(ex: ExchangeReport, base: str, quote: str) -> str:
    if not ex.has_data:
        return ""
    parts: list[str] = ["• "]
    if ex.spot:
        parts.append(_exchange_name(ex.name, ex.spot.url))
        alias = (getattr(ex, "listed_as", None) or "").upper()
        if alias and alias != base.upper():
            parts.append(f" <code>{html.escape(alias)}</code>")
        parts.append(f" {html.escape(_fmt_price(ex.spot.price))}")
        if ex.futures:
            fut_url = ex.futures.url or links.futures_url(ex.key, base, quote)
            fut_label = (
                f' | <a href="{html.escape(fut_url)}"><b>Futures</b></a>'
                if fut_url
                else " | <b>Futures</b>"
            )
            parts.append(fut_label)
            parts.append(f" {html.escape(_fmt_price(ex.futures.price))}")
            fund = _funding_block(ex.futures)
            if fund:
                parts.append(f" {fund}")
        return "".join(parts)

    if ex.futures:
        parts.append(_exchange_name(ex.name, None))
        alias = (getattr(ex, "listed_as", None) or "").upper()
        if alias and alias != base.upper():
            parts.append(f" <code>{html.escape(alias)}</code>")
        fut_url = ex.futures.url or links.futures_url(ex.key, base, quote)
        if fut_url:
            parts.append(f' · <a href="{html.escape(fut_url)}"><b>Futures</b></a>')
        else:
            parts.append(" · <b>Futures</b>")
        parts.append(f" {html.escape(_fmt_price(ex.futures.price))}")
        fund = _funding_block(ex.futures)
        if fund:
            parts.append(f" {fund}")
    return "".join(parts)


def _network_block(ex: ExchangeReport) -> list[str]:
    if not ex.networks:
        return []
    nets = [n for n in ex.networks if n.deposit is not None or n.withdraw is not None]
    if not nets:
        return []
    width = max(len(n.name) for n in nets)
    lines = [
        f"⛓️ {n.name.ljust(width)}: Dep: {_dw_icon(n.deposit)} | W/d: {_dw_icon(n.withdraw)}"
        for n in nets
    ]
    body = html.escape("\n".join(lines))
    return [f'<a href="{_NOOP_LINK}"><code>{body}</code></a>']


def _contract_lines(contracts: list[Contract]) -> list[str]:
    if not contracts:
        return []
    lines = ["<b>Contracts:</b>"]
    for c in contracts:
        addr = (c.address or "").strip()
        if not addr or is_dummy_address(addr):
            continue
        net = html.escape(c.network.upper())
        if c.url:
            net_label = f'<a href="{html.escape(c.url)}"><b>{net}</b></a>'
        else:
            net_label = f"<b>{net}</b>"
        if c.price is not None:
            lines.append(f"• {net_label} · {html.escape(_fmt_price(c.price))}")
        else:
            lines.append(f"• {net_label}")
        lines.append(f"  <code>{html.escape(addr)}</code>")
    lines.append("")
    return lines


def format_report(symbol: str, exchanges: list[ExchangeReport], contracts: list[Contract]) -> str:
    sym = html.escape(symbol.upper())
    by_key = {e.key: e for e in exchanges if e.has_data}
    ordered = [by_key[m.key] for m in EXCHANGES if m.key in by_key]

    if not ordered:
        return f"<code>{sym}</code>\n\n❌ Not listed on tracked exchanges."

    lines = [f"<code>{sym}</code>", ""]
    lines.extend(_contract_lines(contracts))

    lines.append(f"<b>Exchanges ({len(ordered)}):</b>")
    lines.append("")
    for ex in ordered:
        header = _exchange_line(ex, symbol, "USDT")
        if header:
            lines.append(header)
        lines.extend(_network_block(ex))
        lines.append("")

    return "\n".join(lines).strip()
