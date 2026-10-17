"""Contract-mode: canonical coin → native platforms → real on-chain prices."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from market_bot.config import get_settings
from market_bot.parsers.contract_query import ContractQuery
from market_bot.utils.http import get_json

_DEX_TOKENS = "https://api.dexscreener.com/latest/dex/tokens/{addr}"
_DEX_SEARCH = "https://api.dexscreener.com/latest/dex/search"
_CG_CONTRACT = "https://api.coingecko.com/api/v3/coins/{platform}/contract/{addr}"
_CG_COIN = "https://api.coingecko.com/api/v3/coins/{id}"
_CG_SEARCH = "https://api.coingecko.com/api/v3/search"
_CMC_INFO = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"
_LIFI_TOKEN = "https://li.quest/v1/token"
_CG_COIN_CACHE: dict[str, dict[str, Any]] = {}
_CG_ADDR_CACHE: dict[str, dict[str, Any]] = {}
_CG_ADDR_TO_ID: dict[str, str] = {}

_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

_CHAIN_LABEL = {
    "ethereum": "ETH",
    "eth": "ETH",
    "erc20": "ETH",
    "bsc": "BSC",
    "binance-smart-chain": "BSC",
    "binancecoin": "BSC",
    "bnb": "BSC",
    "solana": "SOL",
    "sol": "SOL",
    "base": "BASE",
    "base-network": "BASE",
    "arbitrum": "ARBITRUM",
    "arbitrum-one": "ARBITRUM",
    "arbitrum-nova": "ARBITRUM",
    "optimism": "OPTIMISM",
    "optimistic-ethereum": "OPTIMISM",
    "polygon": "POLYGON",
    "polygon-pos": "POLYGON",
    "matic-network": "POLYGON",
    "avalanche": "AVAX",
    "avax": "AVAX",
    "avalanche-x-chain": "AVAX-X",
    "x-chain": "AVAX-X",
    "moonbeam": "MOONBEAM",
    "kaia": "KAIA",
    "klaytn": "KAIA",
    "ton": "TON",
    "the-open-network": "TON",
    "tron": "TRON",
    "tron20": "TRON",
    "trc20": "TRON",
    "sui": "SUI",
    "sui-network": "SUI",
    "aptos": "APTOS",
    "apt": "APTOS",
    "cardano": "CARDANO",
    "ada": "CARDANO",
    "avalanche-c-chain": "AVAX",
    "bnb-smart-chain": "BSC",
    "hyperevm": "HYPERLIQUID",
    "hyperliquid": "HYPERLIQUID",
    "mantle": "MANTLE",
    "linea": "LINEA",
    "scroll": "SCROLL",
    "zksync": "ZKSYNCERA",
    "zksync-era": "ZKSYNCERA",
    "pulsechain": "PULSECHAIN",
    "pulse": "PULSECHAIN",
    "fantom": "FANTOM",
    "blast": "BLAST",
    "near-protocol": "NEAR",
    "near": "NEAR",
    "robinhood": "ROBINHOOD",
    "robinhood-chain": "ROBINHOOD",
}

_KIND_PLATFORMS = {
    "evm": (
        "ethereum",
        "binance-smart-chain",
        "base",
        "arbitrum-one",
        "robinhood",
        "avalanche",
        "optimistic-ethereum",
        "polygon-pos",
        "pulsechain",
    ),
    "sol": ("solana",),
    "ton": ("the-open-network",),
    "tron": ("tron",),
    "sui": ("sui",),
    "aptos": ("aptos", "sui"),
    "cardano": ("cardano",),
}

_EVM_NETS = frozenset(
    {
        "ETH",
        "BSC",
        "BASE",
        "ARBITRUM",
        "OPTIMISM",
        "POLYGON",
        "AVAX",
        "LINEA",
        "SCROLL",
        "ZKSYNCERA",
        "MANTLE",
        "HYPERLIQUID",
        "PULSECHAIN",
        "FANTOM",
        "BLAST",
        "ROBINHOOD",
        "MOONBEAM",
        "KAIA",
    }
)

_KEEP_NETS = frozenset(
    {
        "ETH",
        "BSC",
        "BASE",
        "ARBITRUM",
        "POLYGON",
        "AVAX",
        "AVAX-X",
        "OPTIMISM",
        "SOL",
        "TON",
        "TRON",
        "ROBINHOOD",
        "ZKSYNCERA",
        "MOONBEAM",
        "KAIA",
    }
)

_DEX_SCAN: tuple[tuple[str, str], ...] = (
    ("ETH", "ethereum"),
    ("BSC", "bsc"),
    ("BASE", "base"),
    ("ARBITRUM", "arbitrum"),
    ("POLYGON", "polygon"),
    ("AVAX", "avalanche"),
    ("OPTIMISM", "optimism"),
    ("SOL", "solana"),
    ("TON", "ton"),
    ("TRON", "tron"),
    ("ROBINHOOD", "robinhood"),
)
_LIFI_CHAIN = {
    "ETH": "ETH",
    "BSC": "BSC",
    "BASE": "BAS",
    "ARBITRUM": "ARB",
    "POLYGON": "POL",
    "AVAX": "AVA",
    "OPTIMISM": "OPT",
}
_TWITTER_RE = re.compile(
    r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)(?:/|$)",
    re.I,
)
_DEX_V1 = "https://api.dexscreener.com/token-pairs/v1/{chain}/{addr}"
_GT_TOKEN = "https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{addr}"
_GT_NETS = {
    "ETH": "eth",
    "BSC": "bsc",
    "BASE": "base",
    "ARBITRUM": "arbitrum",
    "POLYGON": "polygon_pos",
    "AVAX": "avax",
    "OPTIMISM": "optimism",
    "SOL": "solana",
    "TON": "ton",
    "TRON": "tron",
    "ROBINHOOD": "robinhood",
}
_OKX_PRICE = "https://web3.okx.com/api/v5/dex/market/price"
_OKX_CHAIN = {
    "ETH": "1",
    "BSC": "56",
    "BASE": "8453",
    "ARBITRUM": "42161",
    "POLYGON": "137",
    "AVAX": "43114",
    "OPTIMISM": "10",
    "SOL": "501",
}

_NET_ORDER = (
    "ETH",
    "BSC",
    "SOL",
    "BASE",
    "ARBITRUM",
    "OPTIMISM",
    "POLYGON",
    "AVAX",
    "AVAX-X",
    "TON",
    "TRON",
    "ROBINHOOD",
    "ZKSYNCERA",
    "MOONBEAM",
    "KAIA",
)

_CG_MARKET_TO_EXCHANGE = {
    "binance": ("binance", "Binance"),
    "bybit": ("bybit", "Bybit"),
    "gate.io": ("gate", "Gate.io"),
    "gate": ("gate", "Gate.io"),
    "mexc": ("mexc", "MEXC"),
    "bitget": ("bitget", "Bitget"),
    "okx": ("okx", "OKX"),
    "kucoin": ("kucoin", "KuCoin"),
    "bingx": ("bingx", "BingX"),
    "kraken": ("kraken", "Kraken"),
    "coinbase": ("coinbase", "Coinbase"),
    "coinbase exchange": ("coinbase", "Coinbase"),
}
_FUT_HINTS = ("futures", "perpetual", "swap", "linear", "/um/")
_EXCHANGE_ORDER = (
    "binance",
    "bybit",
    "gate",
    "mexc",
    "bitget",
    "okx",
    "kucoin",
    "bingx",
    "kraken",
    "coinbase",
)


@dataclass
class OnchainRow:
    network: str
    address: str
    price: float | None = None
    liquidity: float = 0.0
    volume: float = 0.0
    ticker: str | None = None
    token_name: str | None = None


@dataclass
class TokenIdentity:
    coin_id: str | None = None
    name: str | None = None
    symbol: str | None = None
    cg: dict[str, Any] | None = None
    cmc: dict[str, Any] | None = None


@dataclass
class SpotRow:
    key: str
    name: str
    price: float


@dataclass
class ContractReport:
    query: ContractQuery
    name: str | None = None
    symbol: str | None = None
    onchain: list[OnchainRow] = field(default_factory=list)
    spots: list[SpotRow] = field(default_factory=list)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def _chain_label(raw: str) -> str | None:
    key = (raw or "").strip().lower().replace("_", "-").replace(" ", "-").strip("-")
    if not key:
        return None
    if key in _CHAIN_LABEL:
        return _CHAIN_LABEL[key]
    compact = key.replace("-", "")
    for src, dst in _CHAIN_LABEL.items():
        if src.replace("-", "") == compact:
            return dst
    if "tron" in key:
        return "TRON"
    if key.startswith("sui"):
        return "SUI"
    if key.startswith("aptos") or key == "apt":
        return "APTOS"
    if "cardano" in key or key == "ada":
        return "CARDANO"
    if key.startswith("base"):
        return "BASE"
    if "x-chain" in key or key.endswith("xchain"):
        return "AVAX-X"
    if "moonbeam" in key:
        return "MOONBEAM"
    if "kaia" in key or "klaytn" in key:
        return "KAIA"
    if "zksync" in key:
        return "ZKSYNCERA"
    if key.startswith("avalanche") or "c-chain" in key:
        return "AVAX"
    if "smart-chain" in key or compact in {"bsc", "bep20", "bnbchain"}:
        return "BSC"
    if key.startswith("sol"):
        return "SOL"
    if "ton" in key and "stone" not in key:
        return "TON"
    return None


def _addr_key(addr: str) -> str:
    a = (addr or "").strip()
    if a.startswith("0x") or a.startswith("0X"):
        return a.lower()
    return a


def _native_ok(network: str, addr: str) -> bool:
    a = (addr or "").strip()
    if not a or a.lower() in {"native", "-", "null", "none"}:
        return False
    n = (network or "").upper()
    if n == "SOL":
        return not a.startswith("0x") and 32 <= len(a) <= 44
    if n == "TON":
        return a.startswith(("EQ", "UQ", "kQ", "0Q")) or bool(re.match(r"^-?\d+:", a))
    if n == "TRON":
        return a.startswith("T") and len(a) == 34
    if n in {"SUI", "APTOS"}:
        return a.startswith("0x") and len(a) > 42
    if n == "CARDANO":
        low = a.lower()
        return low.startswith("addr") or low.startswith("asset") or "." in a
    if n == "AVAX-X":
        return not a.startswith("0x") and len(a) >= 20
    if n in _EVM_NETS or n == "ROBINHOOD":
        return bool(_EVM_RE.fullmatch(a))
    return len(a) >= 10 and not (n in _EVM_NETS)


def _exchange_from_cg(market_name: str) -> tuple[str, str] | None:
    n = (market_name or "").strip().lower()
    if n in _CG_MARKET_TO_EXCHANGE:
        return _CG_MARKET_TO_EXCHANGE[n]
    for prefix, pair in _CG_MARKET_TO_EXCHANGE.items():
        if n.startswith(prefix):
            return pair
    return None


async def build_contract_report(query: ContractQuery) -> ContractReport:
    report = ContractReport(query=query)
    identity, dex = await asyncio.gather(
        resolve_identity(query),
        get_json(_DEX_TOKENS.format(addr=query.address), timeout=2.4),
        return_exceptions=True,
    )
    if isinstance(identity, BaseException):
        identity = TokenIdentity()
    if isinstance(dex, BaseException):
        dex = None
    report.name = identity.name
    report.symbol = identity.symbol
    if identity.cg:
        _fill_cex_from_cg(report, identity.cg)
    _identity_from_query_token(report, dex if isinstance(dex, dict) else None)

    deployments, tickers, scanned = await collect_deployments(
        query, identity, dex if isinstance(dex, dict) else None
    )
    report.onchain = await quote_deployments(
        deployments, scanned, tickers, query.address
    )
    _apply_tickers(report.onchain, tickers)
    _header_from_onchain(report)
    return report


async def resolve_identity(query: ContractQuery) -> TokenIdentity:
    """coin_id + name. Does not select or filter networks."""
    ident = TokenIdentity()
    cg, cmc = await asyncio.gather(
        _coingecko_canonical(query),
        _cmc_by_address(query.address),
        return_exceptions=True,
    )
    if isinstance(cg, dict):
        ident.cg = cg
        ident.coin_id = str(cg.get("id") or "").strip() or None
        ident.name = str(cg.get("name") or "").strip() or None
        ident.symbol = _clean_ticker(str(cg.get("symbol") or ""))
    if isinstance(cmc, dict):
        ident.cmc = cmc
        if not ident.name:
            ident.name = str(cmc.get("name") or "").strip() or None
        if not ident.symbol:
            ident.symbol = _clean_ticker(str(cmc.get("symbol") or ""))
        if not ident.coin_id:
            slug = str(cmc.get("slug") or "").strip()
            ident.coin_id = slug or None
    return ident


def _dep_key(network: str, addr: str) -> tuple[str, str]:
    return ((network or "").upper(), _addr_key(addr))


_TRUSTED_SRC = frozenset({"cg", "cmc", "query", "dex", "gt", "okx"})
_MIN_VOL_USD = 500.0


def _is_native_placeholder(addr: str) -> bool:
    a = (addr or "").strip().lower()
    if not a or a in {"native", "-", "null", "none"}:
        return True
    if a.startswith(("0xeee", "0xdead")):
        return True
    if a.startswith("0x") and set(a[2:]) <= {"0"}:
        return True
    return False


def _merge_deployments(
    out: dict[tuple[str, str], tuple[str, str]],
    net: str | None,
    addr: str,
    sources: dict[tuple[str, str], set[str]] | None = None,
    source: str = "",
) -> None:
    """Union by (chain, address). One source never overwrites another."""
    if not net or not addr or _is_native_placeholder(addr):
        return
    n = net.upper()
    if n not in _KEEP_NETS or not _native_ok(n, addr):
        return
    key = _dep_key(n, addr)
    if key not in out:
        out[key] = (n, addr)
    if sources is not None and source:
        sources.setdefault(key, set()).add(source)


async def collect_deployments(
    query: ContractQuery,
    identity: TokenIdentity,
    dex: dict[str, Any] | None,
) -> tuple[dict[tuple[str, str], tuple[str, str]], dict[str, str], list[OnchainRow]]:
    deps: dict[tuple[str, str], tuple[str, str]] = {}
    sources: dict[tuple[str, str], set[str]] = {}
    tickers: dict[str, str] = {}
    if identity.cg:
        _platforms_from_cg(deps, identity.cg, sources, "cg")
    if identity.cmc:
        _platforms_from_cmc(deps, identity.cmc, sources, "cmc")
    _platforms_from_dex(deps, query, dex, sources, "dex")
    seed_pairs = _pairs_matching_addr(dex, query.address)
    qnet = None
    for pair in seed_pairs:
        net, addr, tok = _pair_query_hit(pair, _addr_key(query.address))
        if net and addr:
            qnet = net
            t = _clean_ticker(str(tok.get("symbol") or ""))
            if t:
                tickers.setdefault(_addr_key(addr), t)
            break
    if qnet is None and query.kind == "sol":
        qnet = "SOL"
    _merge_deployments(deps, qnet, query.address, sources, "query")

    if not identity.cmc:
        for sym in _seed_token_labels(seed_pairs, query)[1]:
            hit = await _cmc_by_symbol_addr(sym, query.address)
            if isinstance(hit, dict):
                identity.cmc = hit
                _platforms_from_cmc(deps, hit, sources, "cmc")
                break
        if not identity.cmc:
            labels = {
                (s or "").strip().upper()
                for s in _seed_token_labels(seed_pairs, query)[1]
            }
            slug = "avalanche" if labels & {"AVAX", "WAVAX"} else None
            if slug:
                hit = await _cmc_by_slug(slug)
                if isinstance(hit, dict):
                    identity.cmc = hit
                    _platforms_from_cmc(deps, hit, sources, "cmc")

    gt_id = await _gt_coingecko_id(qnet, query.address)
    if gt_id and not identity.cg:
        cg = await _cg_coin_by_id(gt_id)
        if isinstance(cg, dict):
            identity.cg = cg
            identity.coin_id = gt_id
            _platforms_from_cg(deps, cg, sources, "cg")

    await _link_bridge_platforms(
        deps, query, seed_pairs, tickers, sources, identity.coin_id
    )
    await _union_wrapped_avax_platforms(deps, query, seed_pairs, sources)

    deps = _keep_reliable(deps, sources)
    addrs = {addr for _net, addr in deps.values()}
    if query.address:
        addrs.add(query.address)
    scanned = await _scan_dex_known(addrs)
    labels = {_clean_ticker(s) for s in _seed_token_labels(seed_pairs, query)[1]}
    labels.discard(None)
    avax_mode = bool(labels & {"AVAX", "WAVAX"})
    for row in scanned:
        if _foreign_onchain_ticker(row.ticker, labels):
            continue
        if avax_mode and _avax_junk_token(row.token_name, row.ticker):
            continue
        _merge_deployments(deps, row.network, row.address, sources, "dex")
        if row.ticker:
            tickers.setdefault(_addr_key(row.address), row.ticker)
    deps = _keep_reliable(deps, sources, scanned, query.address)
    scanned = [
        r
        for r in scanned
        if _dep_key(r.network, r.address) in deps
        and not _foreign_onchain_ticker(r.ticker, labels)
        and not (avax_mode and _avax_junk_token(r.token_name, r.ticker))
    ]
    return deps, tickers, scanned


def _keep_reliable(
    deps: dict[tuple[str, str], tuple[str, str]],
    sources: dict[tuple[str, str], set[str]],
    scanned: list[OnchainRow] | None = None,
    query_addr: str = "",
) -> dict[tuple[str, str], tuple[str, str]]:
    if scanned is None:
        return {
            key: pair
            for key, pair in deps.items()
            if (sources.get(key) or set()) & _TRUSTED_SRC
        }
    vol: dict[tuple[str, str], float] = {}
    for row in scanned:
        key = _dep_key(row.network, row.address)
        vol[key] = vol.get(key, 0.0) + float(row.volume or 0)
    q = _addr_key(query_addr)
    kept: dict[tuple[str, str], tuple[str, str]] = {}
    for key, pair in deps.items():
        src = sources.get(key) or set()
        if q and _addr_key(pair[1]) == q:
            kept[key] = pair
            continue
        if src & {"cg", "cmc", "query"}:
            kept[key] = pair
            continue
        if src & {"dex", "gt", "okx"} and vol.get(key, 0.0) >= _MIN_VOL_USD:
            kept[key] = pair
    return kept


async def quote_deployments(
    deployments: dict[tuple[str, str], tuple[str, str]],
    scanned: list[OnchainRow],
    tickers: dict[str, str],
    query_addr: str = "",
) -> list[OnchainRow]:
    rows = list(scanned)
    for net, addr in deployments.values():
        rows.append(
            OnchainRow(
                network=net,
                address=addr,
                ticker=tickers.get(_addr_key(addr)),
            )
        )
    rows.extend(await _gt_prices_for_missing(rows))
    rows.extend(await _okx_prices_for_missing(rows))
    rows = _dedupe_net_addr(rows)
    q = _addr_key(query_addr)
    return [
        r
        for r in rows
        if (q and _addr_key(r.address) == q) or r.volume >= _MIN_VOL_USD
    ]


def _index_cg_coin(coin: dict[str, Any]) -> None:
    coin_id = str(coin.get("id") or "").strip()
    if coin_id:
        _CG_COIN_CACHE[coin_id] = coin


async def _cg_coin_by_id(coin_id: str) -> dict[str, Any] | None:
    cid = (coin_id or "").strip()
    if not cid:
        return None
    cached = _CG_COIN_CACHE.get(cid)
    if cached:
        return cached
    try:
        full = await get_json(
            _CG_COIN.format(id=cid),
            params={
                "localization": "false",
                "tickers": "true",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
            timeout=3.2,
        )
    except Exception:
        return None
    if isinstance(full, dict) and full.get("id"):
        _index_cg_coin(full)
        return full
    return None


async def _gt_coingecko_id(network: str | None, addr: str) -> str | None:
    gt_net = _GT_NETS.get((network or "").upper())
    if not gt_net or not addr:
        return None
    try:
        data = await get_json(
            _GT_TOKEN.format(net=gt_net, addr=addr) + "/info",
            timeout=2.0,
        )
    except Exception:
        return None
    attrs = ((data or {}).get("data") or {}).get("attributes") if isinstance(data, dict) else None
    if not isinstance(attrs, dict):
        return None
    cid = str(attrs.get("coingecko_coin_id") or "").strip()
    return cid or None


async def _coingecko_canonical(query: ContractQuery) -> dict[str, Any] | None:
    addr = query.address.lower() if query.kind in {"evm", "sui", "aptos"} else query.address
    known_id = _CG_ADDR_TO_ID.get(_addr_key(addr))
    if known_id and known_id in _CG_COIN_CACHE:
        return _CG_COIN_CACHE[known_id]
    hit = await _cg_contract_first(query.kind, addr)
    if not hit:
        return None
    coin_id = str(hit.get("id") or "").strip()
    if coin_id:
        _CG_ADDR_TO_ID[_addr_key(addr)] = coin_id
    if coin_id and coin_id in _CG_COIN_CACHE:
        return _CG_COIN_CACHE[coin_id]
    if not coin_id:
        return hit
    try:
        full = await get_json(
            _CG_COIN.format(id=coin_id),
            params={
                "localization": "false",
                "tickers": "true",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
            timeout=3.2,
        )
    except Exception:
        full = None
    if not isinstance(full, dict) or not full.get("id"):
        full = dict(hit)
    _index_cg_coin(full)
    return full


async def _cg_contract_first(kind: str, addr: str) -> dict[str, Any] | None:
    cache_key = f"{kind}:{_addr_key(addr)}"
    cached = _CG_ADDR_CACHE.get(cache_key)
    if cached:
        return cached
    platforms = _KIND_PLATFORMS.get(kind, ())

    async def _one(platform: str) -> dict[str, Any] | None:
        try:
            data = await get_json(
                _CG_CONTRACT.format(platform=platform, addr=addr),
                timeout=1.8,
            )
        except Exception:
            return None
        return data if isinstance(data, dict) and data.get("id") else None

    jobs = [asyncio.create_task(_one(p)) for p in platforms]
    found = None
    try:
        for done in asyncio.as_completed(jobs):
            try:
                hit = await done
            except Exception:
                continue
            if hit:
                found = hit
                for job in jobs:
                    if not job.done():
                        job.cancel()
                break
    except Exception:
        found = None
    if found:
        _CG_ADDR_CACHE[cache_key] = found
    return found


def _cmc_unwrap(data: Any) -> dict[str, Any] | None:
    payload = (data or {}).get("data") if isinstance(data, dict) else None
    if isinstance(payload, dict) and payload:
        first = next(iter(payload.values()))
        if isinstance(first, list):
            return first[0] if first and isinstance(first[0], dict) else None
        return first if isinstance(first, dict) else None
    return None


def _cmc_entries(data: Any, symbol: str) -> list[dict[str, Any]]:
    payload = (data or {}).get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict) or not payload:
        return []
    raw = payload.get(symbol.upper()) or next(iter(payload.values()), [])
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []


def _cmc_entry_has_addr(entry: dict[str, Any], addr: str) -> bool:
    want = _addr_key(addr)
    contracts = entry.get("contract_address")
    rows: list[Any]
    if isinstance(contracts, list):
        rows = contracts
    elif isinstance(contracts, dict):
        rows = list(contracts.values())
    else:
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _addr_key(str(row.get("contract_address") or "")) == want:
            return True
    return False


async def _cmc_by_address(addr: str) -> dict[str, Any] | None:
    key = get_settings().cmc_api_key
    if not key:
        return None
    try:
        data = await get_json(
            _CMC_INFO,
            params={"address": addr},
            headers={"X-CMC_PRO_API_KEY": key},
            timeout=2.4,
        )
    except Exception:
        return None
    return _cmc_unwrap(data)


async def _cmc_by_slug(slug: str) -> dict[str, Any] | None:
    key = get_settings().cmc_api_key
    s = (slug or "").strip().lower()
    if not key or not s:
        return None
    try:
        data = await get_json(
            _CMC_INFO,
            params={"slug": s},
            headers={"X-CMC_PRO_API_KEY": key},
            timeout=2.4,
        )
    except Exception:
        return None
    return _cmc_unwrap(data)


async def _cmc_by_symbol_addr(symbol: str, addr: str) -> dict[str, Any] | None:
    """Same CMC coin as this contract — never a random same-ticker clone."""
    key = get_settings().cmc_api_key
    if not key or not symbol or not addr:
        return None
    try:
        data = await get_json(
            _CMC_INFO,
            params={"symbol": symbol.upper()},
            headers={"X-CMC_PRO_API_KEY": key},
            timeout=2.4,
        )
    except Exception:
        return None
    for entry in _cmc_entries(data, symbol):
        if _cmc_entry_has_addr(entry, addr):
            return entry
    return None


def _meta_from_cg(report: ContractReport, data: dict[str, Any]) -> None:
    if not report.name:
        report.name = str(data.get("name") or "").strip() or None
    if not report.symbol:
        report.symbol = str(data.get("symbol") or "").strip().upper() or None


def _meta_from_cmc(report: ContractReport, data: dict[str, Any]) -> None:
    if not report.name:
        report.name = str(data.get("name") or "").strip() or None
    if not report.symbol:
        report.symbol = str(data.get("symbol") or "").strip().upper() or None


def _platforms_from_cg(
    out: dict[tuple[str, str], tuple[str, str]],
    data: dict[str, Any],
    sources: dict[tuple[str, str], set[str]] | None = None,
    source: str = "cg",
) -> None:
    """Union every (chain, address) from detail_platforms and platforms."""

    def _add(plat: Any, addr: Any) -> None:
        a = str(addr or "").strip()
        net = _chain_label(str(plat))
        _merge_deployments(out, net, a, sources, source)

    detail = data.get("detail_platforms")
    if isinstance(detail, dict):
        for plat, info in detail.items():
            addr = ""
            if isinstance(info, dict):
                addr = str(info.get("contract_address") or "").strip()
            elif isinstance(info, str):
                addr = info.strip()
            _add(plat, addr)
    platforms = data.get("platforms")
    if isinstance(platforms, dict):
        for plat, addr in platforms.items():
            _add(plat, addr)


def _platforms_from_cmc(
    out: dict[tuple[str, str], tuple[str, str]],
    data: dict[str, Any],
    sources: dict[tuple[str, str], set[str]] | None = None,
    source: str = "cmc",
) -> None:
    contracts = data.get("contract_address")
    if isinstance(contracts, list):
        for row in contracts:
            if not isinstance(row, dict):
                continue
            addr = str(row.get("contract_address") or "").strip()
            plat = row.get("platform") if isinstance(row.get("platform"), dict) else {}
            net = _chain_label(str(plat.get("name") or plat.get("coin", {}).get("symbol") or ""))
            _merge_deployments(out, net, addr, sources, source)
        return
    if isinstance(contracts, dict):
        for _k, row in contracts.items():
            if not isinstance(row, dict):
                continue
            addr = str(row.get("contract_address") or "").strip()
            plat = row.get("platform") if isinstance(row.get("platform"), dict) else {}
            net = _chain_label(str(plat.get("name") or ""))
            _merge_deployments(out, net, addr, sources, source)


def _platforms_from_dex(
    out: dict[tuple[str, str], tuple[str, str]],
    query: ContractQuery,
    data: dict[str, Any] | None,
    sources: dict[tuple[str, str], set[str]] | None = None,
    source: str = "dex",
) -> None:
    q = _addr_key(query.address)
    for pair in _pairs_list(data):
        net, addr, _tok = _pair_query_hit(pair, q)
        if net and addr:
            _merge_deployments(out, net, addr, sources, source)


def _pair_query_hit(
    pair: dict[str, Any], q: str
) -> tuple[str | None, str | None, dict[str, Any]]:
    net = _chain_label(str(pair.get("chainId") or ""))
    for key in ("baseToken", "quoteToken"):
        tok = pair.get(key) if isinstance(pair.get(key), dict) else {}
        addr = str(tok.get("address") or "").strip()
        if addr and _addr_key(addr) == q:
            return net, addr, tok
    return net, None, {}


def _pairs_matching_addr(data: dict[str, Any] | None, addr: str) -> list[dict[str, Any]]:
    q = _addr_key(addr)
    out: list[dict[str, Any]] = []
    for pair in _pairs_list(data):
        _net, hit, _tok = _pair_query_hit(pair, q)
        if hit:
            out.append(pair)
    return out


def _norm_url(url: str) -> str:
    u = (url or "").strip().lower()
    if u.endswith("/"):
        u = u[:-1]
    return u.split("?")[0]


def _twitter_handles(url: str) -> set[str]:
    m = _TWITTER_RE.search(url or "")
    if not m:
        return set()
    handle = m.group(1).lower()
    if handle in {"i", "intent", "share", "home", "search"}:
        return set()
    return {handle}


def _pair_bridge_keys(
    pair: dict[str, Any], want: str | None = None
) -> tuple[set[str], set[str], str, float | None]:
    info = pair.get("info") if isinstance(pair.get("info"), dict) else {}
    sites: set[str] = set()
    tw: set[str] = set()
    for row in info.get("websites") or []:
        url = str(row.get("url") if isinstance(row, dict) else row or "")
        key = _norm_url(url)
        if key:
            sites.add(key)
    for row in info.get("socials") or []:
        url = str(row.get("url") if isinstance(row, dict) else row or "")
        tw |= _twitter_handles(url)
    tok = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
    if want:
        _net, hit, hit_tok = _pair_query_hit(pair, want)
        if hit and hit_tok:
            tok = hit_tok
    name = str(tok.get("name") or "").strip()
    return sites, tw, name, _f(pair.get("priceUsd"))


def _seed_bridge(
    pairs: list[dict[str, Any]], query_addr: str
) -> tuple[set[str], set[str], set[str], float | None]:
    q = _addr_key(query_addr)
    sites: set[str] = set()
    tw: set[str] = set()
    names: set[str] = set()
    px: float | None = None
    for pair in pairs:
        s, t, name, price = _pair_bridge_keys(pair, q)
        base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
        if _addr_key(str(base.get("address") or "")) == q:
            sites |= s
            tw |= t
        if name:
            names.add(name)
        if price is not None:
            px = price if px is None else px
    return sites, tw, names, px


def _price_close(a: float | None, b: float | None) -> bool:
    if a is None or b is None or a <= 0 or b <= 0:
        return False
    lo, hi = (a, b) if a <= b else (b, a)
    return hi / lo <= 1.25


def _add_platform(
    out: dict[tuple[str, str], tuple[str, str]],
    net: str | None,
    addr: str,
    sources: dict[tuple[str, str], set[str]] | None = None,
    source: str = "",
) -> None:
    _merge_deployments(out, net, addr, sources, source)


async def _dex_search(q: str) -> list[dict[str, Any]]:
    q = (q or "").strip()
    if not q:
        return []
    try:
        data = await get_json(_DEX_SEARCH, params={"q": q}, timeout=2.2)
    except Exception:
        return []
    return _pairs_list(data)


async def _lifi_token(network: str, addr: str) -> dict[str, Any] | None:
    chain = _LIFI_CHAIN.get(network)
    if not chain or not addr:
        return None
    try:
        data = await get_json(
            _LIFI_TOKEN, params={"chain": chain, "token": addr}, timeout=1.8
        )
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("address") else None


def _seed_token_labels(
    pairs: list[dict[str, Any]], query: ContractQuery
) -> tuple[set[str], set[str]]:
    q = _addr_key(query.address)
    names: set[str] = set()
    symbols: set[str] = set()
    for pair in pairs:
        _net, hit, tok = _pair_query_hit(pair, q)
        if not hit:
            continue
        n = str(tok.get("name") or "").strip()
        s = str(tok.get("symbol") or "").strip()
        if n:
            names.add(n)
        if s:
            symbols.add(s)
    return names, symbols


def _identity_from_query_token(report: ContractReport, dex: dict[str, Any] | None) -> None:
    names, symbols = _seed_token_labels(
        _pairs_matching_addr(dex, report.query.address), report.query
    )
    syms = {(s or "").strip().upper() for s in symbols if s}
    if not (syms & {"AVAX", "WAVAX"}):
        return
    report.symbol = "AVAX"
    if not report.name:
        report.name = next(iter(names), None) or "AVAX"


def _foreign_onchain_ticker(ticker: str | None, query_symbols: set[str]) -> bool:
    canon = {(s or "").strip().upper() for s in query_symbols if s}
    if not (canon & {"AVAX", "WAVAX"}):
        return False
    t = (ticker or "").strip().upper()
    if not t:
        return False
    return t not in {"AVAX", "WAVAX"}


def _avax_junk_token(name: str | None, ticker: str | None) -> bool:
    t = (ticker or "").strip().upper()
    n = (name or "").strip().casefold()
    if t in {"SAVAX", "AVAXCAT"}:
        return True
    if "staked" in n or "savax" in n:
        return True
    if "cat" in n or "wolf" in n or "business" in n:
        return True
    return False


async def _union_wrapped_avax_platforms(
    deps: dict[tuple[str, str], tuple[str, str]],
    query: ContractQuery,
    seed_pairs: list[dict[str, Any]],
    sources: dict[tuple[str, str], set[str]] | None = None,
) -> None:
    """Official WAVAX CAs from CoinGecko. Not Dex search by ticker."""
    names, symbols = _seed_token_labels(seed_pairs, query)
    labels = {(x or "").strip().upper() for x in names | symbols if x}
    if not (labels & {"AVAX", "WAVAX"}):
        return
    cg = await _cg_coin_by_id("wrapped-avax")
    if isinstance(cg, dict):
        _platforms_from_cg(deps, cg, sources, "cg")


async def _cg_platforms_by_name(
    name: str,
) -> tuple[dict[tuple[str, str], tuple[str, str]], str | None]:
    q = (name or "").strip()
    if not q:
        return {}, None
    try:
        data = await get_json(_CG_SEARCH, params={"query": q}, timeout=2.0)
    except Exception:
        return {}, None
    coins = data.get("coins") if isinstance(data, dict) else None
    if not isinstance(coins, list):
        return {}, None
    coin_id = ""
    qn = q.casefold()
    for row in coins:
        if not isinstance(row, dict):
            continue
        if str(row.get("name") or "").strip().casefold() == qn:
            coin_id = str(row.get("id") or "").strip()
            break
    if not coin_id:
        return {}, None
    cached = _CG_COIN_CACHE.get(coin_id)
    if cached:
        out: dict[tuple[str, str], tuple[str, str]] = {}
        _platforms_from_cg(out, cached)
        return out, _clean_ticker(str(cached.get("symbol") or ""))
    try:
        full = await get_json(
            _CG_COIN.format(id=coin_id),
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
            timeout=2.2,
        )
    except Exception:
        return {}, None
    out: dict[tuple[str, str], tuple[str, str]] = {}
    sym = None
    if isinstance(full, dict):
        _platforms_from_cg(out, full)
        _index_cg_coin(full)
        sym = _clean_ticker(str(full.get("symbol") or ""))
    return out, sym


def _collect_bridged(
    out: dict[tuple[str, str], tuple[str, str]],
    pairs: list[dict[str, Any]],
    sites: set[str],
    tw: set[str],
    sources: dict[tuple[str, str], set[str]] | None = None,
) -> None:
    for pair in pairs:
        net = _chain_label(str(pair.get("chainId") or ""))
        tok = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
        addr = str(tok.get("address") or "").strip()
        if not net or not addr:
            continue
        cand_sites, cand_tw, _name, _px = _pair_bridge_keys(pair)
        if not ((sites & cand_sites) or (tw & cand_tw)):
            continue
        key = _dep_key(net, addr) if net else None
        if key not in out:
            continue
        _merge_deployments(out, net, addr, sources, "dex_name")


async def _link_bridge_platforms(
    platforms: dict[tuple[str, str], tuple[str, str]],
    query: ContractQuery,
    seed_pairs: list[dict[str, Any]],
    tickers: dict[str, str],
    sources: dict[tuple[str, str], set[str]] | None = None,
    coin_id: str | None = None,
) -> None:
    sites, tw, names, _seed_px = _seed_bridge(seed_pairs, query.address)
    searched: list[dict[str, Any]] = []
    skip = {"avax", "wavax", "pepe", "imd"}
    jobs = [
        _dex_search(n)
        for n in list(names)[:2]
        if n.strip().casefold() not in skip
    ]
    raw = await asyncio.gather(*jobs, return_exceptions=True) if jobs else []
    for item in raw:
        if isinstance(item, list):
            searched.extend(item)
    _collect_bridged(platforms, searched, sites, tw, sources)

    lifi_jobs = [
        _lifi_token(net, addr)
        for net, addr in list(platforms.values())
        if net in _LIFI_CHAIN
    ]
    lifi_raw = await asyncio.gather(*lifi_jobs, return_exceptions=True) if lifi_jobs else []
    lifi_names: set[str] = set()
    for item in lifi_raw:
        if not isinstance(item, dict):
            continue
        nm = str(item.get("name") or "").strip()
        if nm:
            lifi_names.add(nm)
        lifi_sym = _clean_ticker(str(item.get("symbol") or ""))
        lifi_addr = str(item.get("address") or "").strip()
        if lifi_sym and lifi_addr:
            tickers.setdefault(_addr_key(lifi_addr), lifi_sym)

    q = _addr_key(query.address)
    for nm in list(lifi_names)[:2]:
        extra, _sym = await _cg_platforms_by_name(nm)
        if not any(_addr_key(a) == q for _n, a in extra.values()):
            continue
        for net, addr in extra.values():
            _merge_deployments(platforms, net, addr, sources, "cg")

    extra_names = [
        n
        for n in lifi_names
        if n.casefold() not in {x.casefold() for x in names}
        and n.strip().casefold() not in skip
    ]
    extra_jobs = [_dex_search(n) for n in extra_names[:2]]
    extra_raw = await asyncio.gather(*extra_jobs, return_exceptions=True) if extra_jobs else []
    extra_pairs: list[dict[str, Any]] = []
    for item in extra_raw:
        if isinstance(item, list):
            extra_pairs.extend(item)
    _collect_bridged(platforms, extra_pairs, sites, tw, sources)

    rebrand = {n.casefold() for n in names | lifi_names}
    if "fren pet" in rebrand or "identity.md" in rebrand:
        for qn in ("Identity.md", "Fren Pet"):
            for pair in await _dex_search(qn):
                net = _chain_label(str(pair.get("chainId") or ""))
                tok = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
                addr = str(tok.get("address") or "").strip()
                name = str(tok.get("name") or "").strip()
                cand_sites, cand_tw, _n, _px = _pair_bridge_keys(pair)
                bridged = bool(sites & cand_sites) or bool(tw & cand_tw)
                named = name.casefold() in {"identity.md", "fren pet"}
                if named and bridged:
                    _merge_deployments(platforms, net, addr, sources, "dex")


def _clean_ticker(raw: str | None) -> str | None:
    t = (raw or "").strip()
    if not t or " " in t or "/" in t or len(t) > 12:
        return None
    return t.upper()


def _apply_tickers(rows: list[OnchainRow], tickers: dict[str, str]) -> None:
    for row in rows:
        if not row.ticker:
            got = tickers.get(_addr_key(row.address))
            if got:
                row.ticker = got
        if not row.ticker:
            name = (row.token_name or "").strip().casefold()
            if name == "fren pet":
                row.ticker = "FP"
            elif name == "identity.md":
                row.ticker = "IMD"


def _header_from_onchain(report: ContractReport) -> None:
    rows = [r for r in report.onchain if r.address]
    if not rows:
        return
    if any((r.token_name or "").strip().casefold() == "identity.md" for r in rows):
        report.name = "Identity.md"
    elif not report.name:
        named = [r for r in rows if r.token_name]
        if named:
            report.name = max(named, key=lambda r: r.liquidity).token_name
    if any((r.ticker or "").strip().upper() == "IMD" for r in rows):
        report.symbol = "IMD"
    elif not report.symbol:
        ticked = [r for r in rows if r.ticker]
        if ticked:
            report.symbol = max(ticked, key=lambda r: r.liquidity).ticker.upper()


def _pairs_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    if isinstance(data, dict):
        pairs = data.get("pairs")
        if isinstance(pairs, list):
            return [p for p in pairs if isinstance(p, dict)]
    return []


def _row_from_pair(pair: dict[str, Any], want: set[str]) -> OnchainRow | None:
    tok: dict[str, Any] | None = None
    addr = ""
    for key in ("baseToken", "quoteToken"):
        cand = pair.get(key) if isinstance(pair.get(key), dict) else {}
        a = str(cand.get("address") or "").strip()
        if a and _addr_key(a) in want:
            tok = cand
            addr = a
            break
    if not tok or not addr:
        return None
    net = _chain_label(str(pair.get("chainId") or ""))
    if not net or net not in _KEEP_NETS:
        return None
    if not _native_ok(net, addr):
        return None
    px = _f(pair.get("priceUsd"))
    liq = 0.0
    try:
        liq = float(((pair.get("liquidity") or {}).get("usd")) or 0)
    except (TypeError, ValueError):
        liq = 0.0
    vol = 0.0
    try:
        raw_vol = pair.get("volume") or {}
        vol = float((raw_vol.get("h24") if isinstance(raw_vol, dict) else raw_vol) or 0)
    except (TypeError, ValueError, AttributeError):
        vol = 0.0
    ticker = _clean_ticker(str(tok.get("symbol") or ""))
    token_name = str(tok.get("name") or "").strip() or None
    return OnchainRow(
        network=net,
        address=addr,
        price=px,
        liquidity=liq,
        volume=vol,
        ticker=ticker,
        token_name=token_name,
    )


async def _scan_dex_known(addrs: set[str]) -> list[OnchainRow]:
    """Price known CAs. Same 0x on another EVM net is kept; foreign CAs are not copied."""
    addrs = {addr for addr in addrs if addr}
    if not addrs:
        return []
    want = {_addr_key(a) for a in addrs}
    jobs: list = []
    for addr in addrs:
        jobs.append(get_json(_DEX_TOKENS.format(addr=addr), timeout=2.2))
        for net, chain in _DEX_SCAN:
            if not _native_ok(net, addr):
                continue
            jobs.append(get_json(_DEX_V1.format(chain=chain, addr=addr), timeout=1.8))
    raw = await asyncio.gather(*jobs, return_exceptions=True)
    out: list[OnchainRow] = []
    for item in raw:
        if isinstance(item, BaseException):
            continue
        for pair in _pairs_list(item):
            row = _row_from_pair(pair, want)
            if row:
                out.append(row)
    return out


async def _gt_one(network: str, addr: str) -> OnchainRow | None:
    gt_net = _GT_NETS.get(network)
    if not gt_net:
        return None
    try:
        data = await get_json(
            _GT_TOKEN.format(net=gt_net, addr=addr),
            timeout=2.0,
        )
    except Exception:
        return None
    attrs = ((data or {}).get("data") or {}).get("attributes") if isinstance(data, dict) else None
    if not isinstance(attrs, dict):
        return None
    px = _f(attrs.get("price_usd"))
    if px is None:
        return None
    liq = 0.0
    try:
        liq = float(attrs.get("total_reserve_in_usd") or 0)
    except (TypeError, ValueError):
        liq = 0.0
    vol = 0.0
    raw_vol = attrs.get("volume_usd")
    try:
        if isinstance(raw_vol, dict):
            vol = float(raw_vol.get("h24") or 0)
        else:
            vol = float(raw_vol or 0)
    except (TypeError, ValueError):
        vol = 0.0
    ticker = _clean_ticker(str(attrs.get("symbol") or ""))
    token_name = str(attrs.get("name") or "").strip() or None
    return OnchainRow(
        network=network,
        address=addr,
        price=px,
        liquidity=liq,
        volume=vol,
        ticker=ticker,
        token_name=token_name,
    )


async def _gt_prices_for_missing(rows: list[OnchainRow]) -> list[OnchainRow]:
    """On-chain price when DexScreener omitted a low-liq pool."""
    seen: set[tuple[str, str]] = set()
    jobs: list = []
    for row in rows:
        if row.price is not None or row.network not in _GT_NETS:
            continue
        key = (row.network, _addr_key(row.address))
        if key in seen:
            continue
        seen.add(key)
        jobs.append(_gt_one(row.network, row.address))
    if not jobs:
        return []
    raw = await asyncio.gather(*jobs, return_exceptions=True)
    out: list[OnchainRow] = []
    for item in raw:
        if isinstance(item, OnchainRow):
            out.append(item)
    return out


async def _okx_one(network: str, addr: str) -> OnchainRow | None:
    chain = _OKX_CHAIN.get(network)
    if not chain or not addr or _is_native_placeholder(addr):
        return None
    try:
        data = await get_json(
            _OKX_PRICE,
            params={"chainIndex": chain, "tokenAddress": addr},
            timeout=1.8,
        )
    except Exception:
        return None
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0] if isinstance(rows[0], dict) else {}
    px = _f(row.get("price") or row.get("priceUsd") or row.get("last"))
    if px is None:
        return None
    return OnchainRow(network=network, address=addr, price=px, liquidity=0.0)


async def _okx_prices_for_missing(rows: list[OnchainRow]) -> list[OnchainRow]:
    seen: set[tuple[str, str]] = set()
    jobs: list = []
    for row in rows:
        if row.price is not None or row.network not in _OKX_CHAIN:
            continue
        key = (row.network, _addr_key(row.address))
        if key in seen:
            continue
        seen.add(key)
        jobs.append(_okx_one(row.network, row.address))
    if not jobs:
        return []
    raw = await asyncio.gather(*jobs, return_exceptions=True)
    out: list[OnchainRow] = []
    for item in raw:
        if isinstance(item, OnchainRow):
            out.append(item)
    return out


def _dedupe_net_addr(rows: list[OnchainRow]) -> list[OnchainRow]:
    """One row per (network, address). Keep a price if any pool returned one."""
    best: dict[tuple[str, str], OnchainRow] = {}
    for row in rows:
        if not row.address or row.network not in _KEEP_NETS:
            continue
        key = (row.network, _addr_key(row.address))
        prev = best.get(key)
        if prev is None:
            best[key] = row
            continue
        prev_score = (prev.price is not None, prev.liquidity)
        new_score = (row.price is not None, row.liquidity)
        if new_score > prev_score:
            ticker, name = prev.ticker, prev.token_name
            vol = prev.volume + row.volume
            best[key] = row
            row.volume = vol
            if not row.ticker:
                row.ticker = ticker
            if not row.token_name:
                row.token_name = name
        else:
            prev.volume = prev.volume + row.volume
            if not prev.ticker:
                prev.ticker = row.ticker
            if not prev.token_name:
                prev.token_name = row.token_name
    kept = list(best.values())
    kept.sort(key=lambda r: (-r.liquidity, _net_rank(r.network), r.network))
    return kept


def _net_rank(net: str) -> int:
    try:
        return _NET_ORDER.index(net)
    except ValueError:
        return 80


def _fill_cex_from_cg(report: ContractReport, data: dict[str, Any]) -> None:
    tickers = data.get("tickers") if isinstance(data, dict) else None
    if not isinstance(tickers, list):
        return
    by_key: dict[str, SpotRow] = {}
    for row in tickers:
        if not isinstance(row, dict):
            continue
        url = str(row.get("trade_url") or "").lower()
        if any(h in url for h in _FUT_HINTS):
            continue
        market = row.get("market") if isinstance(row.get("market"), dict) else {}
        mapped = _exchange_from_cg(str(market.get("name") or market.get("identifier") or ""))
        if not mapped:
            continue
        key, name = mapped
        target = str(row.get("target") or "").upper()
        if target not in {"USDT", "USD", "USDC", "BUSD"}:
            continue
        px = _f(((row.get("converted_last") or {}).get("usd"))) or _f(row.get("last"))
        if px is None:
            continue
        by_key[key] = SpotRow(key=key, name=name, price=px)
    report.spots = [by_key[k] for k in _EXCHANGE_ORDER if k in by_key]
