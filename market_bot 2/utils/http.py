"""Persistent aiohttp session — lives for the whole bot process."""

from __future__ import annotations

import asyncio
import ssl
from typing import Any

import aiohttp
import certifi

_session: aiohttp.ClientSession | None = None
_lock = asyncio.Lock()
_initialized = False


def _aio_timeout(seconds: float) -> aiohttp.ClientTimeout:
    conn = min(0.35, max(0.12, seconds))
    return aiohttp.ClientTimeout(
        total=seconds, connect=conn, sock_connect=conn, sock_read=seconds
    )


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


async def init_session() -> aiohttp.ClientSession:
    """Create session once; reuse forever (no lazy re-create on requests)."""
    global _session, _initialized
    async with _lock:
        if _session is not None and not _session.closed:
            return _session
        timeout = aiohttp.ClientTimeout(total=0.90, connect=0.25, sock_connect=0.25, sock_read=0.80)
        connector = aiohttp.TCPConnector(
            limit=128,
            limit_per_host=32,
            ttl_dns_cache=7200,
            keepalive_timeout=120,
            enable_cleanup_closed=True,
            ssl=_ssl_context(),
        )
        _session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"User-Agent": "market-bot/2.0", "Connection": "keep-alive"},
        )
        _initialized = True
        return _session


async def get_session() -> aiohttp.ClientSession:
    if _session is not None and not _session.closed:
        return _session
    return await init_session()


async def close_session() -> None:
    """Only for tests — production bot keeps session open."""
    global _session, _initialized
    async with _lock:
        if _session and not _session.closed:
            await _session.close()
        _session = None
        _initialized = False


async def ping_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 1.2,
) -> bool:
    session = await get_session()
    try:
        async with session.get(
            url,
            params=params,
            timeout=_aio_timeout(timeout),
        ) as resp:
            await resp.read()
            return resp.status < 500
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False


async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 0.70,
) -> Any:
    session = await get_session()
    try:
        async with session.get(
            url,
            params=params,
            headers=headers,
            timeout=_aio_timeout(timeout),
        ) as resp:
            if resp.status >= 400:
                return None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


async def post_form(
    url: str,
    data: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 0.70,
) -> Any:
    session = await get_session()
    try:
        async with session.post(
            url,
            data=data,
            headers=headers,
            timeout=_aio_timeout(timeout),
        ) as resp:
            if resp.status >= 400:
                return None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


async def post_json(
    url: str,
    body: Any,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 0.70,
) -> Any:
    session = await get_session()
    try:
        async with session.post(
            url,
            json=body,
            headers=headers,
            timeout=_aio_timeout(timeout),
        ) as resp:
            if resp.status >= 400:
                return None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None
