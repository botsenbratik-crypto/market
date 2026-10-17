"""Deep links to exchange trading pages."""

from __future__ import annotations


def _btc_alias(base: str) -> str:
    return "XBT" if base.upper() == "BTC" else base.upper()


def spot_url(exchange: str, base: str, quote: str) -> str | None:
    b, q = base.upper(), quote.upper()
    bl, ql = base.lower(), quote.lower()
    urls = {
        "binance": f"https://www.binance.com/en/trade/{b}_{q}",
        "bybit": f"https://www.bybit.com/trade/spot/{b}/{q}",
        "gate": f"https://www.gate.io/trade/{b}_{q}",
        "mexc": f"https://www.mexc.com/exchange/{b}_{q}",
        "bitget": f"https://www.bitget.com/spot/{b}{q}",
        "okx": f"https://www.okx.com/trade-spot/{bl}-{ql}",
        "kucoin": f"https://www.kucoin.com/trade/{b}-{q}",
        "bingx": f"https://bingx.com/en-us/spot/{b}-{q}",
        "kraken": f"https://pro.kraken.com/app/trade/{_btc_alias(base).lower()}-{ql}",
        "coinbase": f"https://www.coinbase.com/advanced-trade/spot/{b}-{q}",
        "aster": f"https://www.asterdex.com/en/trade/pro/spot/{b}{q}",
    }
    return urls.get(exchange)


def futures_url(exchange: str, base: str, quote: str) -> str | None:
    b, q = base.upper(), quote.upper()
    bl, ql = base.lower(), quote.lower()
    kucoin_fut = "XBTUSDTM" if b == "BTC" else f"{b}{q}M"
    urls = {
        "binance": f"https://www.binance.com/en/futures/{b}{q}",
        "bybit": f"https://www.bybit.com/trade/usdt/{b}{q}",
        "gate": f"https://www.gate.io/futures/{q}/{b}_{q}",
        "mexc": f"https://www.mexc.com/futures/{b}_{q}",
        "bitget": f"https://www.bitget.com/futures/usdt/{b}{q}",
        "okx": f"https://www.okx.com/trade-swap/{bl}-{ql}-swap",
        "kucoin": f"https://www.kucoin.com/futures/trade/{kucoin_fut}",
        "bingx": f"https://bingx.com/en-us/perpetual/{b}-{q}",
        "kraken": f"https://futures.kraken.com/trade/futures/PF_{_btc_alias(base)}USD",
        "aster": f"https://www.asterdex.com/en/trade/pro/futures/{b}{q}",
        "hyperliquid": f"https://app.hyperliquid.xyz/trade/{b}",
    }
    return urls.get(exchange)
