import aiosqlite
import json
from typing import Dict, Any, List, Optional
from loguru import logger
from config.settings import settings
from database.models import (
    CREATE_TOKENS_TABLE,
    CREATE_SIGNALS_TABLE,
    CREATE_SCANS_TABLE,
    CREATE_SIGNALS_INDEX,
    CREATE_SCANS_INDEX,
    ALTER_SIGNALS_TABLE_ADD_COMPOSITE,
    ALTER_SIGNALS_TABLE_ADD_DEX_SOURCE,
    # Enhancement tables
    CREATE_WALLET_ANALYSES_TABLE,
    CREATE_WALLET_ANALYSES_INDEX,
    CREATE_SOCIAL_SIGNALS_TABLE,
    CREATE_SOCIAL_SIGNALS_INDEX,
    CREATE_RUG_ANALYSES_TABLE,
    CREATE_RUG_ANALYSES_INDEX,
    # Phase 1 tables
    CREATE_DETECTED_POOLS_TABLE,
    CREATE_DETECTED_POOLS_INDEX,
    CREATE_HOLDER_VELOCITY_TABLE,
    CREATE_HOLDER_VELOCITY_INDEX,
    CREATE_FIRST_BUYER_TABLE,
    CREATE_FIRST_BUYER_INDEX,
    # Phase 2 tables
    CREATE_TX_PATTERN_TABLE,
    CREATE_TX_PATTERN_INDEX,
    CREATE_LIQUIDITY_GROWTH_TABLE,
    CREATE_LIQUIDITY_GROWTH_INDEX,
    CREATE_CROSS_DEX_TABLE,
    CREATE_CROSS_DEX_INDEX,
    # Phase 3 tables
    CREATE_CIRCUIT_BREAKER_TABLE,
)

# Module-level singleton connection — initialized once in init_db()
_db_connection: Optional[aiosqlite.Connection] = None


async def _get_db() -> aiosqlite.Connection:
    """Return the shared database connection, raising if not yet initialised."""
    global _db_connection  # FIX BUG-16
    if _db_connection is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    try:
        await _db_connection.execute("SELECT 1")
    except Exception:
        logger.warning("DB connection lost, reconnecting...")
        _db_connection = await aiosqlite.connect(settings.SQLITE_DB_PATH)
        _db_connection.row_factory = aiosqlite.Row
        await _db_connection.execute("PRAGMA journal_mode = WAL;")
    return _db_connection  # FIX BUG-16


async def init_db() -> None:
    """Initialize SQLite database, create tables, and apply indexes.
    Opens the singleton connection that will be reused for the lifetime of the process.
    """
    global _db_connection
    logger.info(f"Initializing database at: {settings.SQLITE_DB_PATH}")
    _db_connection = await aiosqlite.connect(settings.SQLITE_DB_PATH)
    _db_connection.row_factory = aiosqlite.Row
    await _db_connection.execute("PRAGMA foreign_keys = ON;")
    await _db_connection.execute("PRAGMA journal_mode = WAL;")
    await _db_connection.execute(CREATE_TOKENS_TABLE)
    await _db_connection.execute(CREATE_SIGNALS_TABLE)
    # Migrate existing databases: add columns that may not exist
    try:
        await _db_connection.execute(ALTER_SIGNALS_TABLE_ADD_COMPOSITE)
    except Exception:
        pass  # column already exists
    try:
        await _db_connection.execute(ALTER_SIGNALS_TABLE_ADD_DEX_SOURCE)
    except Exception:
        pass  # column already exists
    await _db_connection.execute(CREATE_SCANS_TABLE)
    # Enhancement tables
    await _db_connection.execute(CREATE_WALLET_ANALYSES_TABLE)
    await _db_connection.execute(CREATE_SOCIAL_SIGNALS_TABLE)
    await _db_connection.execute(CREATE_RUG_ANALYSES_TABLE)
    # Phase 1 tables
    await _db_connection.execute(CREATE_DETECTED_POOLS_TABLE)
    await _db_connection.execute(CREATE_HOLDER_VELOCITY_TABLE)
    await _db_connection.execute(CREATE_FIRST_BUYER_TABLE)
    # Phase 2 tables
    await _db_connection.execute(CREATE_TX_PATTERN_TABLE)
    await _db_connection.execute(CREATE_LIQUIDITY_GROWTH_TABLE)
    await _db_connection.execute(CREATE_CROSS_DEX_TABLE)
    # Phase 3 tables (auto-trading)
    await _db_connection.execute(CREATE_CIRCUIT_BREAKER_TABLE)
    # Apply indexes (idempotent — IF NOT EXISTS)
    await _db_connection.execute(CREATE_SIGNALS_INDEX)
    await _db_connection.execute(CREATE_SCANS_INDEX)
    await _db_connection.execute(CREATE_WALLET_ANALYSES_INDEX)
    await _db_connection.execute(CREATE_SOCIAL_SIGNALS_INDEX)
    await _db_connection.execute(CREATE_RUG_ANALYSES_INDEX)
    await _db_connection.execute(CREATE_DETECTED_POOLS_INDEX)
    await _db_connection.execute(CREATE_HOLDER_VELOCITY_INDEX)
    await _db_connection.execute(CREATE_FIRST_BUYER_INDEX)
    await _db_connection.execute(CREATE_TX_PATTERN_INDEX)
    await _db_connection.execute(CREATE_LIQUIDITY_GROWTH_INDEX)
    await _db_connection.execute(CREATE_CROSS_DEX_INDEX)
    await _db_connection.commit()
    logger.info("Database tables and indexes initialized successfully.")

    # --- Wire Phase 3 singletons to the shared connection ---
    from trading.portfolio_tracker import portfolio_tracker
    from trading.circuit_breaker   import circuit_breaker
    portfolio_tracker.set_db_connection(_db_connection)
    await portfolio_tracker.ensure_tables()
    circuit_breaker.set_db_connection(_db_connection)
    await circuit_breaker.ensure_table()
    await circuit_breaker.load_persisted_state()
    logger.info("Phase 3 trading modules wired to DB connection.")


async def save_token(token_data: Dict[str, Any]) -> bool:
    """Save or update a token in the database. Returns True on success."""
    try:
        db = await _get_db()
        await db.execute(
            """
            INSERT OR REPLACE INTO tokens (
                address, symbol, name, liquidity_usd, market_cap,
                is_honeypot, goplus_score, liquidity_locked, top10_holders_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token_data.get("address", ""),
                token_data.get("symbol", ""),
                token_data.get("name", ""),
                token_data.get("liquidity_usd"),
                token_data.get("market_cap"),
                1 if token_data.get("is_honeypot") else 0,
                token_data.get("goplus_score"),
                1 if token_data.get("liquidity_locked") else 0,
                token_data.get("top10_holders_pct"),
            ),
        )
        await db.commit()
        logger.debug(f"Saved token to DB: {token_data.get('address', 'unknown')}")
        return True
    except Exception as exc:
        logger.error(f"Error saving token: {exc}")
        return False

async def save_signal(signal_data: Dict[str, Any]) -> bool:
    """Save a generated signal to the database. Returns True on success."""
    try:
        db = await _get_db()
        await db.execute(
            """
            INSERT INTO signals (
                token_address, signal_type, price_at_signal,
                confidence_score, composite_score, dex_source, result
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_data.get("token_address", ""),
                signal_data.get("signal_type", ""),
                signal_data.get("price_at_signal", 0.0),
                signal_data.get("confidence_score", 0.0),
                signal_data.get("composite_score", 0.0),
                signal_data.get("dex_source", "Raydium_AMM"),
                signal_data.get("result"),
            ),
        )
        await db.commit()
        logger.debug(f"Saved signal for token {signal_data.get('token_address', 'unknown')}")
        return True
    except Exception as exc:
        logger.error(f"Error saving signal: {exc}")
        return False


async def save_scan(scan_data: Dict[str, Any]) -> bool:
    """Save a security scan result to the database. Returns True on success."""
    try:
        db = await _get_db()
        await db.execute(
            """
            INSERT INTO scans (
                token_address, passed, fail_reason
            ) VALUES (?, ?, ?)
            """,
            (
                scan_data.get("token_address", ""),
                1 if scan_data.get("passed") else 0,
                scan_data.get("fail_reason"),
            ),
        )
        await db.commit()
        logger.debug(f"Saved security scan for token {scan_data.get('token_address', 'unknown')}")
        return True
    except Exception as exc:
        logger.error(f"Error saving scan: {exc}")
        return False


async def get_token(address: str) -> Optional[Dict[str, Any]]:
    """Retrieve token details by address."""
    db = await _get_db()
    async with db.execute(
        "SELECT * FROM tokens WHERE address = ?", (address,)
    ) as cursor:
        row = await cursor.fetchone()
        if row:
            res = dict(row)
            res["is_honeypot"] = bool(res["is_honeypot"])
            res["liquidity_locked"] = bool(res["liquidity_locked"])
            return res
        return None


async def token_exists(address: str) -> bool:
    """Check if a token already exists in the database."""
    db = await _get_db()
    async with db.execute(
        "SELECT 1 FROM tokens WHERE address = ? LIMIT 1", (address,)
    ) as cursor:
        row = await cursor.fetchone()
        return row is not None


async def get_recent_signals(limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve the most recent signals, including basic token details."""
    db = await _get_db()
    async with db.execute(
        """
        SELECT s.*, t.symbol, t.name
        FROM signals s
        JOIN tokens t ON s.token_address = t.address
        ORDER BY s.sent_at DESC
        LIMIT ?
        """,
        (limit,),
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Enhancement save helpers
# ---------------------------------------------------------------------------

async def save_wallet_analysis(data: Dict[str, Any]) -> None:
    """Persist a WalletAnalysis result to the wallet_analyses table."""
    try:
        db = await _get_db()
        await db.execute(
            """
            INSERT INTO wallet_analyses (
                token_address, creator_wallet, wallet_age_days,
                sniper_count, sniper_percentage, wallet_score, risk_level
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                data.get("creator_wallet", ""),
                data.get("wallet_age_days", 0),
                data.get("sniper_count", 0),
                data.get("sniper_percentage", 0.0),
                data.get("wallet_score", 0.0),
                data.get("risk_level", "UNKNOWN"),
            ),
        )
        await db.commit()
        logger.debug(f"Saved wallet_analysis for token {data['token_address']}")
    except Exception as exc:
        logger.error(f"Error saving wallet_analysis: {exc}")


async def save_social_signals(data: Dict[str, Any]) -> None:
    """Persist a SocialSignals result to the social_signals table."""
    try:
        db = await _get_db()
        await db.execute(
            """
            INSERT INTO social_signals (
                token_address, mention_count_1h, mention_velocity,
                sentiment_score, has_viral_tweet, social_score
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                data.get("mention_count_1h", 0),
                data.get("mention_velocity", 0.0),
                data.get("sentiment_score", 50.0),
                1 if data.get("has_viral_tweet") else 0,
                data.get("social_score", 0.0),
            ),
        )
        await db.commit()
        logger.debug(f"Saved social_signals for token {data['token_address']}")
    except Exception as exc:
        logger.error(f"Error saving social_signals: {exc}")


async def save_rug_analysis(data: Dict[str, Any]) -> None:
    """Persist a RugAnalysis result to the rug_analyses table.

    risk_flags is stored as a JSON-encoded list of strings.
    """
    try:
        db = await _get_db()
        risk_flags_json = json.dumps(data.get("risk_flags", []))
        await db.execute(
            """
            INSERT INTO rug_analyses (
                token_address, rug_probability, pattern_score,
                risk_flags, recommendation
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                data.get("rug_probability", 0.0),
                data.get("pattern_score", 100.0),
                risk_flags_json,
                data.get("recommendation", "SAFE"),
            ),
        )
        await db.commit()
        logger.debug(f"Saved rug_analysis for token {data['token_address']}")
    except Exception as exc:
        logger.error(f"Error saving rug_analysis: {exc}")


# ---------------------------------------------------------------------------
# Phase 1 save helpers
# ---------------------------------------------------------------------------

async def save_detected_pool(data: Dict[str, Any]) -> None:
    """Persist a DetectedPool record to the detected_pools table."""
    try:
        db = await _get_db()
        await db.execute(
            """
            INSERT INTO detected_pools (
                mint_address, dex_name, signature, block_time, latency_ms
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                data["mint_address"],
                data.get("dex_name", "Unknown"),
                data.get("signature", ""),
                data.get("block_time", 0),
                data.get("latency_ms", 0.0),
            ),
        )
        await db.commit()
        logger.debug(
            f"Saved detected_pool: mint={data['mint_address'][:12]}... "
            f"dex={data.get('dex_name')} latency={data.get('latency_ms', 0):.0f}ms"
        )
    except Exception as exc:
        logger.error(f"Error saving detected_pool: {exc}")


async def save_holder_velocity_snapshot(data: Dict[str, Any]) -> None:
    """Persist a HolderVelocityResult snapshot to holder_velocity_snapshots."""
    try:
        db = await _get_db()
        await db.execute(
            """
            INSERT INTO holder_velocity_snapshots (
                token_address, holder_count, holders_per_minute,
                velocity_label, is_rug_warning, holder_velocity_score
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                data.get("holder_count", 0),
                data.get("holders_per_minute", 0.0),
                data.get("velocity_label", "STAGNANT"),
                1 if data.get("is_rug_warning") else 0,
                data.get("holder_velocity_score", 0.0),
            ),
        )
        await db.commit()
        logger.debug(f"Saved holder_velocity_snapshot for token {data['token_address']}")
    except Exception as exc:
        logger.error(f"Error saving holder_velocity_snapshot: {exc}")


async def save_first_buyer_analysis(data: Dict[str, Any]) -> None:
    """Persist a FirstBuyerAnalysis result to first_buyer_analyses.

    ``first_buyers`` is JSON-encoded.
    """
    try:
        db = await _get_db()
        first_buyers_json = json.dumps(data.get("first_buyers", []))
        await db.execute(
            """
            INSERT INTO first_buyer_analyses (
                token_address, first_buyers, smart_money_count,
                smart_money_pct, smart_money_score, analyzed_wallets, data_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                first_buyers_json,
                data.get("smart_money_count", 0),
                data.get("smart_money_pct", 0.0),
                data.get("smart_money_score", 0.0),
                data.get("analyzed_wallets", 0),
                data.get("data_source", "rpc"),
            ),
        )
        await db.commit()
        logger.debug(f"Saved first_buyer_analysis for token {data['token_address']}")
    except Exception as exc:
        logger.error(f"Error saving first_buyer_analysis: {exc}")


# ---------------------------------------------------------------------------
# Phase 2 save helpers
# ---------------------------------------------------------------------------

async def save_tx_pattern(data: Dict[str, Any]) -> None:
    """Persist a TxPatternResult to tx_pattern_results."""
    try:
        db = await _get_db()
        await db.execute(
            """
            INSERT INTO tx_pattern_results (
                token_address, buy_count, sell_count, total_txs,
                buy_ratio, wash_trade_count, is_artificial_pump, tx_pattern_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                data.get("buy_count",           0),
                data.get("sell_count",          0),
                data.get("total_txs",           0),
                data.get("buy_ratio",           0.5),
                data.get("wash_trade_count",    0),
                1 if data.get("is_artificial_pump") else 0,
                data.get("tx_pattern_score",    50.0),
            ),
        )
        await db.commit()
        logger.debug(f"Saved tx_pattern for token {data['token_address']}")
    except Exception as exc:
        logger.error(f"Error saving tx_pattern: {exc}")


async def save_liquidity_growth(data: Dict[str, Any]) -> None:
    """Persist a LiquidityGrowthResult to liquidity_growth_results."""
    try:
        db = await _get_db()
        await db.execute(
            """
            INSERT INTO liquidity_growth_results (
                token_address, candle_count, total_volume, first_candle_vol_pct,
                volume_cv, growth_pattern, growth_rate_pct, liquidity_growth_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                data.get("candle_count",            0),
                data.get("total_volume",            0.0),
                data.get("first_candle_vol_pct",    0.0),
                data.get("volume_cv",               0.0),
                data.get("growth_pattern",          "INSUFFICIENT_DATA"),
                data.get("growth_rate_pct",         0.0),
                data.get("liquidity_growth_score",  50.0),
            ),
        )
        await db.commit()
        logger.debug(f"Saved liquidity_growth for token {data['token_address']}")
    except Exception as exc:
        logger.error(f"Error saving liquidity_growth: {exc}")


async def save_cross_dex_result(data: Dict[str, Any]) -> None:
    """Persist a CrossDexResult to cross_dex_results. Prices dict is JSON-encoded."""
    try:
        db = await _get_db()
        prices_json = json.dumps(data.get("prices", {}))
        await db.execute(
            """
            INSERT INTO cross_dex_results (
                token_address, source_count, price_gap_pct,
                gap_label, is_manipulated, cross_dex_score, prices_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                data.get("source_count",    0),
                data.get("price_gap_pct",   0.0),
                data.get("gap_label",       "UNKNOWN"),
                1 if data.get("is_manipulated") else 0,
                data.get("cross_dex_score", 50.0),
                prices_json,
            ),
        )
        await db.commit()
        logger.debug(f"Saved cross_dex_result for token {data['token_address']}")
    except Exception as exc:
        logger.error(f"Error saving cross_dex_result: {exc}")
