"""Lightweight unit tests — no network, no Telegram, no .env required."""

from __future__ import annotations

import unittest


def _fmt_price_standalone(value: float) -> str:
    """Mirror of services/formatter._fmt_price for testing without heavy deps."""
    if value >= 1000:
        s = f"{value:,.2f}"
    elif value >= 1:
        s = f"{value:.4f}"
    elif value >= 0.01:
        s = f"{value:.6f}"
    else:
        s = f"{value:.8f}"
    return f"${s.rstrip('0').rstrip('.')}"


class TestFmtPrice(unittest.TestCase):
    """services/formatter._fmt_price — price formatting tiers."""

    def _fmt(self, v: float) -> str:
        return _fmt_price_standalone(v)

    def test_thousands(self):
        self.assertEqual(self._fmt(67_123.456), "$67,123.46")

    def test_above_one(self):
        self.assertEqual(self._fmt(3.14159), "$3.1416")

    def test_above_001(self):
        self.assertEqual(self._fmt(0.04567), "$0.04567")

    def test_tiny(self):
        self.assertEqual(self._fmt(0.0000125), "$0.0000125")

    def test_zero_trailing(self):
        self.assertEqual(self._fmt(0.001), "$0.001")

    def test_one_exact(self):
        self.assertEqual(self._fmt(1.0), "$1")

    def test_big_round(self):
        self.assertEqual(self._fmt(1000.0), "$1,000")


class TestParseTicker(unittest.TestCase):
    """domain/ticker.parse_ticker — basic parsing."""

    def _parse(self, raw: str, dq: str = "USDT"):
        from crypto_bot.domain.ticker import parse_ticker
        return parse_ticker(raw, dq)

    def test_plain(self):
        self.assertEqual(self._parse("sol"), ("SOL", "USDT"))

    def test_pair(self):
        self.assertEqual(self._parse("btcusdt"), ("BTC", "USDT"))

    def test_slash(self):
        self.assertEqual(self._parse("ETH/BTC"), ("ETH", "BTC"))

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            self._parse("")


class TestTickerAliases(unittest.TestCase):
    """domain/ticker — 1000PEPE / kPEPE alias stripping."""

    def _strip(self, raw: str) -> str | None:
        from crypto_bot.domain.ticker import strip_multiplier_prefix
        return strip_multiplier_prefix(raw)

    def test_1000pepe(self):
        self.assertEqual(self._strip("1000PEPE"), "PEPE")

    def test_kpepe(self):
        self.assertEqual(self._strip("kPEPE"), "PEPE")

    def test_plain_btc(self):
        self.assertIsNone(self._strip("BTC"))

    def test_memerobinhood_parse(self):
        from crypto_bot.domain.ticker import parse_ticker
        self.assertEqual(parse_ticker("MEMEROBINHOOD"), ("MEMEROBINHOOD", "USDT"))


class TestContractAliases(unittest.TestCase):
    """Same token / different CEX tickers — match by contract, not name."""

    def test_norm_addr(self):
        from crypto_bot.domain.alias_match import norm_addr
        self.assertEqual(
            norm_addr("0x385F4f8ae47651ce5F58F5265395a669f8281e18"),
            "0x385f4f8ae47651ce5f58f5265395a669f8281e18",
        )
        self.assertIsNone(norm_addr("native"))

    def test_dex_score_exact_symbol(self):
        from crypto_bot.domain.alias_match import score_dex_pair
        pair = {
            "baseToken": {"symbol": "MEMEROBINHOOD", "name": "Meme Robinhood", "address": "0x" + "a"*40},
            "liquidity": {"usd": 8000},
        }
        self.assertGreater(score_dex_pair("MEMEROBINHOOD", pair), 100)

    def test_pick_same_contract_symbol(self):
        from crypto_bot.domain.alias_match import dex_symbols_for_addresses, pick_dex_addresses
        addr = "0x" + "b" * 40
        pairs = [
            {
                "baseToken": {"symbol": "ROBINHOOD", "name": "MEMEROBINHOOD", "address": addr},
                "liquidity": {"usd": 20000},
            }
        ]
        found = pick_dex_addresses("MEMEROBINHOOD", pairs)
        self.assertEqual(found, [addr])
        self.assertIn("ROBINHOOD", dex_symbols_for_addresses(pairs, set(found)))


class TestGuards(unittest.TestCase):
    """core/guards — code_ok / dw_flag helpers."""

    def test_code_ok(self):
        from crypto_bot.core.guards import code_ok
        self.assertTrue(code_ok(0))
        self.assertTrue(code_ok("0"))
        self.assertFalse(code_ok(1))

    def test_dw_flag(self):
        from crypto_bot.core.guards import dw_flag
        self.assertTrue(dw_flag("true"))
        self.assertFalse(dw_flag("false"))
        self.assertIsNone(dw_flag(None))


class TestContractQuery(unittest.TestCase):
    """domain/contract_query — addresses only; tickers stay on the old path."""

    def _q(self, raw: str):
        from crypto_bot.domain.contract_query import parse_contract_query
        return parse_contract_query(raw)

    def test_ticker_sol_untouched(self):
        from crypto_bot.domain.ticker import parse_ticker
        self.assertIsNone(self._q("sol"))
        self.assertIsNone(self._q("BTC"))
        self.assertEqual(parse_ticker("sol"), ("SOL", "USDT"))
        self.assertEqual(parse_ticker("BTC"), ("BTC", "USDT"))

    def test_evm(self):
        addr = "0x" + "a" * 40
        hit = self._q(addr)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.kind, "evm")
        self.assertEqual(hit.address, addr)

    def test_solana(self):
        addr = "So11111111111111111111111111111111111111112"
        hit = self._q(addr)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.kind, "sol")

    def test_ton_user(self):
        addr = "EQ" + "A" * 46
        hit = self._q(addr)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.kind, "ton")


if __name__ == "__main__":
    unittest.main()
