import json
from typing import Dict, Any, List, Optional
from loguru import logger
from config.settings import settings
from database.db import db
from database.models import get_ddl


async def _get_db():
    """Return the shared DatabaseClient, raising if not initialised."""
    if not db._connected:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    await db.ensure_connected()
    return db


async def init_db() -> None:
    """Initialize database, create tables, and apply indexes."""
    logger.info("Initializing database…")
    await db.connect()

    ddl = get_ddl(db.is_postgres)

    await db.execute(ddl["tokens"])
    await db.execute(ddl["signals"])
    if not db.is_postgres:
        try:
            await db.execute("ALTER TABLE signals ADD COLUMN composite_score REAL DEFAULT 0.0;")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE signals ADD COLUMN dex_source TEXT DEFAULT 'Raydium_AMM';")
        except Exception:
            pass
    await db.execute(ddl["scans"])
    await db.execute(ddl["wallet_analyses"])
    await db.execute(ddl["social_signals"])
    await db.execute(ddl["rug_analyses"])
    await db.execute(ddl["detected_pools"])
    await db.execute(ddl["holder_velocity"])
    await db.execute(ddl["first_buyer"])
    await db.execute(ddl["tx_pattern"])
    await db.execute(ddl["liquidity_growth"])
    await db.execute(ddl["cross_dex"])
    await db.execute(ddl["circuit_breaker"])

    await db.execute("CREATE INDEX IF NOT EXISTS idx_signals_token_address ON signals (token_address);")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_scans_token_address ON scans (token_address);")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_wallet_analyses_token_address ON wallet_analyses (token_address);")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_social_signals_token_address ON social_signals (token_address);")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rug_analyses_token_address ON rug_analyses (token_address);")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_detected_pools_mint ON detected_pools (mint_address);")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_holder_velocity_token ON holder_velocity_snapshots (token_address);")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_first_buyer_token ON first_buyer_analyses (token_address);")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tx_pattern_token ON tx_pattern_results (token_address);")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_liquidity_growth_token ON liquidity_growth_results (token_address);")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_cross_dex_token ON cross_dex_results (token_address);")
    await db.commit()
    logger.info("Database tables and indexes initialized successfully.")

    # --- Wire Phase 3 singletons to the shared connection ---
    from trading.portfolio_tracker import portfolio_tracker
    from trading.circuit_breaker import circuit_breaker
    portfolio_tracker.set_db(db)
    await portfolio_tracker.ensure_tables()
    circuit_breaker.set_db(db)
    await circuit_breaker.ensure_table()
    await circuit_breaker.load_persisted_state()
    logger.info("Phase 3 trading modules wired to DB.")


async def save_token(token_data: Dict[str, Any]) -> bool:
    """Save or update a token in the database. Returns True on success."""
    try:
        conn = await _get_db()
        sql = conn.insert_or_replace("tokens", [
            "address", "symbol", "name", "liquidity_usd", "market_cap",
            "is_honeypot", "goplus_score", "liquidity_locked", "top10_holders_pct",
        ])
        await conn.execute(sql, (
            token_data.get("address", ""),
            token_data.get("symbol", ""),
            token_data.get("name", ""),
            token_data.get("liquidity_usd"),
            token_data.get("market_cap"),
            1 if token_data.get("is_honeypot") else 0,
            token_data.get("goplus_score"),
            1 if token_data.get("liquidity_locked") else 0,
            token_data.get("top10_holders_pct"),
        ))
        await conn.commit()
        logger.debug(f"Saved token to DB: {token_data.get('address', 'unknown')}")
        return True
    except Exception as exc:
        logger.error(f"Error saving token: {exc}")
        return False


async def save_signal(signal_data: Dict[str, Any]) -> bool:
    """Save a generated signal to the database. Returns True on success."""
    try:
        conn = await _get_db()
        await conn.execute(
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
        await conn.commit()
        logger.debug(f"Saved signal for token {signal_data.get('token_address', 'unknown')}")
        return True
    except Exception as exc:
        logger.error(f"Error saving signal: {exc}")
        return False


async def save_scan(scan_data: Dict[str, Any]) -> bool:
    """Save a security scan result to the database. Returns True on success."""
    try:
        conn = await _get_db()
        await conn.execute(
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
        await conn.commit()
        logger.debug(f"Saved security scan for token {scan_data.get('token_address', 'unknown')}")
        return True
    except Exception as exc:
        logger.error(f"Error saving scan: {exc}")
        return False


async def get_token(address: str) -> Optional[Dict[str, Any]]:
    """Retrieve token details by address."""
    conn = await _get_db()
    row = await conn.fetchone(
        "SELECT * FROM tokens WHERE address = ?", (address,)
    )
    if row:
        row["is_honeypot"] = bool(row["is_honeypot"])
        row["liquidity_locked"] = bool(row["liquidity_locked"])
        return row
    return None


async def token_exists(address: str) -> bool:
    """Check if a token already exists in the database."""
    conn = await _get_db()
    row = await conn.fetchone(
        "SELECT 1 FROM tokens WHERE address = ? LIMIT 1", (address,)
    )
    return row is not None


async def get_recent_signals(limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve the most recent signals, including basic token details."""
    conn = await _get_db()
    return await conn.fetchall(
        """
        SELECT s.*, t.symbol, t.name
        FROM signals s
        JOIN tokens t ON s.token_address = t.address
        ORDER BY s.sent_at DESC
        LIMIT ?
        """,
        (limit,),
    )


# ---------------------------------------------------------------------------
# Enhancement save helpers
# ---------------------------------------------------------------------------

async def save_wallet_analysis(data: Dict[str, Any]) -> None:
    try:
        conn = await _get_db()
        await conn.execute(
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
        await conn.commit()
    except Exception as exc:
        logger.error(f"Error saving wallet_analysis: {exc}")


async def save_social_signals(data: Dict[str, Any]) -> None:
    try:
        conn = await _get_db()
        await conn.execute(
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
        await conn.commit()
    except Exception as exc:
        logger.error(f"Error saving social_signals: {exc}")


async def save_rug_analysis(data: Dict[str, Any]) -> None:
    try:
        conn = await _get_db()
        risk_flags_json = json.dumps(data.get("risk_flags", []))
        await conn.execute(
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
        await conn.commit()
    except Exception as exc:
        logger.error(f"Error saving rug_analysis: {exc}")


# ---------------------------------------------------------------------------
# Phase 1 save helpers
# ---------------------------------------------------------------------------

async def save_detected_pool(data: Dict[str, Any]) -> None:
    try:
        conn = await _get_db()
        await conn.execute(
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
        await conn.commit()
    except Exception as exc:
        logger.error(f"Error saving detected_pool: {exc}")


async def save_holder_velocity_snapshot(data: Dict[str, Any]) -> None:
    try:
        conn = await _get_db()
        await conn.execute(
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
        await conn.commit()
    except Exception as exc:
        logger.error(f"Error saving holder_velocity_snapshot: {exc}")


async def save_first_buyer_analysis(data: Dict[str, Any]) -> None:
    try:
        conn = await _get_db()
        first_buyers_json = json.dumps(data.get("first_buyers", []))
        await conn.execute(
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
        await conn.commit()
    except Exception as exc:
        logger.error(f"Error saving first_buyer_analysis: {exc}")


# ---------------------------------------------------------------------------
# Phase 2 save helpers
# ---------------------------------------------------------------------------

async def save_tx_pattern(data: Dict[str, Any]) -> None:
    try:
        conn = await _get_db()
        await conn.execute(
            """
            INSERT INTO tx_pattern_results (
                token_address, buy_count, sell_count, total_txs,
                buy_ratio, wash_trade_count, is_artificial_pump, tx_pattern_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                data.get("buy_count", 0),
                data.get("sell_count", 0),
                data.get("total_txs", 0),
                data.get("buy_ratio", 0.5),
                data.get("wash_trade_count", 0),
                1 if data.get("is_artificial_pump") else 0,
                data.get("tx_pattern_score", 50.0),
            ),
        )
        await conn.commit()
    except Exception as exc:
        logger.error(f"Error saving tx_pattern: {exc}")


async def save_liquidity_growth(data: Dict[str, Any]) -> None:
    try:
        conn = await _get_db()
        await conn.execute(
            """
            INSERT INTO liquidity_growth_results (
                token_address, candle_count, total_volume, first_candle_vol_pct,
                volume_cv, growth_pattern, growth_rate_pct, liquidity_growth_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                data.get("candle_count", 0),
                data.get("total_volume", 0.0),
                data.get("first_candle_vol_pct", 0.0),
                data.get("volume_cv", 0.0),
                data.get("growth_pattern", "INSUFFICIENT_DATA"),
                data.get("growth_rate_pct", 0.0),
                data.get("liquidity_growth_score", 50.0),
            ),
        )
        await conn.commit()
    except Exception as exc:
        logger.error(f"Error saving liquidity_growth: {exc}")


async def save_cross_dex_result(data: Dict[str, Any]) -> None:
    try:
        conn = await _get_db()
        prices_json = json.dumps(data.get("prices", {}))
        await conn.execute(
            """
            INSERT INTO cross_dex_results (
                token_address, source_count, price_gap_pct,
                gap_label, is_manipulated, cross_dex_score, prices_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["token_address"],
                data.get("source_count", 0),
                data.get("price_gap_pct", 0.0),
                data.get("gap_label", "UNKNOWN"),
                1 if data.get("is_manipulated") else 0,
                data.get("cross_dex_score", 50.0),
                prices_json,
            ),
        )
        await conn.commit()
    except Exception as exc:
        logger.error(f"Error saving cross_dex_result: {exc}")
