"""Ticker lookup — plain text (sol, btc) or leftover /get."""

from __future__ import annotations

import asyncio
import time

import structlog
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from crypto_bot.config.settings import get_settings
from crypto_bot.domain.exchanges import (
    FETCH_HARD_TIMEOUT,
    LOADING_FOOTER_MIN_EXCHANGES,
    REPORT_EXCHANGE_COUNT,
)
from crypto_bot.services.report_ui import show_loading_footer, with_loading_footer
from crypto_bot.domain.contract_query import parse_contract_query
from crypto_bot.domain.ticker import parse_ticker
from crypto_bot.handlers.contract import reply_contract
from crypto_bot.services.market_service import MarketService
from crypto_bot.services.report_cache import ReportCache

logger = structlog.get_logger(__name__)
router = Router(name="get")

_ANTIFLOOD_SEC = 1.5


def setup_get_router(market: MarketService, cache: ReportCache) -> Router:
    settings = get_settings()
    if settings.turbo_mode:
        first_wait = settings.first_paint_ms / 1000.0
    else:
        first_wait = settings.fast_wait_ms / 1000.0

    # per-user antiflood timestamps
    _last_request: dict[int, float] = {}

    async def _load_snapshots(
        base: str,
        quote: str,
        *,
        task: asyncio.Task | None = None,
    ) -> tuple[list, list]:
        coro = task if task is not None else asyncio.create_task(market.fetch_fast(base, quote))
        try:
            return await asyncio.wait_for(asyncio.shield(coro), timeout=FETCH_HARD_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("fetch_hard_timeout", base=base)
            # DO NOT cancel — task may belong to other users
            return [], []

    async def _deliver(
        base: str,
        quote: str,
        snapshots: list,
        contracts: list,
        *,
        status: Message | None,
        message: Message,
        t0: float,
    ) -> Message:
        report = market.report_text(base, quote, snapshots, contracts)
        ex_count = market.exchange_count(snapshots)
        if show_loading_footer(ex_count, target=LOADING_FOOTER_MIN_EXCHANGES):
            report = with_loading_footer(report)
        cache.set(base, quote, report, snapshots, contracts, complete=False)
        if status is not None:
            await status.edit_text(report, disable_web_page_preview=True)
            out = status
        else:
            out = await message.answer(report, disable_web_page_preview=True)
        logger.info(
            "fast_report",
            base=base,
            ms=int((time.perf_counter() - t0) * 1000),
            exchanges=len([s for s in snapshots if s.has_data]),
        )
        market.schedule_enrich(base, quote, snapshots, contracts, out, cache)
        return out

    async def _lookup(message: Message, raw: str) -> None:
        # antiflood
        uid = message.from_user.id if message.from_user else 0
        now = time.monotonic()
        if now - _last_request.get(uid, 0) < _ANTIFLOOD_SEC:
            await message.answer("⏳ Wait a moment…")
            return
        _last_request[uid] = now

        contract = parse_contract_query(raw)
        if contract is not None:
            await reply_contract(message, contract)
            return

        try:
            base, quote = parse_ticker(raw, settings.default_quote)
        except ValueError as exc:
            await message.answer(f"❌ {exc}")
            return

        t0 = time.perf_counter()
        pair_key = f"{base.upper()}:{quote.upper()}"
        market.kick_wallet_prefetch(base)

        # --- fast cache hit (complete or partial) ---
        instant = market.peek_fast(base, quote)
        if instant:
            text = instant.text
            if show_loading_footer(
                market.exchange_count(instant.snapshots),
                target=LOADING_FOOTER_MIN_EXCHANGES,
            ):
                text = with_loading_footer(text)
            msg = await message.answer(text, disable_web_page_preview=True)
            logger.info("fast_cache_hit", base=base, ms=int((time.perf_counter() - t0) * 1000))
            if market.exchange_count(instant.snapshots) > 0 and (
                not instant.complete
                or market.needs_enrich(
                    instant.snapshots, instant.contracts, base
                )
            ):
                market.schedule_enrich(
                    base, quote, instant.snapshots, instant.contracts, msg, cache
                )
            return

        # --- report cache ---
        peek = cache.peek(base, quote)
        if peek:
            cached, stale = peek
            msg = await message.answer(cached.text, disable_web_page_preview=True)
            logger.info(
                "cache_hit",
                base=base,
                ms=int((time.perf_counter() - t0) * 1000),
                stale=stale,
                complete=cached.complete,
            )
            if (
                stale
                or not cached.complete
                or market.needs_enrich(cached.snapshots, cached.contracts, base)
            ):
                market.schedule_enrich(
                    base,
                    quote,
                    cached.snapshots,
                    cached.contracts,
                    msg,
                    cache,
                    refresh_prices=stale,
                )
            return

        # --- fresh fetch, join inflight if exists ---
        inflight = market.get_inflight_fast(pair_key)
        fetch_task = inflight if inflight else asyncio.create_task(
            market.fetch_fast(base, quote)
        )
        if fetch_task is not inflight:
            market.set_inflight_fast(pair_key, fetch_task)

        status: Message | None = None
        try:
            try:
                snapshots, contracts = await asyncio.wait_for(
                    asyncio.shield(fetch_task), timeout=first_wait
                )
            except asyncio.TimeoutError:
                snapshots, contracts = [], []
                if fetch_task.done():
                    try:
                        snapshots, contracts = fetch_task.result()
                    except Exception:
                        pass
                if not snapshots:
                    status = await message.answer(f"⏳ <b>{base}</b>…")
                    snapshots, contracts = await _load_snapshots(
                        base, quote, task=fetch_task
                    )

            await _deliver(
                base,
                quote,
                snapshots,
                contracts,
                status=status,
                message=message,
                t0=t0,
            )
        except Exception:
            logger.exception("get_error", base=base)
            err = "❌ Error. Please try again."
            if status is not None:
                await status.edit_text(err)
            else:
                await message.answer(err)
        finally:
            market.clear_inflight_fast(pair_key, fetch_task)

    @router.message(Command("get"))
    async def cmd_get(message: Message, command: CommandObject) -> None:
        if not command.args:
            await message.answer("Send a ticker: <code>sol</code>")
            return
        await _lookup(message, command.args.strip().split()[0])

    @router.message(F.text)
    async def on_ticker(message: Message) -> None:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return
        await _lookup(message, text.split()[0])

    return router
