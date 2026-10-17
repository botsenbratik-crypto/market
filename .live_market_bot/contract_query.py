"""Detect contract-address queries. Tickers (btc, sol, agix) never match."""

from __future__ import annotations

import re
from dataclasses import dataclass

_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_SUI_APT_RE = re.compile(r"^0x[a-fA-F0-9]{1,64}$")
_TON_RAW_RE = re.compile(r"^-?\d+:[a-fA-F0-9]{64}$")
_TON_USER_RE = re.compile(r"^(?:EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}$")
_TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
_CARDANO_RE = re.compile(r"^(addr1|addr_test1|asset1)[0-9a-z]+$", re.IGNORECASE)
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


@dataclass(frozen=True)
class ContractQuery:
    address: str
    kind: str  # evm | sol | ton | tron | sui | aptos | cardano


def parse_contract_query(raw: str) -> ContractQuery | None:
    token = (raw or "").strip().split()[0] if raw else ""
    if not token or token.startswith("/"):
        return None
    if _EVM_RE.fullmatch(token):
        return ContractQuery(address=token, kind="evm")
    if token.startswith("0x") and _SUI_APT_RE.fullmatch(token) and len(token) > 42:
        return ContractQuery(address=token, kind="sui")
    if _TON_RAW_RE.fullmatch(token) or _TON_USER_RE.fullmatch(token):
        return ContractQuery(address=token, kind="ton")
    if _TRON_RE.fullmatch(token):
        return ContractQuery(address=token, kind="tron")
    if _CARDANO_RE.fullmatch(token):
        return ContractQuery(address=token, kind="cardano")
    if _looks_solana(token):
        return ContractQuery(address=token, kind="sol")
    return None


def _looks_solana(token: str) -> bool:
    if len(token) < 32 or len(token) > 44 or token.startswith("0x"):
        return False
    if token.startswith("T") and len(token) == 34:
        return False
    if _TON_USER_RE.fullmatch(token) or _TON_RAW_RE.fullmatch(token):
        return False
    return all(c in _B58 for c in token)
