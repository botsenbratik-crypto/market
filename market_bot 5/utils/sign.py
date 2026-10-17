"""HMAC helpers for private exchange REST."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone
from urllib.parse import urlencode


def hmac_sha256_hex(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def hmac_sha256_b64(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def bybit_headers(api_key: str, api_secret: str, params: dict) -> dict[str, str]:
    ts = str(int(time.time() * 1000))
    query = urlencode(sorted(params.items()))
    sign = hmac_sha256_hex(api_secret, f"{ts}{api_key}5000{query}")
    return {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
    }


def okx_timestamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"


def okx_headers(
    api_key: str,
    api_secret: str,
    passphrase: str,
    method: str,
    path: str,
    body: str = "",
) -> dict[str, str]:
    ts = okx_timestamp()
    sign = hmac_sha256_b64(api_secret, f"{ts}{method}{path}{body}")
    return {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


def bingx_headers(api_key: str, api_secret: str, params: dict) -> dict[str, str]:
    """BingX signs sorted query string only (no timestamp prefix)."""
    query = urlencode(sorted(params.items()))
    sign = hmac_sha256_hex(api_secret, query)
    return {"X-BX-APIKEY": api_key, "X-BX-SIGNATURE": sign}


_kraken_nonce = 0


def kraken_nonce() -> str:
    global _kraken_nonce
    n = int(time.time() * 1000)
    if n <= _kraken_nonce:
        n = _kraken_nonce + 1
    _kraken_nonce = n
    return str(n)


def kraken_sign(path: str, data: dict[str, str], api_secret: str) -> str:
    postdata = urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = path.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(api_secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def kraken_headers(path: str, data: dict[str, str], api_key: str, api_secret: str) -> dict[str, str]:
    return {
        "API-Key": api_key,
        "API-Sign": kraken_sign(path, data, api_secret),
        "Content-Type": "application/x-www-form-urlencoded",
    }
