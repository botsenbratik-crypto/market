"""HTML for CONTRCT mode. Independent from ticker format_report."""

from __future__ import annotations

import html

from crypto_bot.services.contract_lookup import ContractReport, OnchainRow, SpotRow


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


def format_contract_report(report: ContractReport) -> str:
    lines = ["<code>CONTRCT</code>", ""]
    title_bits: list[str] = []
    if report.name:
        title_bits.append(html.escape(report.name))
    if report.symbol:
        title_bits.append(f"<code>{html.escape(report.symbol.upper())}</code>")
    if title_bits:
        lines.append(" · ".join(title_bits))
    meta: list[str] = []
    if report.query_network:
        meta.append(html.escape(report.query_network))
    if report.decimals is not None:
        meta.append(f"decimals {report.decimals}")
    if meta:
        lines.append(" · ".join(meta))
    lines.append(f"<code>{html.escape(report.query.address)}</code>")
    lines.append("")

    onchain = [r for r in report.onchain if r.address]
    spots = [s for s in report.spots if s.price > 0]
    if not report.name and not report.symbol and not onchain and not spots:
        return (
            "<code>CONTRCT</code>\n\n"
            "❌ No data for this contract."
        )

    if onchain:
        lines.append(f"<b>On-chain ({len(onchain)}):</b>")
        for row in onchain:
            lines.append(_onchain_line(row))
            lines.append(f"  <code>{html.escape(row.address)}</code>")
        lines.append("")

    if spots:
        lines.append(f"<b>Exchanges spot ({len(spots)}):</b>")
        lines.append("")
        for row in spots:
            lines.append(_spot_line(row))
        lines.append("")

    text = "\n".join(lines).strip()
    if text == "<code>CONTRCT</code>":
        return (
            "<code>CONTRCT</code>\n\n"
            "❌ No data for this contract."
        )
    if len(text) > 4096:
        text = text[:4080] + "\n\n<i>…trimmed</i>"
    return text


def _onchain_line(row: OnchainRow) -> str:
    net = html.escape(row.network)
    if row.price is not None:
        return f"• <b>{net}</b> · {html.escape(_fmt_price(row.price))}"
    return f"• <b>{net}</b>"


def _spot_line(row: SpotRow) -> str:
    label = "Gate&#46;io" if row.name == "Gate.io" else html.escape(row.name)
    price = html.escape(_fmt_price(row.price))
    if row.url:
        return f'• <a href="{html.escape(row.url)}"><b>{label}</b></a> {price}'
    return f"• <b>{label}</b> {price}"
