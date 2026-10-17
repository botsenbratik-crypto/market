"""Detect contract-address queries. Tickers (btc, sol) never match."""

from __future__ import annotations

import re
from dataclasses import dataclass

_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_TON_RAW_RE = re.compile(r"^-?\d+:[a-fA-F0-9]{64}$")
_TON_USER_RE = re.compile(r"^(?:EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}$")
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


@dataclass(frozen=True)
class ContractQuery:
    address: str
    kind: str  # evm | sol | ton


def parse_contract_query(raw: str) -> ContractQuery | None:
    text = (raw or "").strip()
    if not text or text.startswith("/"):
        return None
    token = text.split()[0]
    if _EVM_RE.fullmatch(token):
        return ContractQuery(address=token, kind="evm")
    if _TON_RAW_RE.fullmatch(token) or _TON_USER_RE.fullmatch(token):
        return ContractQuery(address=token, kind="ton")
    if _looks_solana(token):
        return ContractQuery(address=token, kind="sol")
    return None


def _looks_solana(token: str) -> bool:
    if len(token) < 32 or len(token) > 44:
        return False
    if token.startswith("0x"):
        return False
    if _TON_USER_RE.fullmatch(token) or _TON_RAW_RE.fullmatch(token):
        return False
    return all(c in _B58 for c in token)
