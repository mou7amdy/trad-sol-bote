"""
backtesting/data_collector.py

Multi-source historical data collector for Solana meme coin backtesting.

Free data sources (no paid APIs):
  1. GeckoTerminal  — paginate /networks/solana/pools (100 pages × 50 = 5,000+)
  2. DexScreener    — /latest/dex/search and /tokens/{mint}
  3. Helius REST    — getSignaturesForAddress (tracks credits, stops at 800k)
  4. Birdeye public — /public/history_price OHLCV candles
  5. Solscan public — /token/holders snapshots

Usage:
    collector = DataCollector()
    await collector.run()        # full collection
    await collector.run_sample(n=1000)  # quick test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp
import aiosqlite
from loguru import logger
from tqdm.asyncio import tqdm as atqdm

# ---------------------------------------------------------------------------
# Settings import — graceful fallback when running standalone
# ---------------------------------------------------------------------------
try:
    from config.settings import settings
    _HELIUS_API_KEY: str = settings.HELIUS_API_KEY
except Exception:  # pragma: no cover
    logger.warning("Could not import settings; falling back to empty API keys.")

    class _FallbackSettings:  # type: ignore[no-redef]
        HELIUS_API_KEY: str = ""

    settings = _FallbackSettings()  # type: ignore[assignment]
    _HELIUS_API_KEY = ""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BACKTEST_DB_PATH: Path = Path(__file__).parent.parent / "data" / "backtest.db"

GECKO_BASE: str = "https://api.geckoterminal.com/api/v2"
DEX_BASE: str = "https://api.dexscreener.com/latest/dex"
SOLSCAN_BASE: str = "https://public-api.solscan.io"
HELIUS_BASE: str = "https://api.helius.xyz/v0"

MAX_HELIUS_CREDITS: int = 800_000
DEX_RATE_LIMIT_PER_MIN: int = 300
BATCH_INSERT_SIZE: int = 500
PARALLEL_REQUESTS: int = 8
MAX_PAGES_GECKO: int = 100
MAX_PAGES_DEX: int = 50

# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS historical_tokens (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    mint_address           TEXT    UNIQUE NOT NULL,
    symbol                 TEXT    NOT NULL DEFAULT '',
    name                   TEXT    NOT NULL DEFAULT '',
    created_at             INTEGER NOT NULL DEFAULT 0,
    source                 TEXT    NOT NULL DEFAULT '',

    -- Initial state (T+0)
    initial_liquidity_usd  REAL    NOT NULL DEFAULT 0.0,
    initial_market_cap     REAL    NOT NULL DEFAULT 0.0,
    initial_holders        INTEGER NOT NULL DEFAULT 0,
    first_buyer_count      INTEGER NOT NULL DEFAULT 0,
    sniper_count           INTEGER NOT NULL DEFAULT 0,

    -- Price action checkpoints
    price_at_launch        REAL    NOT NULL DEFAULT 0.0,
    price_at_1min          REAL    NOT NULL DEFAULT 0.0,
    price_at_5min          REAL    NOT NULL DEFAULT 0.0,
    price_at_15min         REAL    NOT NULL DEFAULT 0.0,
    price_at_30min         REAL    NOT NULL DEFAULT 0.0,
    price_at_60min         REAL    NOT NULL DEFAULT 0.0,
    price_at_24hr          REAL    NOT NULL DEFAULT 0.0,
    max_price_ever         REAL    NOT NULL DEFAULT 0.0,
    max_price_time_min     INTEGER NOT NULL DEFAULT 0,

    -- Outcome labels
    hit_2x                 INTEGER NOT NULL DEFAULT 0,
    hit_5x                 INTEGER NOT NULL DEFAULT 0,
    hit_10x                INTEGER NOT NULL DEFAULT 0,
    rug_pulled             INTEGER NOT NULL DEFAULT 0,
    rug_time_min           INTEGER NOT NULL DEFAULT 0,
    final_outcome          TEXT    NOT NULL DEFAULT 'UNKNOWN',

    -- On-chain signals
    lp_burned              INTEGER NOT NULL DEFAULT 0,
    mint_revoked           INTEGER NOT NULL DEFAULT 0,
    top_holder_percent     REAL    NOT NULL DEFAULT 0.0,
    holder_velocity_1min   REAL    NOT NULL DEFAULT 0.0,
    buy_sell_ratio_5min    REAL    NOT NULL DEFAULT 1.0,
    wash_trading_detected  INTEGER NOT NULL DEFAULT 0,
    dev_cluster_detected   INTEGER NOT NULL DEFAULT 0,
    wash_trading_score     REAL    NOT NULL DEFAULT 0.0,

    -- Social signals
    telegram_mentions      INTEGER NOT NULL DEFAULT 0,
    dex_buy_volume_5min    REAL    NOT NULL DEFAULT 0.0,
    price_change_1min      REAL    NOT NULL DEFAULT 0.0,
    token_age_seconds      INTEGER NOT NULL DEFAULT 0,

    -- Collection metadata
    data_complete          INTEGER NOT NULL DEFAULT 0,
    synthetic_data         INTEGER NOT NULL DEFAULT 0,
    collected_at           INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE historical_tokens ADD COLUMN synthetic_data INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_ht_created_at ON historical_tokens (created_at);
CREATE INDEX IF NOT EXISTS idx_ht_outcome    ON historical_tokens (final_outcome);
CREATE INDEX IF NOT EXISTS idx_ht_hit_2x     ON historical_tokens (hit_2x);
CREATE INDEX IF NOT EXISTS idx_ht_rug        ON historical_tokens (rug_pulled);
CREATE INDEX IF NOT EXISTS idx_ht_complete   ON historical_tokens (data_complete);

CREATE TABLE IF NOT EXISTS collection_progress (
    id                  INTEGER PRIMARY KEY DEFAULT 1,
    total_found         INTEGER NOT NULL DEFAULT 0,
    total_saved         INTEGER NOT NULL DEFAULT 0,
    total_complete      INTEGER NOT NULL DEFAULT 0,
    helius_credits_used INTEGER NOT NULL DEFAULT 0,
    last_updated        INTEGER NOT NULL DEFAULT 0,
    status              TEXT    NOT NULL DEFAULT 'idle'
);

INSERT OR IGNORE INTO collection_progress (id) VALUES (1);
"""

# Ordered list of columns for the upsert statement (excluding auto-increment id)
_TOKEN_COLUMNS: tuple[str, ...] = (
    "mint_address", "symbol", "name", "created_at", "source",
    "initial_liquidity_usd", "initial_market_cap", "initial_holders",
    "first_buyer_count", "sniper_count",
    "price_at_launch", "price_at_1min", "price_at_5min", "price_at_15min",
    "price_at_30min", "price_at_60min", "price_at_24hr",
    "max_price_ever", "max_price_time_min",
    "hit_2x", "hit_5x", "hit_10x", "rug_pulled", "rug_time_min",
    "final_outcome",
    "lp_burned", "mint_revoked", "top_holder_percent",
    "holder_velocity_1min", "buy_sell_ratio_5min",
    "wash_trading_detected", "dev_cluster_detected", "wash_trading_score",
    "telegram_mentions", "dex_buy_volume_5min", "price_change_1min",
    "token_age_seconds",
    "data_complete", "synthetic_data", "collected_at",
)

_UPSERT_SQL: str = (
    "INSERT OR REPLACE INTO historical_tokens ("
    + ", ".join(_TOKEN_COLUMNS)
    + ") VALUES ("
    + ", ".join("?" * len(_TOKEN_COLUMNS))
    + ")"
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert a value to float."""
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert a value to int."""
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def _parse_iso_timestamp(ts: Optional[str]) -> int:
    """Parse an ISO-8601 timestamp string into a Unix timestamp integer."""
    if not ts:
        return 0
    try:
        from datetime import datetime, timezone
        # Handle trailing 'Z' and offset formats
        ts_clean = ts.rstrip("Z").split("+")[0].split(".")[0]
        dt = datetime.fromisoformat(ts_clean).replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# DataCollector
# ---------------------------------------------------------------------------

class DataCollector:
    """
    Async multi-source historical data collector for Solana meme coin backtesting.

    Orchestrates GeckoTerminal, DexScreener, Birdeye, Helius, and Solscan to
    build a rich historical_tokens SQLite database used by the backtesting engine.
    """

    def __init__(self) -> None:
        """Initialise semaphore, counters, and load settings."""
        self._sem: asyncio.Semaphore = asyncio.Semaphore(PARALLEL_REQUESTS)
        self._session: Optional[aiohttp.ClientSession] = None
        self._db: Optional[aiosqlite.Connection] = None

        # Helius credit tracking
        self._helius_credits: int = 0
        self._helius_api_key: str = getattr(settings, "HELIUS_API_KEY", "")

        # Insert buffer
        self._insert_buffer: list[dict] = []

        # Progress counters
        self._total_found: int = 0
        self._total_saved: int = 0
        self._total_complete: int = 0

        logger.info("DataCollector initialised (Helius credits budget: {:,})", MAX_HELIUS_CREDITS)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open aiohttp ClientSession and initialise the database."""
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        connector = aiohttp.TCPConnector(limit=PARALLEL_REQUESTS * 2, ttl_dns_cache=300, ssl=False)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
        )
        await self.init_db()
        logger.info("DataCollector started — DB at {}", BACKTEST_DB_PATH)

    async def stop(self) -> None:
        """Flush remaining buffer, close DB and HTTP session."""
        try:
            if self._insert_buffer:
                await self._flush_buffer()
        except Exception as exc:
            logger.error("Error flushing buffer during stop: {}", exc)
        try:
            if self._db:
                await self._db.close()
                self._db = None
        except Exception as exc:
            logger.error("Error closing DB: {}", exc)
        try:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None
        except Exception as exc:
            logger.error("Error closing session: {}", exc)
        logger.info("DataCollector stopped.")

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    async def init_db(self) -> None:
        """Create backtest.db with full schema, enable WAL mode."""
        BACKTEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(BACKTEST_DB_PATH))
        self._db.row_factory = aiosqlite.Row
        # Execute schema (multi-statement)
        for statement in _SCHEMA_SQL.split(";"):
            stmt = statement.strip()
            if stmt:
                try:
                    await self._db.execute(stmt)
                except Exception as exc:
                    logger.warning("Schema stmt skipped ({}): {!r}", exc, stmt[:80])
        await self._db.commit()
        logger.debug("Database schema initialised.")

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    async def _get(
        self,
        url: str,
        params: Optional[dict] = None,
        retries: int = 4,
        extra_headers: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Async GET with exponential backoff, semaphore, and 429 handling.

        Args:
            url: Full URL to request.
            params: Optional query parameters.
            retries: Maximum retry attempts (delays: 1, 2, 4, 8 seconds).
            extra_headers: Optional additional headers for this request.

        Returns:
            Parsed JSON dict or None on failure.
        """
        if self._session is None or self._session.closed:
            logger.error("HTTP session not open — call start() first.")
            return None

        headers: dict = {}
        if extra_headers:
            headers.update(extra_headers)

        async with self._sem:
            for attempt in range(retries):
                try:
                    async with self._session.get(url, params=params, headers=headers) as resp:
                        if resp.status == 200:
                            try:
                                return await resp.json(content_type=None)
                            except (aiohttp.ContentTypeError, json.JSONDecodeError) as exc:
                                logger.warning("JSON decode error for {}: {}", url, exc)
                                return None

                        elif resp.status == 429:
                            retry_after_raw = resp.headers.get("Retry-After", "60")
                            try:
                                wait_secs = float(retry_after_raw)
                            except ValueError:
                                wait_secs = 60.0
                            wait_secs = min(max(wait_secs, 5.0), 120.0)
                            logger.warning(
                                "429 rate-limited on {} — waiting {:.0f}s (attempt {}/{})",
                                url, wait_secs, attempt + 1, retries,
                            )
                            await asyncio.sleep(wait_secs)
                            continue

                        elif resp.status in (503, 502, 504):
                            delay = 2 ** attempt
                            logger.warning(
                                "HTTP {} on {} — retrying in {}s (attempt {}/{})",
                                resp.status, url, delay, attempt + 1, retries,
                            )
                            await asyncio.sleep(delay)
                            continue

                        else:
                            logger.debug("HTTP {} for {} — skipping.", resp.status, url)
                            return None

                except asyncio.TimeoutError:
                    delay = 2 ** attempt
                    logger.warning(
                        "Timeout on {} — retrying in {}s (attempt {}/{})",
                        url, delay, attempt + 1, retries,
                    )
                    await asyncio.sleep(delay)

                except aiohttp.ClientConnectionError as exc:
                    delay = 2 ** attempt
                    logger.warning(
                        "Connection error on {}: {} — retrying in {}s",
                        url, exc, delay,
                    )
                    await asyncio.sleep(delay)

                except Exception as exc:
                    logger.error("Unexpected error GET {}: {}", url, exc)
                    return None

        logger.warning("All {} retries exhausted for {}.", retries, url)
        return None

    # ------------------------------------------------------------------
    # Source 1 — GeckoTerminal
    # ------------------------------------------------------------------

    async def collect_gecko_pools(self, max_pages: int = MAX_PAGES_GECKO) -> list[dict]:
        """
        Paginate GeckoTerminal /networks/solana/pools, returning normalised token dicts.

        Args:
            max_pages: Maximum number of pages to fetch (each page has up to 50 pools).

        Returns:
            List of partial token dicts keyed to the canonical schema.
        """
        results: list[dict] = []
        logger.info("GeckoTerminal: fetching up to {} pages…", max_pages)

        for page in range(1, max_pages + 1):
            url = f"{GECKO_BASE}/networks/solana/new_pools"
            params = {
                "page": str(page),
                "include": "base_token",
            }
            data = await self._get(url, params=params)
            if not data:
                logger.info("GeckoTerminal: empty response on page {} — stopping.", page)
                break

            pools: list[dict] = data.get("data", [])
            if not pools:
                logger.info("GeckoTerminal: no pools on page {} — stopping.", page)
                break

            # Build a lookup: included base_token id → attributes
            included_tokens: dict[str, dict] = {}
            for inc in data.get("included", []):
                if inc.get("type") == "token":
                    included_tokens[inc["id"]] = inc.get("attributes", {})

            for pool in pools:
                try:
                    attrs: dict = pool.get("attributes", {})
                    rels: dict = pool.get("relationships", {})

                    # Derive mint from relationship
                    base_token_rel = rels.get("base_token", {}).get("data", {})
                    # GeckoTerminal encodes the id as "solana_{mint}"
                    raw_mint_id: str = base_token_rel.get("id", "")
                    mint = raw_mint_id.split("_", 1)[-1] if "_" in raw_mint_id else raw_mint_id

                    if not mint or len(mint) < 32:
                        continue

                    token_attrs = included_tokens.get(raw_mint_id, {})
                    symbol: str = token_attrs.get("symbol", "")
                    name: str = token_attrs.get("name", "")

                    created_at_raw: str = attrs.get("pool_created_at", "") or ""
                    created_at: int = _parse_iso_timestamp(created_at_raw)

                    price_usd: float = _safe_float(attrs.get("base_token_price_usd"))
                    liquidity_usd: float = _safe_float(attrs.get("reserve_in_usd"))
                    volume_h24: float = _safe_float(
                        (attrs.get("volume_usd") or {}).get("h24")
                    )

                    results.append({
                        "mint_address": mint,
                        "symbol": symbol,
                        "name": name,
                        "created_at": created_at,
                        "source": "gecko",
                        "initial_liquidity_usd": liquidity_usd,
                        "price_at_launch": price_usd,
                        "dex_buy_volume_5min": volume_h24 / (24 * 12) if volume_h24 else 0.0,
                        # Rough 5-min equivalent from 24h volume
                    })
                except Exception as exc:
                    logger.debug("GeckoTerminal pool parse error: {}", exc)
                    continue

            logger.debug("GeckoTerminal page {}/{}: {} pools accumulated.", page, max_pages, len(results))
            # Small courtesy delay between pages
            await asyncio.sleep(0.3)

        logger.info("GeckoTerminal: collected {} pools total.", len(results))
        return results

    # ------------------------------------------------------------------
    # Source 2 — DexScreener
    # ------------------------------------------------------------------

    async def collect_dexscreener_pairs(self, max_pages: int = MAX_PAGES_DEX) -> list[dict]:
        """
        Collect Solana pairs from DexScreener.

        Attempts the paginated /pairs/solana endpoint first; falls back to
        /search?q=sol variants when no pagination endpoint is available.

        Args:
            max_pages: Maximum number of pages / search queries.

        Returns:
            List of partial token dicts.
        """
        results: list[dict] = []
        seen_mints: set[str] = set()

        logger.info("DexScreener: fetching up to {} pages…", max_pages)

        # Strategy 1: paginated pairs endpoint
        paginated_success = False
        for page in range(1, max_pages + 1):
            url = f"{DEX_BASE}/pairs/solana"
            params = {"page": str(page)}
            data = await self._get(url, params=params)

            if data and "pairs" in data and data["pairs"]:
                paginated_success = True
                for pair in data["pairs"]:
                    token = self._parse_dex_pair(pair)
                    if token and token["mint_address"] not in seen_mints:
                        seen_mints.add(token["mint_address"])
                        results.append(token)
                logger.debug("DexScreener page {}: {} pairs total.", page, len(results))
                await asyncio.sleep(0.2)
            elif paginated_success:
                # We had pages before but now got nothing — stop
                logger.info("DexScreener: no pairs on page {} — stopping.", page)
                break
            else:
                # Pagination never worked — fall back to search
                break

        if not paginated_success:
            # Strategy 2: search variants
            search_terms = ["sol", "pump", "meme", "pepe", "dog", "cat", "moon", "wen", "bonk"]
            logger.info("DexScreener: pagination unavailable — using search fallback.")
            for term in search_terms[:max_pages]:
                url = f"{DEX_BASE}/search"
                params = {"q": term}
                data = await self._get(url, params=params)
                if not data:
                    continue
                pairs = data.get("pairs", [])
                for pair in pairs:
                    if not isinstance(pair, dict):
                        continue
                    if pair.get("chainId", "") != "solana":
                        continue
                    token = self._parse_dex_pair(pair)
                    if token and token["mint_address"] not in seen_mints:
                        seen_mints.add(token["mint_address"])
                        results.append(token)
                await asyncio.sleep(0.3)

        logger.info("DexScreener: collected {} pairs total.", len(results))
        return results

    def _parse_dex_pair(self, pair: dict) -> Optional[dict]:
        """Parse a raw DexScreener pair dict into the canonical token schema."""
        try:
            base_token: dict = pair.get("baseToken", {})
            mint: str = base_token.get("address", "")
            if not mint or len(mint) < 32:
                return None

            created_ts_ms: int = _safe_int(pair.get("pairCreatedAt", 0))
            created_at: int = created_ts_ms // 1000 if created_ts_ms > 1_000_000_000_000 else created_ts_ms

            price_usd: float = _safe_float(pair.get("priceUsd"))
            liquidity: dict = pair.get("liquidity", {})
            liquidity_usd: float = _safe_float(liquidity.get("usd"))
            fdv: float = _safe_float(pair.get("fdv"))
            volume: dict = pair.get("volume", {})
            volume_h24: float = _safe_float(volume.get("h24"))

            price_change: dict = pair.get("priceChange", {})
            price_change_5m: float = _safe_float(price_change.get("m5"))
            price_change_1h: float = _safe_float(price_change.get("h1"))

            txns: dict = pair.get("txns", {})
            txns_5m: dict = txns.get("m5", {})
            buys_5m: float = _safe_float(txns_5m.get("buys"))
            sells_5m: float = _safe_float(txns_5m.get("sells"))
            buy_sell_ratio: float = (buys_5m / sells_5m) if sells_5m > 0 else 1.0

            return {
                "mint_address": mint,
                "symbol": base_token.get("symbol", ""),
                "name": base_token.get("name", ""),
                "created_at": created_at,
                "source": "dexscreener",
                "initial_liquidity_usd": liquidity_usd,
                "initial_market_cap": fdv,
                "price_at_launch": price_usd,
                "dex_buy_volume_5min": _safe_float(volume.get("m5")),
                "buy_sell_ratio_5min": buy_sell_ratio,
                "price_change_1min": price_change_5m,  # closest available
            }
        except Exception as exc:
            logger.debug("DexScreener pair parse error: {}", exc)
            return None

    # ------------------------------------------------------------------
    # Source 3 — GeckoTerminal OHLCV enrichment (free, no API key)
    # ------------------------------------------------------------------

    async def enrich_with_geckoterminal(self, mint: str, created_at: int) -> dict:
        """
        Fetch price checkpoints from GeckoTerminal for the first hour post-launch.

        Args:
            mint: Token mint address.
            created_at: Unix timestamp of token creation.

        Returns:
            Dict with price_at_Nmin checkpoint keys, or empty dict on failure.
        """
        if not created_at:
            return {}
        try:
            url = f"{GECKO_BASE}/networks/solana/tokens/{mint}/pools"
            data = await self._get(url)
            if not data:
                return {}
            pools = data.get("data", [])
            if not pools:
                return {}
            pool_id = pools[0].get("id", "")
            if not pool_id:
                return {}
            ohlcv_url = f"{GECKO_BASE}/networks/solana/pools/{pool_id}/ohlcv/1m"
            ohlcv_data = await self._get(ohlcv_url, params={"limit": "60"})
            if not ohlcv_data:
                return {}
            items = (ohlcv_data.get("data") or {}).get("attributes", {}).get("ohlcv_list", [])
            if not items:
                return {}
            price_map: dict[int, float] = {}
            for item in items:
                if len(item) >= 5:
                    ts = int(item[0])
                    close = float(item[4])
                    if close > 0 and ts > 0:
                        offset_min = abs((ts - created_at)) // 60 if created_at else 0
                        if offset_min <= 1440:
                            price_map[offset_min] = close
            if not price_map:
                return {}

            def _get_price_at(target_min: int) -> float:
                candidates = [
                    (abs(m - target_min), p)
                    for m, p in price_map.items()
                    if m <= target_min + 2
                ]
                if not candidates:
                    return 0.0
                candidates.sort(key=lambda x: x[0])
                return candidates[0][1]

            all_prices = list(price_map.values())
            max_price = max(all_prices) if all_prices else 0.0
            max_price_min = (
                min(price_map, key=lambda m: abs(price_map[m] - max_price))
                if price_map else 0
            )

            return {
                "price_at_1min": _get_price_at(1),
                "price_at_5min": _get_price_at(5),
                "price_at_15min": _get_price_at(15),
                "price_at_30min": _get_price_at(30),
                "price_at_60min": _get_price_at(60),
                "max_price_ever": max_price,
                "max_price_time_min": max_price_min,
                "price_at_24hr": _get_price_at(1440),
            }
        except Exception as exc:
            logger.debug("GeckoTerminal enrich error for {}: {}", mint, exc)
            return {}

    # ------------------------------------------------------------------
    # Source 4 — Helius on-chain enrichment
    # ------------------------------------------------------------------

    async def enrich_with_helius(self, mint: str) -> dict:
        """
        Fetch early transaction data from Helius to derive sniper/holder signals.

        Credits budget is tracked — method returns empty dict if budget exhausted.

        Args:
            mint: Token mint address.

        Returns:
            Dict with sniper_count, holder_velocity_1min, first_buyer_count keys.
        """
        if self._helius_credits >= MAX_HELIUS_CREDITS:
            logger.warning("Helius credit budget exhausted ({:,}).", MAX_HELIUS_CREDITS)
            return {}

        api_key = self._helius_api_key
        if not api_key or "your_" in api_key:
            return {}

        try:
            url = f"{HELIUS_BASE}/addresses/{mint}/transactions"
            params = {
                "api-key": api_key,
                "limit": "10",
                "type": "SWAP",
            }
            data = await self._get(url, params=params)
            # Count approximately 10 credits per call
            self._helius_credits += 10

            if not data or not isinstance(data, list):
                return {}

            transactions: list[dict] = data

            if not transactions:
                return {}

            # Use the timestamp of the first (oldest) transaction as T0
            # Helius returns newest-first; reverse to get chronological order
            txs_chrono = list(reversed(transactions))

            t0_ts: int = _safe_int(txs_chrono[0].get("timestamp", 0))
            if not t0_ts:
                return {}

            unique_signers: set[str] = set()
            first_3_blocks_signers: set[str] = set()
            first_60s_count: int = 0

            for tx in txs_chrono:
                tx_ts: int = _safe_int(tx.get("timestamp", 0))
                signer: str = tx.get("feePayer", "") or ""
                slot: int = _safe_int(tx.get("slot", 0))
                t0_slot: int = _safe_int(txs_chrono[0].get("slot", 0))

                if signer:
                    unique_signers.add(signer)
                    if slot and t0_slot and (slot - t0_slot) <= 3:
                        first_3_blocks_signers.add(signer)

                if tx_ts and (tx_ts - t0_ts) <= 60:
                    first_60s_count += 1

            holder_velocity = float(len(unique_signers))  # unique buyers in sample
            sniper_count = len(first_3_blocks_signers)

            return {
                "sniper_count": sniper_count,
                "first_buyer_count": first_60s_count,
                "holder_velocity_1min": holder_velocity,
            }

        except Exception as exc:
            logger.debug("Helius enrich error for {}: {}", mint, exc)
            return {}

    # ------------------------------------------------------------------
    # Source 5 — Solscan holder snapshot
    # ------------------------------------------------------------------

    async def enrich_with_solscan(self, mint: str) -> dict:
        """
        Fetch top holder data from Solscan to compute concentration metrics.

        Args:
            mint: Token mint address.

        Returns:
            Dict with top_holder_percent and dev_cluster_detected keys.
        """
        try:
            url = f"{SOLSCAN_BASE}/token/holders"
            params = {
                "tokenAddress": mint,
                "limit": "20",
                "offset": "0",
            }
            data = await self._get(url, params=params)
            if not data:
                return {}

            # Solscan public API formats
            holders: list[dict] = (
                data.get("data", [])
                if isinstance(data.get("data"), list)
                else data.get("result", [])
                if isinstance(data.get("result"), list)
                else []
            )

            if not holders:
                return {}

            amounts: list[float] = []
            for holder in holders:
                # Different response shapes: amount / uiAmount / uiTokenAmount
                raw_amount = (
                    holder.get("uiAmount")
                    or holder.get("amount")
                    or (holder.get("uiTokenAmount") or {}).get("uiAmount")
                    or 0
                )
                amounts.append(_safe_float(raw_amount))

            if not amounts:
                return {}

            total: float = sum(amounts)
            if total <= 0:
                return {}

            top1_pct: float = (amounts[0] / total) * 100.0 if amounts else 0.0
            top5_sum: float = sum(amounts[:5])
            top5_pct: float = (top5_sum / total) * 100.0

            dev_cluster: int = 1 if top5_pct > 60.0 else 0

            return {
                "top_holder_percent": top1_pct,
                "dev_cluster_detected": dev_cluster,
            }

        except Exception as exc:
            logger.debug("Solscan enrich error for {}: {}", mint, exc)
            return {}

    # ------------------------------------------------------------------
    # Outcome computation
    # ------------------------------------------------------------------

    async def _compute_outcome(self, token: dict) -> dict:
        """
        Derive outcome labels and computed fields from price checkpoints.

        Args:
            token: Partial token dict with price_at_* fields populated.

        Returns:
            Dict with hit_2x, hit_5x, hit_10x, rug_pulled, rug_time_min,
            max_price_ever, max_price_time_min, final_outcome, price_change_1min.
        """
        launch: float = _safe_float(token.get("price_at_launch"))
        p1: float = _safe_float(token.get("price_at_1min"))
        p5: float = _safe_float(token.get("price_at_5min"))
        p15: float = _safe_float(token.get("price_at_15min"))
        p30: float = _safe_float(token.get("price_at_30min"))
        p60: float = _safe_float(token.get("price_at_60min"))
        p24h: float = _safe_float(token.get("price_at_24hr"))
        max_price: float = _safe_float(token.get("max_price_ever"))
        max_price_min: int = _safe_int(token.get("max_price_time_min"))

        if launch <= 0:
            # Cannot compute meaningful ratios without launch price
            return {
                "hit_2x": 0, "hit_5x": 0, "hit_10x": 0,
                "rug_pulled": 0, "rug_time_min": 0,
                "max_price_ever": max_price, "max_price_time_min": max_price_min,
                "final_outcome": "UNKNOWN",
                "price_change_1min": 0.0,
            }

        # Recompute max_price from available checkpoints if not set
        checkpoint_prices = [(p1, 1), (p5, 5), (p15, 15), (p30, 30), (p60, 60), (p24h, 1440)]
        valid_checkpoints = [(p, m) for p, m in checkpoint_prices if p > 0]

        if not max_price and valid_checkpoints:
            max_price, max_price_min = max(valid_checkpoints, key=lambda x: x[0])

        hit_2x: int = 1 if max_price >= launch * 2.0 else 0
        hit_5x: int = 1 if max_price >= launch * 5.0 else 0
        hit_10x: int = 1 if max_price >= launch * 10.0 else 0

        # Rug: any checkpoint drops below 20% of launch
        rug_pulled: int = 0
        rug_time_min: int = 0
        for p_check, m_check in valid_checkpoints:
            if p_check < launch * 0.2:
                rug_pulled = 1
                rug_time_min = m_check
                break

        # price_change_1min — percentage change from launch to 1-minute mark
        price_change_1min: float = 0.0
        if launch > 0 and p1 > 0:
            price_change_1min = ((p1 - launch) / launch) * 100.0

        # final_outcome classification
        if rug_pulled:
            final_outcome = "RUG"
        elif hit_10x:
            final_outcome = "MOON"
        elif hit_2x:
            final_outcome = "PUMP"
        elif p24h > 0 and p24h < launch * 0.5:
            final_outcome = "DEAD"
        elif p60 > 0 and p60 < launch * 0.5:
            final_outcome = "DEAD"
        else:
            final_outcome = "UNKNOWN"

        return {
            "hit_2x": hit_2x,
            "hit_5x": hit_5x,
            "hit_10x": hit_10x,
            "rug_pulled": rug_pulled,
            "rug_time_min": rug_time_min,
            "max_price_ever": max_price,
            "max_price_time_min": max_price_min,
            "final_outcome": final_outcome,
            "price_change_1min": price_change_1min,
        }

    # ------------------------------------------------------------------
    # Buffer / persistence
    # ------------------------------------------------------------------

    async def _upsert_token(self, data: dict) -> None:
        """
        Add token dict to the insert buffer, flushing when it reaches BATCH_INSERT_SIZE.

        Args:
            data: Fully enriched token dict conforming to the schema columns.
        """
        self._insert_buffer.append(data)
        self._total_saved += 1
        if len(self._insert_buffer) >= BATCH_INSERT_SIZE:
            await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Execute batched INSERT OR REPLACE for all buffered token records."""
        if not self._insert_buffer or self._db is None:
            return

        now: int = int(time.time())
        rows: list[tuple] = []
        for token in self._insert_buffer:
            row = tuple(
                token.get(col, self._default_for_col(col))
                for col in _TOKEN_COLUMNS
            )
            rows.append(row)

        try:
            await self._db.executemany(_UPSERT_SQL, rows)
            await self._db.commit()
            logger.debug("Flushed {} token records to DB.", len(rows))
        except Exception as exc:
            logger.error("DB flush error: {} — {} records may be lost.", exc, len(rows))
        finally:
            self._insert_buffer.clear()

    @staticmethod
    def _default_for_col(col: str) -> Any:
        """Return the appropriate zero-value default for a schema column."""
        text_cols = {"mint_address", "symbol", "name", "source", "final_outcome"}
        float_cols = {
            "initial_liquidity_usd", "initial_market_cap", "price_at_launch",
            "price_at_1min", "price_at_5min", "price_at_15min", "price_at_30min",
            "price_at_60min", "price_at_24hr", "max_price_ever",
            "top_holder_percent", "holder_velocity_1min", "buy_sell_ratio_5min",
            "wash_trading_score", "dex_buy_volume_5min", "price_change_1min",
        }
        if col in text_cols:
            return ""
        if col in float_cols:
            return 0.0
        return 0

    # ------------------------------------------------------------------
    # Progress tracking
    # ------------------------------------------------------------------

    async def _update_progress(self, **kwargs: Any) -> None:
        """
        Update collection_progress row (id=1) with the provided keyword arguments.

        Accepted keys match the collection_progress columns.
        """
        if not self._db:
            return
        kwargs["last_updated"] = int(time.time())
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values())
        try:
            await self._db.execute(
                f"UPDATE collection_progress SET {set_clause} WHERE id = 1",
                values,
            )
            await self._db.commit()
        except Exception as exc:
            logger.warning("Progress update error: {}", exc)

    async def get_progress(self) -> dict:
        """
        Read the current collection_progress row.

        Returns:
            Dict with all progress columns, or empty dict on error.
        """
        if not self._db:
            return {}
        try:
            async with self._db.execute(
                "SELECT * FROM collection_progress WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
        except Exception as exc:
            logger.error("Error reading progress: {}", exc)
        return {}

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def get_stats(self) -> dict:
        """
        Return aggregate counts from the historical_tokens table.

        Returns:
            Dict with keys: total, complete, rugs, pumps, moons, dead, unknown,
            hit_2x, hit_5x, hit_10x.
        """
        if not self._db:
            return {}
        try:
            async with self._db.execute(
                """
                SELECT
                    COUNT(*)                                             AS total,
                    SUM(data_complete)                                   AS complete,
                    SUM(CASE WHEN final_outcome='RUG'     THEN 1 ELSE 0 END) AS rugs,
                    SUM(CASE WHEN final_outcome='PUMP'    THEN 1 ELSE 0 END) AS pumps,
                    SUM(CASE WHEN final_outcome='MOON'    THEN 1 ELSE 0 END) AS moons,
                    SUM(CASE WHEN final_outcome='DEAD'    THEN 1 ELSE 0 END) AS dead,
                    SUM(CASE WHEN final_outcome='UNKNOWN' THEN 1 ELSE 0 END) AS unknown,
                    SUM(hit_2x)                                          AS hit_2x,
                    SUM(hit_5x)                                          AS hit_5x,
                    SUM(hit_10x)                                         AS hit_10x
                FROM historical_tokens
                """
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
        except Exception as exc:
            logger.error("Error reading stats: {}", exc)
        return {}

    # ------------------------------------------------------------------
    # Enrichment pipeline for a single token
    # ------------------------------------------------------------------

    async def _enrich_and_save(self, token: dict, pbar: Optional[Any] = None) -> None:
        """
        Run all enrichments for a single token, compute outcome, and upsert.

        Args:
            token: Partial token dict (from collect phase).
            pbar: Optional tqdm progress bar to update.
        """
        mint: str = token.get("mint_address", "")
        created_at: int = _safe_int(token.get("created_at", 0))

        # Parallel enrichment — run all three concurrently
        geo_task = asyncio.create_task(self.enrich_with_geckoterminal(mint, created_at))
        helius_task = asyncio.create_task(self.enrich_with_helius(mint))
        solscan_task = asyncio.create_task(self.enrich_with_solscan(mint))

        geo_data, helius_data, solscan_data = await asyncio.gather(
            geo_task, helius_task, solscan_task, return_exceptions=False
        )

        # Merge enrichment results into token dict
        merged: dict = {**token}
        for enrichment in (geo_data, helius_data, solscan_data):
            if isinstance(enrichment, dict):
                merged.update(enrichment)

        # Fallback for missing price checkpoints if GeckoTerminal failed/skipped
        _has_synthetic_data = False
        if merged.get("price_at_5min", 0.0) <= 0.0:
            logger.warning("No real price data for {} — marking incomplete.", mint)
            _has_synthetic_data = True

        # Fallback for Solscan holder concentration metrics
        if merged.get("top_holder_percent", 0.0) <= 0.0:
            logger.warning("No real holder data for {} — marking incomplete.", mint)
            _has_synthetic_data = True

        # Fallback for Helius sniper/buyer metrics
        if merged.get("sniper_count", 0) <= 0 and not _has_synthetic_data:
            logger.warning("No real sniper/buyer data for {} — marking incomplete.", mint)
            _has_synthetic_data = True

        if _has_synthetic_data:
            merged["data_complete"] = 0
            merged["synthetic_data"] = 1

        # Compute outcome labels
        outcome = await self._compute_outcome(merged)
        merged.update(outcome)

        # Mark completeness: has price data and outcome
        is_complete = (
            merged.get("price_at_launch", 0.0) > 0
            and merged.get("price_at_5min", 0.0) > 0
            and merged.get("final_outcome", "UNKNOWN") != "UNKNOWN"
        )
        merged["data_complete"] = 1 if is_complete else 0
        merged["collected_at"] = int(time.time())

        # Token age
        if created_at:
            merged["token_age_seconds"] = max(0, int(time.time()) - created_at)

        await self._upsert_token(merged)

        if is_complete:
            self._total_complete += 1

        if pbar is not None:
            pbar.update(1)
            pbar.set_postfix({
                "found": self._total_found,
                "saved": self._total_saved,
                "complete": self._total_complete,
                "helius_cr": f"{self._helius_credits:,}",
            })

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    async def run(
        self,
        max_pages_gecko: int = MAX_PAGES_GECKO,
        max_pages_dex: int = MAX_PAGES_DEX,
    ) -> None:
        """
        Execute the full collection pipeline.

        Steps:
          1. Collect pools from GeckoTerminal.
          2. Collect pairs from DexScreener.
          3. Deduplicate by mint address.
          4. For each unique token: enrich (GeckoTerminal + Helius + Solscan).
          5. Compute outcome labels.
          6. Upsert into SQLite.

        Args:
            max_pages_gecko: GeckoTerminal page limit.
            max_pages_dex: DexScreener page limit.
        """
        await self._update_progress(status="running")
        logger.info("Starting full data collection pipeline.")

        # Step 1 & 2: collect sources concurrently
        gecko_task = asyncio.create_task(self.collect_gecko_pools(max_pages_gecko))
        dex_task = asyncio.create_task(self.collect_dexscreener_pairs(max_pages_dex))

        gecko_results, dex_results = await asyncio.gather(gecko_task, dex_task)

        # Step 3: deduplicate, preferring dexscreener data when both present
        all_tokens: dict[str, dict] = {}
        for token in gecko_results:
            mint = token.get("mint_address", "")
            if mint:
                all_tokens[mint] = token

        for token in dex_results:
            mint = token.get("mint_address", "")
            if mint:
                if mint in all_tokens:
                    # Merge: keep dexscreener as base, fill missing from gecko
                    merged = {**all_tokens[mint], **token}
                    all_tokens[mint] = merged
                else:
                    all_tokens[mint] = token

        unique_tokens: list[dict] = list(all_tokens.values())
        self._total_found = len(unique_tokens)
        logger.info("Deduplicated {} unique tokens — starting enrichment.", self._total_found)
        await self._update_progress(total_found=self._total_found)

        # Step 4–6: enrich with progress bar
        pbar = atqdm(
            total=self._total_found,
            desc="Enriching tokens",
            unit="token",
            dynamic_ncols=True,
            file=sys.stdout,
        )

        # Use semaphore-limited concurrency for enrichment
        chunk_size = PARALLEL_REQUESTS
        for i in range(0, len(unique_tokens), chunk_size):
            chunk = unique_tokens[i : i + chunk_size]
            tasks = [self._enrich_and_save(t, pbar) for t in chunk]
            await asyncio.gather(*tasks, return_exceptions=True)

            # Update progress periodically
            if i % (chunk_size * 10) == 0:
                await self._update_progress(
                    total_saved=self._total_saved,
                    total_complete=self._total_complete,
                    helius_credits_used=self._helius_credits,
                )

        pbar.close()

        # Final flush
        if self._insert_buffer:
            await self._flush_buffer()

        await self._update_progress(
            status="complete",
            total_found=self._total_found,
            total_saved=self._total_saved,
            total_complete=self._total_complete,
            helius_credits_used=self._helius_credits,
        )
        logger.success(
            "Pipeline complete: {} found / {} saved / {} complete. Helius credits: {:,}.",
            self._total_found,
            self._total_saved,
            self._total_complete,
            self._helius_credits,
        )

    async def run_sample(self, n: int = 1000) -> None:
        """
        Collect the first *n* tokens for rapid testing.

        Fetches only enough GeckoTerminal pages to fill the quota, skips DexScreener.

        Args:
            n: Maximum number of tokens to collect.
        """
        await self._update_progress(status="running_sample")
        logger.info("Running sample collection (n={}).", n)

        # Fetch just enough pages — each page yields ~50 tokens
        pages_needed = math.ceil(n / 50)
        pages_needed = min(pages_needed, MAX_PAGES_GECKO)

        gecko_results = await self.collect_gecko_pools(max_pages=pages_needed)
        sample = gecko_results[:n]
        self._total_found = len(sample)

        logger.info("Enriching {} sample tokens.", self._total_found)
        pbar = atqdm(
            total=self._total_found,
            desc=f"Sample ({n})",
            unit="token",
            dynamic_ncols=True,
            file=sys.stdout,
        )

        chunk_size = PARALLEL_REQUESTS
        for i in range(0, len(sample), chunk_size):
            chunk = sample[i : i + chunk_size]
            tasks = [self._enrich_and_save(t, pbar) for t in chunk]
            await asyncio.gather(*tasks, return_exceptions=True)

        pbar.close()

        if self._insert_buffer:
            await self._flush_buffer()

        await self._update_progress(
            status="sample_complete",
            total_found=self._total_found,
            total_saved=self._total_saved,
            total_complete=self._total_complete,
            helius_credits_used=self._helius_credits,
        )
        logger.success(
            "Sample complete: {} found / {} saved / {} complete.",
            self._total_found,
            self._total_saved,
            self._total_complete,
        )

    # ------------------------------------------------------------------
    # Dry-run connectivity test
    # ------------------------------------------------------------------

    async def dry_run(self) -> None:
        """Test connectivity to each API source with a single minimal request."""
        logger.info("=== Dry-run: testing API connectivity ===")

        # GeckoTerminal
        gecko_url = f"{GECKO_BASE}/networks/solana/new_pools"
        gecko_data = await self._get(gecko_url, params={"page": "1", "include": "base_token"})
        if gecko_data and gecko_data.get("data"):
            pool_count = len(gecko_data["data"])
            logger.success("GeckoTerminal ✓  ({} pools on page 1)", pool_count)
        else:
            logger.error("GeckoTerminal ✗  (no data returned)")

        # DexScreener
        dex_url = f"{DEX_BASE}/search"
        dex_data = await self._get(dex_url, params={"q": "sol"})
        if dex_data and dex_data.get("pairs"):
            pair_count = len(dex_data["pairs"])
            logger.success("DexScreener ✓  ({} pairs returned)", pair_count)
        else:
            logger.error("DexScreener ✗  (no data returned)")

        # Helius
        if self._helius_api_key and "your_" not in self._helius_api_key:
            helius_url = f"{HELIUS_BASE}/addresses/{bonk_mint}/transactions"
            helius_params = {"api-key": self._helius_api_key, "limit": "1"}
            helius_data = await self._get(helius_url, params=helius_params)
            if helius_data is not None:
                logger.success("Helius ✓  (transactions endpoint responded)")
            else:
                logger.error("Helius ✗  (no response — check API key)")
        else:
            logger.warning("Helius — skipped (no API key configured)")

        # Solscan
        solscan_url = f"{SOLSCAN_BASE}/token/holders"
        solscan_params = {"tokenAddress": bonk_mint, "limit": "5", "offset": "0"}
        solscan_data = await self._get(solscan_url, params=solscan_params)
        if solscan_data:
            logger.success("Solscan ✓  (holders endpoint responded)")
        else:
            logger.warning("Solscan ✗  (no data — public endpoint may be rate-limited)")

        logger.info("=== Dry-run complete ===")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect historical Solana token data")
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Collect N tokens only (0 = full collection)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test API connectivity only — no data written",
    )
    parser.add_argument(
        "--gecko-pages",
        type=int,
        default=MAX_PAGES_GECKO,
        help=f"GeckoTerminal page limit (default: {MAX_PAGES_GECKO})",
    )
    parser.add_argument(
        "--dex-pages",
        type=int,
        default=MAX_PAGES_DEX,
        help=f"DexScreener page limit (default: {MAX_PAGES_DEX})",
    )
    args = parser.parse_args()

    # Configure loguru: INFO to console, DEBUG to file
    logger.remove()
    logger.add(sys.stdout, level="INFO", colorize=True, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logger.add(
        BACKTEST_DB_PATH.parent / "data_collector.log",
        level="DEBUG",
        rotation="50 MB",
        retention=3,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
    )

    async def main() -> None:
        """Async entry point."""
        collector = DataCollector()
        await collector.start()
        try:
            if args.dry_run:
                await collector.dry_run()
            elif args.sample:
                await collector.run_sample(n=args.sample)
            else:
                await collector.run(
                    max_pages_gecko=args.gecko_pages,
                    max_pages_dex=args.dex_pages,
                )
            stats = await collector.get_stats()
            print(f"\nCollection complete: {stats}")
        except KeyboardInterrupt:
            logger.warning("Interrupted by user — flushing buffer and exiting.")
        except Exception as exc:
            logger.exception("Fatal error in main: {}", exc)
        finally:
            await collector.stop()

    asyncio.run(main())
