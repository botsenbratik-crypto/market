"""Deposit / withdrawal status."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlencode

from market_bot.config import Settings, get_settings
from market_bot.models import NetworkStatus
from market_bot.parsers.exchanges import DW_EXCHANGES
from market_bot.utils.cache import TtlCache
from market_bot.utils.http import get_json, post_form
from market_bot.utils.sign import bybit_headers, hmac_sha256_hex, kraken_headers, kraken_nonce, okx_headers

logger = logging.getLogger(__name__)

_BINANCE_DW_URL = "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll"
_binance_cache: list[dict] | None = None
_mexc_config_cache = TtlCache[list[dict]](300.0)
_bingx_config_cache = TtlCache[list[dict]](300.0)
_kraken_assets_cache = TtlCache[dict](300.0)
_mexc_inflight: asyncio.Task[list[dict]] | None = None
_bingx_inflight: asyncio.Task[list[dict]] | None = None
_mexc_lock = asyncio.Lock()
_bingx_lock = asyncio.Lock()

_AUTH_TIMEOUTS: dict[str, float] = {}
_bybit_key_dead = True
_DEFAULT_TIMEOUT = 0.55
_last_good_dw = TtlCache[list[NetworkStatus]](180.0)
_NO_FETCH_TIMEOUT = frozenset()
_bg_dw: set[asyncio.Task] = set()

_OKX_CHAIN_SUFFIX = {
    "BITCOIN": "BTC",
    "OKTC": "OKTC",
    "LIGHTNING": "LIGHTNING",
    "APTOS": "APT",
    "SUI": "SUI",
    "X LAYER": "XLAYER",
    "XLAYER": "XLAYER",
    "SOLANA": "SOL",
    "ARBITRUM ONE": "ARBITRUM",
    "ARBITRUM": "ARBITRUM",
    "OPTIMISM": "OPTIMISM",
    "POLYGON": "POLYGON",
    "BSC": "BSC",
    "ERC20": "ETH",
    "ZKSYNC ERA": "ZKSYNCERA",
    "STARKNET": "STARKNET",
    "LINEA": "LINEA",
    "BASE": "BASE",
    "UNICHAIN": "UNICHAIN",
}


async def prefetch_binance_dw() -> None:
    await refresh_binance_dw()


async def refresh_binance_dw() -> None:
    global _binance_cache
    data = await get_json(_BINANCE_DW_URL, timeout=2.0)
    _binance_cache = (data or {}).get("data") or [] if isinstance(data, dict) else []


async def _warm_kraken_assets() -> None:
    data = await get_json("https://api.kraken.com/0/public/Assets", timeout=1.5)
    assets = (data or {}).get("result") or {}
    if assets:
        _kraken_assets_cache.set("all", assets)


async def refresh_warm_caches() -> None:
    settings = get_settings()
    tasks: list = [refresh_binance_dw(), _warm_kraken_assets()]
    if settings.exchange_creds("mexc") and _mexc_config_cache.get("all") is None:
        tasks.append(_fetch_mexc_config(settings))
    if settings.exchange_creds("bingx") and _bingx_config_cache.get("all") is None:
        tasks.append(_fetch_bingx_config(settings))
    await asyncio.gather(*tasks, return_exceptions=True)
    if settings.exchange_creds("bingx") and _bingx_config_cache.get("all") is None:
        await _fetch_bingx_config(settings)
    try:
        nets = await _bingx_networks("BTC", settings)
        if nets:
            _last_good_dw.set("bingx:BTC", nets)
    except Exception:
        pass
    asyncio.create_task(_warm_kraken_dw(settings))


async def _warm_kraken_dw(settings: Settings) -> None:
    try:
        nets = await _kraken_networks("BTC", settings, http_timeout=2.5)
        if isinstance(nets, list) and nets:
            _last_good_dw.set("kraken:BTC", nets)
    except Exception:
        pass


async def prefetch_auth_configs() -> None:
    await refresh_warm_caches()


async def _fetch_mexc_config(settings: Settings) -> list[dict]:
    creds = settings.exchange_creds("mexc")
    if not creds:
        return []
    ts = str(int(time.time() * 1000))
    query = urlencode({"recvWindow": "60000", "timestamp": ts})
    sign = hmac.new(creds["secret"].encode(), query.encode(), hashlib.sha256).hexdigest()
    data = await get_json(
        f"https://api.mexc.com/api/v3/capital/config/getall?{query}&signature={sign}",
        headers={"X-MEXC-APIKEY": creds["key"]},
        timeout=0.7,
    )
    rows = data if isinstance(data, list) else []
    if not rows:
        logger.warning("mexc_dw_empty — check MEXC_API_KEY (invalid/expired keys return no D/W)")
        return []
    _mexc_config_cache.set("all", rows)
    return rows


async def _load_mexc_config(settings: Settings) -> list[dict]:
    global _mexc_inflight
    cached = _mexc_config_cache.get("all")
    if cached is not None:
        return cached
    async with _mexc_lock:
        cached = _mexc_config_cache.get("all")
        if cached is not None:
            return cached
        if _mexc_inflight is None or _mexc_inflight.done():
            _mexc_inflight = asyncio.create_task(_fetch_mexc_config(settings))
        task = _mexc_inflight
    try:
        return await task
    except Exception:
        async with _mexc_lock:
            if _mexc_inflight is task:
                _mexc_inflight = None
        return []


async def _fetch_bingx_config(settings: Settings) -> list[dict]:
    creds = settings.exchange_creds("bingx")
    if not creds:
        return []
    ts = str(int(time.time() * 1000))
    params = {"timestamp": ts}
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = hmac_sha256_hex(creds["secret"], query)
    data = await get_json(
        "https://open-api.bingx.com/openApi/wallets/v1/capital/config/getall",
        params={**params, "signature": sig},
        headers={"X-BX-APIKEY": creds["key"]},
        timeout=2.5,
    )
    rows = (data or {}).get("data") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        logger.warning("bingx_dw_empty")
        return []
    _bingx_config_cache.set("all", rows)
    return rows


async def _load_bingx_config(settings: Settings) -> list[dict]:
    global _bingx_inflight
    cached = _bingx_config_cache.get("all")
    if cached is not None:
        return cached
    async with _bingx_lock:
        cached = _bingx_config_cache.get("all")
        if cached is not None:
            return cached
        if _bingx_inflight is None or _bingx_inflight.done():
            _bingx_inflight = asyncio.create_task(_fetch_bingx_config(settings))
        task = _bingx_inflight
    try:
        return await task
    except Exception:
        async with _bingx_lock:
            if _bingx_inflight is task:
                _bingx_inflight = None
        return []


async def _ensure_auth_configs(settings: Settings) -> None:
    tasks = []
    if settings.exchange_creds("mexc"):
        tasks.append(_load_mexc_config(settings))
    if settings.exchange_creds("bingx"):
        tasks.append(_load_bingx_config(settings))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _bool_or_none(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes", "enabled")
    return bool(v)


def _norm_chain(name: str) -> str:
    n = (name or "").strip().upper()
    if "(" in n:
        n = n.split("(")[0].strip()
    n = " ".join(n.split())
    if n.endswith(" NETWORK"):
        n = n[: -len(" NETWORK")].strip()

    aliases = {
        "BEP20": "BSC",
        "BNB SMART CHAIN": "BSC",
        "BNB CHAIN": "BSC",
        "BITCOIN": "BTC",
        "BTC": "BTC",
        "ERC20": "ETH",
        "ETHEREUM": "ETH",
        "ETH": "ETH",
        "TRC20": "TRON",
        "TRON": "TRON",
        "ARBITRUM ONE": "ARBITRUM",
        "ARBITRUM ONE NETWORK": "ARBITRUM",
        "ARBITRUMONE": "ARBITRUM",
        "ARBITRUM": "ARBITRUM",
        "ARBI": "ARBITRUM",
        "ARBEVM": "ARBITRUM",
        "ARB NOVA": "ARBNOVA",
        "ARBITRUM NOVA": "ARBNOVA",
        "ARBINOVA": "ARBNOVA",
        "OP": "OPTIMISM",
        "OPETH": "OPTIMISM",
        "OPTIMISM": "OPTIMISM",
        "MATIC": "POLYGON",
        "POLYGON": "POLYGON",
        "ZKSYNC": "ZKSYNCERA",
        "ZKSYNC ERA": "ZKSYNCERA",
        "ZKSERA": "ZKSYNCERA",
        "SOLANA": "SOL",
        "SOL": "SOL",
        "AVAX C-CHAIN": "AVAX",
        "AVAXC": "AVAX",
        "AVALANCHE C-CHAIN": "AVAX",
        "LIGHTNING NETWORK": "LIGHTNING",
        "LIGHTNING": "LIGHTNING",
        "SEGWIT": "SEGWITBTC",
        "SEGWITBTC": "SEGWITBTC",
        "BTC-SEGWIT": "BTC-SEGWIT",
        "LINEAETH": "LINEA",
        "LINEA": "LINEA",
        "BASEEVM": "BASE",
        "BASE": "BASE",
        "MANTAETH": "MANTLE",
        "MANTA": "MANTLE",
        "MTL": "MANTLE",
        "MANTLE": "MANTLE",
        "STARKNET": "STARKNET",
        "SCROLL": "SCROLL",
        "APTOS": "APT",
        "APT": "APT",
        "SUI": "SUI",
        "TON": "TON",
        "KCC": "KCC",
        "OKTC": "OKTC",
        "BSC": "BSC",
    }
    if n in aliases:
        return aliases[n]
    compact = n.replace(" ", "").replace("-", "")
    return aliases.get(compact, n)


def _okx_chain(raw: str) -> str:
    c = (raw or "").strip()
    if "-" not in c:
        return _norm_chain(c)
    _, suffix = c.split("-", 1)
    parts = suffix.split()
    if parts and parts[-1].upper() in {"ETH", "BTC", "USDT", "USDC", "SOL", "BNB", "K"}:
        parts = parts[:-1]
    label = " ".join(parts)
    if "(" in label:
        label = label.split("(")[0].strip()
    key = label.upper()
    return _norm_chain(_OKX_CHAIN_SUFFIX.get(key, key))


async def _binance_networks(symbol: str) -> list[NetworkStatus]:
    global _binance_cache
    if _binance_cache is None:
        await prefetch_binance_dw()
    for coin in _binance_cache or []:
        if str(coin.get("coin", "")).upper() != symbol.upper():
            continue
        out: list[NetworkStatus] = []
        for net in coin.get("networkList") or []:
            if not isinstance(net, dict):
                continue
            chain = _norm_chain(str(net.get("network") or net.get("name") or ""))
            if not chain:
                continue
            busy = net.get("busy")
            dep = False if busy else _bool_or_none(net.get("depositEnable"))
            wdr = False if busy else _bool_or_none(net.get("withdrawEnable"))
            out.append(NetworkStatus(chain, dep, wdr))
        return out
    return []


def _bybit_rows_to_networks(rows: list[dict], symbol: str) -> list[NetworkStatus]:
    out: list[NetworkStatus] = []
    want = symbol.upper()
    for row in rows:
        coin = str(row.get("coin") or row.get("coinName") or "").upper()
        if coin and coin != want:
            continue
        for ch in row.get("chains") or row.get("chainList") or []:
            if not isinstance(ch, dict):
                continue
            chain = _norm_chain(
                str(ch.get("chain") or ch.get("chainType") or ch.get("chainName") or "")
            )
            if not chain:
                continue
            dep = _bool_or_none(
                ch.get("chainDeposit") or ch.get("depositEnable") or ch.get("canDeposit")
            )
            wdr = _bool_or_none(
                ch.get("chainWithdraw") or ch.get("withdrawEnable") or ch.get("canWithdraw")
            )
            out.append(NetworkStatus(chain, dep, wdr))
    return out


async def _bybit_networks(symbol: str, settings: Settings) -> list[NetworkStatus]:
    return await _bybit_public_networks(symbol)


async def _bybit_public_networks(symbol: str) -> list[NetworkStatus]:
    """Public allowed-deposit list — works without a wallet API key."""
    want = symbol.upper()
    out: list[NetworkStatus] = []
    cursor: str | None = None
    for _ in range(2):
        params: dict[str, str] = {"coin": want, "limit": "50"}
        if cursor:
            params["cursor"] = cursor
        data = await get_json(
            "https://api.bybit.com/v5/asset/deposit/query-allowed-list",
            params=params,
            timeout=0.7,
        )
        result = (data or {}).get("result") or {}
        rows = result.get("configList") or []
        if not isinstance(rows, list) or not rows:
            break
        for ch in rows:
            if not isinstance(ch, dict):
                continue
            if str(ch.get("coin") or "").upper() not in ("", want):
                continue
            chain = _norm_chain(str(ch.get("chainType") or ch.get("chain") or ""))
            if not chain:
                continue
            # This endpoint only returns chains that currently accept deposits.
            # Withdraw is not published publicly; treat listed chains as open.
            out.append(NetworkStatus(chain, True, True))
        cursor = str(result.get("nextPageCursor") or "") or None
        if not cursor:
            break
    # drop duplicate chains from extra pages
    uniq: list[NetworkStatus] = []
    have: set[str] = set()
    for n in out:
        if n.name in have:
            continue
        have.add(n.name)
        uniq.append(n)
    return uniq


async def _gate_networks(symbol: str) -> list[NetworkStatus]:
    data = await get_json(
        "https://api.gateio.ws/api/v4/wallet/currency_chains",
        params={"currency": symbol.upper()},
        timeout=0.7,
    )
    if not isinstance(data, list):
        return []
    return [
        NetworkStatus(
            _norm_chain(str(row.get("chain") or "")),
            _bool_or_none(not row.get("is_deposit_disabled")),
            _bool_or_none(not row.get("is_withdraw_disabled")),
            address=(str(row.get("contract_address") or "").strip() or None),
        )
        for row in data
        if row.get("chain")
    ]


async def _okx_networks(symbol: str, settings: Settings) -> list[NetworkStatus]:
    creds = settings.exchange_creds("okx")
    if not creds:
        return []
    path = "/api/v5/asset/currencies?ccy=" + symbol.upper()
    headers = okx_headers(creds["key"], creds["secret"], creds["passphrase"], "GET", path)
    data = await get_json(
        "https://www.okx.com/api/v5/asset/currencies",
        params={"ccy": symbol.upper()},
        headers=headers,
        timeout=0.7,
    )
    out: list[NetworkStatus] = []
    for row in (data or {}).get("data") or []:
        chain = _okx_chain(str(row.get("chain") or ""))
        if not chain:
            continue
        out.append(
            NetworkStatus(
                chain,
                _bool_or_none(row.get("canDep")),
                _bool_or_none(row.get("canWd")),
            )
        )
    return out


async def _kucoin_networks(symbol: str) -> list[NetworkStatus]:
    data = await get_json(f"https://api.kucoin.com/api/v3/currencies/{symbol.upper()}")
    out: list[NetworkStatus] = []
    for row in (data or {}).get("data", {}).get("chains") or []:
        chain = _norm_chain(str(row.get("chainName") or row.get("chain") or ""))
        out.append(
            NetworkStatus(
                chain,
                _bool_or_none(row.get("isDepositEnabled")),
                _bool_or_none(row.get("isWithdrawEnabled")),
            )
        )
    return out


async def _bitget_networks(symbol: str) -> list[NetworkStatus]:
    data = await get_json(
        "https://api.bitget.com/api/v2/spot/public/coins",
        params={"coin": symbol.upper()},
    )
    out: list[NetworkStatus] = []
    for row in (data or {}).get("data") or []:
        for ch in row.get("chains") or []:
            chain = _norm_chain(str(ch.get("chain") or ""))
            out.append(
                NetworkStatus(
                    chain,
                    _bool_or_none(ch.get("rechargeable")),
                    _bool_or_none(ch.get("withdrawable")),
                )
            )
    return out


async def _bingx_networks(symbol: str, settings: Settings) -> list[NetworkStatus]:
    rows = await _load_bingx_config(settings)
    want = symbol.upper()
    out: list[NetworkStatus] = []
    for row in rows:
        coin = str(row.get("coin") or row.get("coinName") or "").upper()
        if coin != want:
            continue
        nets = row.get("networkList") or row.get("networks") or []
        if not nets and (
            row.get("depositEnable") is not None or row.get("withdrawEnable") is not None
        ):
            nets = [row]
        for ch in nets:
            if not isinstance(ch, dict):
                continue
            chain = _norm_chain(str(ch.get("network") or ch.get("name") or ch.get("chain") or ""))
            if not chain:
                continue
            out.append(
                NetworkStatus(
                    chain,
                    _bool_or_none(
                        ch.get("depositEnable")
                        if ch.get("depositEnable") is not None
                        else ch.get("isDepositEnabled")
                    ),
                    _bool_or_none(
                        ch.get("withdrawEnable")
                        if ch.get("withdrawEnable") is not None
                        else ch.get("isWithdrawEnabled")
                    ),
                    address=(
                        str(ch.get("contractAddress") or ch.get("contract") or "").strip()
                        or None
                    ),
                )
            )
    return out


async def _mexc_networks(symbol: str, settings: Settings) -> list[NetworkStatus]:
    rows = await _load_mexc_config(settings)
    out: list[NetworkStatus] = []
    if isinstance(rows, list):
        for row in rows:
            if str(row.get("coin", "")).upper() != symbol.upper():
                continue
            for net in row.get("networkList") or []:
                chain = _norm_chain(str(net.get("netWork") or net.get("network") or ""))
                out.append(
                    NetworkStatus(
                        chain,
                        _bool_or_none(net.get("depositEnable")),
                        _bool_or_none(net.get("withdrawEnable")),
                        address=(
                            str(net.get("contract") or net.get("contractAddress") or "").strip()
                            or None
                        ),
                    )
                )
    return out


_KRAKEN_METHOD_ALIASES = {
    "BITCOIN": "BTC",
    "BITCOIN LIGHTNING": "LIGHTNING",
    "BTC": "BTC",
    "LITECOIN": "LTC",
    "RIPPLE": "XRP",
    "DOGECOIN": "DOGE",
    "SOLANA": "SOL",
    "TRON": "TRON",
    "BNB CHAIN": "BSC",
    "BNB SMART CHAIN": "BSC",
    "ETHEREUM": "ETH",
    "ARBITRUM": "ARBITRUM",
    "ARBITRUM ONE": "ARBITRUM",
    "ARBITRUM ONE NETWORK": "ARBITRUM",
    "OPTIMISM": "OPTIMISM",
    "POLYGON": "POLYGON",
    "BASE": "BASE",
    "LINEA": "LINEA",
    "AVALANCHE": "AVAX",
}


def _kraken_method_chain(method: str) -> str | None:
    raw = (method or "").strip()
    if not raw:
        return None
    if " - " in raw:
        return _norm_chain(raw.split(" - ", 1)[1])
    upper = raw.upper()
    if upper in _KRAKEN_METHOD_ALIASES:
        return _KRAKEN_METHOD_ALIASES[upper]
    if "(" in raw:
        return _norm_chain(raw.split("(", 1)[0])
    return _norm_chain(raw)


def _kraken_asset_code(symbol: str, assets: dict) -> str | None:
    sym = symbol.upper()
    for key, row in assets.items():
        alt = str(row.get("altname", "")).upper()
        if alt == sym or (sym == "BTC" and alt == "XBT"):
            return key
    return sym


async def _kraken_private(
    settings: Settings, path: str, params: dict[str, str], timeout: float = 0.5
) -> Any:
    creds = settings.exchange_creds("kraken")
    if not creds:
        return None
    data = {"nonce": kraken_nonce(), **params}
    headers = kraken_headers(path, data, creds["key"], creds["secret"])
    return await post_form(
        f"https://api.kraken.com{path}", data, headers=headers, timeout=timeout
    )


async def _kraken_networks(
    symbol: str, settings: Settings | None = None, http_timeout: float = 0.5
) -> list[NetworkStatus]:
    settings = settings or get_settings()
    assets = _kraken_assets_cache.get("all")
    if assets is None:
        data = await get_json(
            "https://api.kraken.com/0/public/Assets", timeout=min(1.5, http_timeout)
        )
        assets = (data or {}).get("result") or {}
        if assets:
            _kraken_assets_cache.set("all", assets)
    if not assets:
        return []

    asset = _kraken_asset_code(symbol, assets)
    if not asset:
        return []

    dep_j, wdr_j = await asyncio.gather(
        _kraken_private(
            settings, "/0/private/DepositMethods", {"asset": asset}, timeout=http_timeout
        ),
        _kraken_private(
            settings, "/0/private/WithdrawMethods", {"asset": asset}, timeout=http_timeout
        ),
    )
    if not dep_j and not wdr_j:
        return []

    chains: dict[str, dict[str, bool | None]] = {}
    for row in (dep_j or {}).get("result") or []:
        chain = _kraken_method_chain(str(row.get("method") or ""))
        if not chain:
            continue
        chains.setdefault(chain, {"deposit": None, "withdraw": None})
        chains[chain]["deposit"] = True
    for row in (wdr_j or {}).get("result") or []:
        chain = _kraken_method_chain(str(row.get("method") or ""))
        if not chain:
            continue
        chains.setdefault(chain, {"deposit": None, "withdraw": None})
        chains[chain]["withdraw"] = True

    if not chains:
        return []

    return [
        NetworkStatus(name, flags.get("deposit"), flags.get("withdraw"))
        for name, flags in sorted(chains.items())
    ]


def _coinbase_status(raw: object) -> bool | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in {"online", "ok", "active", "true", "1"}:
        return True
    if s in {"offline", "disabled", "delisted", "false", "0"}:
        return False
    return None


async def _coinbase_networks(symbol: str) -> list[NetworkStatus]:
    data = await get_json(
        f"https://api.exchange.coinbase.com/currencies/{symbol.upper()}",
        timeout=0.55,
    )
    if not isinstance(data, dict):
        return []
    out: list[NetworkStatus] = []
    for net in data.get("supported_networks") or []:
        if not isinstance(net, dict):
            continue
        chain = _norm_chain(str(net.get("name") or net.get("id") or ""))
        if not chain:
            continue
        st = str(net.get("status") or "").lower()
        if st in {"delisted", "disabled"}:
            continue
        deposit = _coinbase_status(net.get("deposit_status"))
        withdraw = _coinbase_status(net.get("withdraw_status"))
        if deposit is None and withdraw is None:
            online = st in {"", "online"}
            deposit = withdraw = True if online else False
        out.append(NetworkStatus(chain, deposit, withdraw))
    return out


async def fetch_networks(exchange: str, symbol: str, settings: Settings | None = None) -> list[NetworkStatus]:
    settings = settings or get_settings()
    fetchers = {
        "binance": lambda: _binance_networks(symbol),
        "bybit": lambda: _bybit_networks(symbol, settings),
        "gate": lambda: _gate_networks(symbol),
        "okx": lambda: _okx_networks(symbol, settings),
        "kucoin": lambda: _kucoin_networks(symbol),
        "bitget": lambda: _bitget_networks(symbol),
        "bingx": lambda: _bingx_networks(symbol, settings),
        "mexc": lambda: _mexc_networks(symbol, settings),
        "kraken": lambda: _kraken_networks(symbol, settings),
        "coinbase": lambda: _coinbase_networks(symbol),
    }
    fn = fetchers.get(exchange)
    if not fn:
        return []
    cache_key = f"{exchange}:{symbol.strip().upper()}"
    try:
        nets = await fn()
    except Exception:
        hit = _last_good_dw.get(cache_key)
        return list(hit) if hit else []
    if isinstance(nets, list) and nets:
        _last_good_dw.set(cache_key, nets)
        return nets
    return nets if isinstance(nets, list) else []


async def fetch_all_networks(symbol: str) -> dict[str, list[NetworkStatus]]:
    settings = get_settings()
    keys = list(DW_EXCHANGES)
    tasks = [asyncio.create_task(fetch_networks(k, symbol, settings)) for k in keys]
    await asyncio.wait(tasks, timeout=_DEFAULT_TIMEOUT)
    out: dict[str, list[NetworkStatus]] = {}
    for k, task in zip(keys, tasks):
        if task.done() and not task.cancelled():
            try:
                r = task.result()
            except Exception:
                r = []
            out[k] = r if isinstance(r, list) else []
        else:
            _bg_dw.add(task)
            task.add_done_callback(_bg_dw.discard)
            hit = _last_good_dw.get(f"{k}:{symbol.strip().upper()}")
            out[k] = list(hit) if hit else []
    return out
