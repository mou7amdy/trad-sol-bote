# trading/portfolio_tracker.py
"""
Portfolio tracker — persistent trade ledger + daily P&L.

Tables managed here:
  trades       — one row per open/closed position
  daily_stats  — one row per UTC day

All writes go through the shared aiosqlite connection opened by init_db().
This module ONLY writes to its own tables; it never touches the existing
signal/scan/token tables.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger

from config.settings import settings

# ---------------------------------------------------------------------------
# Table DDL  (CREATE IF NOT EXISTS — safe to run every startup)
# ---------------------------------------------------------------------------

CREATE_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    mint_address        TEXT    NOT NULL,
    symbol              TEXT    NOT NULL DEFAULT '',
    entry_price         REAL    NOT NULL DEFAULT 0.0,
    entry_sol           REAL    NOT NULL DEFAULT 0.0,
    tokens_received     REAL    NOT NULL DEFAULT 0.0,
    current_price       REAL    NOT NULL DEFAULT 0.0,
    current_value_sol   REAL    NOT NULL DEFAULT 0.0,
    realized_pnl_sol    REAL    NOT NULL DEFAULT 0.0,
    unrealized_pnl_sol  REAL    NOT NULL DEFAULT 0.0,
    status              TEXT    NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','closed','emergency_sold')),
    entry_time          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    exit_time           TIMESTAMP,
    exit_reason         TEXT,
    signal_score        REAL    NOT NULL DEFAULT 0.0,
    -- ladder tracking (JSON list of completed rung multipliers)
    tp_rungs_hit        TEXT    NOT NULL DEFAULT '[]',
    highest_price       REAL    NOT NULL DEFAULT 0.0,
    tx_signature_buy    TEXT,
    tx_signature_sell   TEXT
);
"""

CREATE_TRADES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_trades_mint
    ON trades (mint_address);
"""

CREATE_TRADES_STATUS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_trades_status
    ON trades (status);
"""

CREATE_DAILY_STATS_TABLE = """
CREATE TABLE IF NOT EXISTS daily_stats (
    date                TEXT    PRIMARY KEY,   -- YYYY-MM-DD UTC
    trades_total        INTEGER NOT NULL DEFAULT 0,
    trades_won          INTEGER NOT NULL DEFAULT 0,
    trades_lost         INTEGER NOT NULL DEFAULT 0,
    starting_balance_sol REAL   NOT NULL DEFAULT 0.0,
    ending_balance_sol  REAL    NOT NULL DEFAULT 0.0,
    total_pnl_sol       REAL    NOT NULL DEFAULT 0.0,
    win_rate            REAL    NOT NULL DEFAULT 0.0,
    best_trade          REAL    NOT NULL DEFAULT 0.0,
    worst_trade         REAL    NOT NULL DEFAULT 0.0,
    fees_paid_sol       REAL    NOT NULL DEFAULT 0.0
);
"""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    id: int
    mint_address: str
    symbol: str
    entry_price: float
    entry_sol: float
    tokens_received: float
    current_price: float
    current_value_sol: float
    realized_pnl_sol: float
    unrealized_pnl_sol: float
    status: str                      # open | closed | emergency_sold
    entry_time: str
    exit_time: Optional[str]
    exit_reason: Optional[str]
    signal_score: float
    tp_rungs_hit: List[float]        # e.g. [2.0, 5.0]
    highest_price: float
    tx_signature_buy: Optional[str]
    tx_signature_sell: Optional[str]

    @property
    def pnl_pct(self) -> float:
        if self.entry_sol <= 0:
            return 0.0
        total_pnl = self.realized_pnl_sol + self.unrealized_pnl_sol
        return (total_pnl / self.entry_sol) * 100.0

    @property
    def hold_duration_str(self) -> str:
        try:
            entry_dt = datetime.fromisoformat(self.entry_time)
            end_dt   = (
                datetime.fromisoformat(self.exit_time)
                if self.exit_time else datetime.now(timezone.utc)
            )
            secs = int((end_dt - entry_dt).total_seconds())
            m, s = divmod(secs, 60)
            return f"{m}m {s}s"
        except Exception:
            return "?"


@dataclass
class DailyStats:
    date: str
    trades_total: int
    trades_won: int
    trades_lost: int
    starting_balance_sol: float
    ending_balance_sol: float
    total_pnl_sol: float
    win_rate: float
    best_trade: float
    worst_trade: float
    fees_paid_sol: float


# ---------------------------------------------------------------------------
# PortfolioTracker
# ---------------------------------------------------------------------------

class PortfolioTracker:
    """
    Manages the trades + daily_stats SQLite tables.

    Uses the **same** aiosqlite connection as the rest of the bot
    (injected via ``set_db_connection``).  Call this after init_db().
    """

    def __init__(self) -> None:
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def set_db_connection(self, conn: aiosqlite.Connection) -> None:
        """Inject the shared connection opened by sqlite_client.init_db()."""
        self._db = conn

    async def ensure_tables(self) -> None:
        """Create trades + daily_stats tables if they don't exist yet."""
        if not self._db:
            raise RuntimeError("PortfolioTracker: no DB connection. Call set_db_connection first.")
        await self._db.execute(CREATE_TRADES_TABLE)
        await self._db.execute(CREATE_TRADES_INDEX)
        await self._db.execute(CREATE_TRADES_STATUS_INDEX)
        await self._db.execute(CREATE_DAILY_STATS_TABLE)
        await self._db.commit()
        logger.info("PortfolioTracker tables ensured.")

    # ── daily stats helpers ────────────────────────────────────────────────

    def _today_utc(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()  # YYYY-MM-DD UTC

    async def _ensure_daily_row(self, today: str, starting_balance: float = 0.0) -> None:
        """Insert a daily_stats row for today if it doesn't exist."""
        await self._db.execute(
            """
            INSERT OR IGNORE INTO daily_stats (date, starting_balance_sol, ending_balance_sol)
            VALUES (?, ?, ?)
            """,
            (today, starting_balance, starting_balance),
        )
        await self._db.commit()

    # ── core trade operations ──────────────────────────────────────────────

    async def record_buy(
        self,
        mint_address: str,
        symbol: str,
        entry_price: float,
        entry_sol: float,
        tokens_received: float,
        signal_score: float,
        tx_signature: Optional[str] = None,
    ) -> int:
        """
        Insert an open trade row.  Returns the new trade ``id``.
        """
        async with self._lock:
            if not self._db:
                raise RuntimeError("No DB connection.")

            today = self._today_utc()
            await self._ensure_daily_row(today)

            cursor = await self._db.execute(
                """
                INSERT INTO trades (
                    mint_address, symbol, entry_price, entry_sol,
                    tokens_received, current_price, current_value_sol,
                    signal_score, highest_price, tx_signature_buy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mint_address, symbol, entry_price, entry_sol,
                    tokens_received, entry_price, entry_sol,
                    signal_score, entry_price, tx_signature,
                ),
            )
            trade_id = cursor.lastrowid

            # bump today's total
            await self._db.execute(
                "UPDATE daily_stats SET trades_total = trades_total + 1 WHERE date = ?",
                (today,),
            )
            await self._db.commit()

        logger.info(
            f"PortfolioTracker: recorded BUY trade_id={trade_id} "
            f"mint={mint_address[:8]}… sol={entry_sol:.4f}"
        )
        return trade_id  # type: ignore[return-value]

    async def record_sell(
        self,
        trade_id: int,
        exit_price: float,
        exit_sol: float,
        exit_reason: str,
        status: str = "closed",
        fees_sol: float = 0.0,
        tx_signature: Optional[str] = None,
    ) -> None:
        """
        Close a trade, compute realized P&L, update daily_stats.
        """
        async with self._lock:
            if not self._db:
                raise RuntimeError("No DB connection.")

            trade = await self.get_trade(trade_id)
            if not trade:
                logger.error(f"record_sell: trade_id={trade_id} not found.")
                return

            realized_pnl = exit_sol - trade.entry_sol - fees_sol

            await self._db.execute(
                """
                UPDATE trades SET
                    status              = ?,
                    exit_time           = CURRENT_TIMESTAMP,
                    exit_reason         = ?,
                    realized_pnl_sol    = ?,
                    unrealized_pnl_sol  = 0.0,
                    current_price       = ?,
                    current_value_sol   = ?,
                    tx_signature_sell   = ?
                WHERE id = ?
                """,
                (
                    status, exit_reason, realized_pnl,
                    exit_price, exit_sol, tx_signature, trade_id,
                ),
            )

            today = self._today_utc()
            await self._ensure_daily_row(today)

            won = 1 if realized_pnl > 0 else 0
            await self._db.execute(
                """
                UPDATE daily_stats SET
                    trades_won  = trades_won  + ?,
                    trades_lost = trades_lost + ?,
                    total_pnl_sol = total_pnl_sol + ?,
                    fees_paid_sol = fees_paid_sol + ?,
                    best_trade  = MAX(best_trade,  ?),
                    worst_trade = MIN(worst_trade, ?)
                WHERE date = ?
                """,
                (won, 1 - won, realized_pnl, fees_sol,
                 realized_pnl, realized_pnl, today),
            )
            # recalculate win_rate
            await self._db.execute(
                """
                UPDATE daily_stats SET
                    win_rate = CASE WHEN trades_total > 0
                               THEN CAST(trades_won AS REAL) / trades_total * 100.0
                               ELSE 0.0 END
                WHERE date = ?
                """,
                (today,),
            )
            await self._db.commit()

        logger.info(
            f"PortfolioTracker: recorded SELL trade_id={trade_id} "
            f"reason={exit_reason} pnl={realized_pnl:+.4f} SOL"
        )

    async def update_price(
        self,
        trade_id: int,
        current_price: float,
        current_value_sol: float,
        highest_price: Optional[float] = None,
    ) -> None:
        """Update the live price / unrealized P&L for an open trade."""
        if not self._db:
            return
        trade = await self.get_trade(trade_id)
        if not trade:
            return

        new_highest = max(trade.highest_price, highest_price or current_price)
        unrealized  = current_value_sol - trade.entry_sol

        await self._db.execute(
            """
            UPDATE trades SET
                current_price      = ?,
                current_value_sol  = ?,
                unrealized_pnl_sol = ?,
                highest_price      = ?
            WHERE id = ?
            """,
            (current_price, current_value_sol, unrealized, new_highest, trade_id),
        )
        await self._db.commit()

    async def record_tp_rung_hit(self, trade_id: int, rung_multiplier: float) -> None:
        """Mark a take-profit rung as completed (idempotent)."""
        import json
        if not self._db:
            return
        async with self._lock:
            trade = await self.get_trade(trade_id)
            if not trade:
                return
            rungs = trade.tp_rungs_hit
            if rung_multiplier not in rungs:
                rungs.append(rung_multiplier)
                await self._db.execute(
                    "UPDATE trades SET tp_rungs_hit = ? WHERE id = ?",
                    (json.dumps(rungs), trade_id),
                )
                await self._db.commit()

    # ── read helpers ───────────────────────────────────────────────────────

    async def get_trade(self, trade_id: int) -> Optional[TradeRecord]:
        """Fetch a single trade by primary-key id."""
        import json
        if not self._db:
            return None
        async with self._db.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            d["tp_rungs_hit"] = json.loads(d.get("tp_rungs_hit") or "[]")
            return TradeRecord(**d)

    async def get_open_trades(self) -> List[TradeRecord]:
        """Return all currently open trades."""
        import json
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY entry_time DESC"
        ) as cur:
            rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["tp_rungs_hit"] = json.loads(d.get("tp_rungs_hit") or "[]")
            result.append(TradeRecord(**d))
        return result

    async def get_open_trade_by_mint(self, mint_address: str) -> Optional[TradeRecord]:
        """Return the first open trade for a given mint (or None)."""
        import json
        if not self._db:
            return None
        async with self._db.execute(
            "SELECT * FROM trades WHERE mint_address = ? AND status = 'open' LIMIT 1",
            (mint_address,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            d["tp_rungs_hit"] = json.loads(d.get("tp_rungs_hit") or "[]")
            return TradeRecord(**d)

    async def count_open_trades(self) -> int:
        """Return number of currently open positions."""
        if not self._db:
            return 0
        async with self._db.execute(
            "SELECT COUNT(*) FROM trades WHERE status = 'open'"
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def get_closed_trades(self, limit: int = 10) -> List[TradeRecord]:
        """Return the last N closed/emergency_sold trades."""
        import json
        if not self._db:
            return []
        async with self._db.execute(
            """
            SELECT * FROM trades
            WHERE status IN ('closed','emergency_sold')
            ORDER BY exit_time DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["tp_rungs_hit"] = json.loads(d.get("tp_rungs_hit") or "[]")
            result.append(TradeRecord(**d))
        return result

    async def get_today_stats(self) -> Optional[DailyStats]:
        """Return today's DailyStats row (or None if no trades yet)."""
        if not self._db:
            return None
        today = self._today_utc()
        async with self._db.execute(
            "SELECT * FROM daily_stats WHERE date = ?", (today,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return DailyStats(**dict(row))

    async def get_daily_pnl(self) -> float:
        """Return today's realized P&L in SOL (0.0 if no row yet)."""
        stats = await self.get_today_stats()
        return stats.total_pnl_sol if stats else 0.0

    async def set_starting_balance(self, balance_sol: float) -> None:
        """Record today's starting balance (call once at bot startup)."""
        if not self._db:
            return
        today = self._today_utc()
        await self._ensure_daily_row(today, starting_balance=balance_sol)
        # Only overwrite if still at default 0.0 to avoid clobbering real data
        await self._db.execute(
            """
            UPDATE daily_stats
            SET starting_balance_sol = ?,
                ending_balance_sol   = ?
            WHERE date = ? AND starting_balance_sol = 0.0
            """,
            (balance_sol, balance_sol, today),
        )
        await self._db.commit()

    async def update_ending_balance(self, balance_sol: float) -> None:
        """Update tonight's ending balance (call after each trade closes)."""
        if not self._db:
            return
        today = self._today_utc()
        await self._ensure_daily_row(today)
        await self._db.execute(
            "UPDATE daily_stats SET ending_balance_sol = ? WHERE date = ?",
            (balance_sol, today),
        )
        await self._db.commit()

    # ── Telegram-ready formatters ──────────────────────────────────────────

    async def format_portfolio_message(self) -> str:
        """Format /portfolio reply — all open positions."""
        trades = await self.get_open_trades()
        if not trades:
            return "📭 *No open positions.*"

        lines = ["📊 *Open Positions*\n"]
        for t in trades:
            pnl_emoji = "🟢" if t.unrealized_pnl_sol >= 0 else "🔴"
            lines.append(
                f"{pnl_emoji} *{t.symbol or t.mint_address[:8]+'…'}*\n"
                f"   Entry: ${t.entry_price:.8f} | Now: ${t.current_price:.8f}\n"
                f"   Size: {t.entry_sol:.4f} SOL | "
                f"P&L: `{t.unrealized_pnl_sol:+.4f} SOL` ({t.pnl_pct:+.1f}%)\n"
                f"   Hold: {t.hold_duration_str} | "
                f"Score: {t.signal_score:.0f}/100\n"
            )
        return "\n".join(lines)

    async def format_stats_message(self) -> str:
        """Format /stats reply — today's performance."""
        stats = await self.get_today_stats()
        if not stats:
            return "📭 *No trades today yet.*"

        pnl_emoji = "🟢" if stats.total_pnl_sol >= 0 else "🔴"
        return (
            f"📈 *Today's Performance* ({stats.date})\n\n"
            f"Trades: `{stats.trades_total}` "
            f"(✅ {stats.trades_won} won / ❌ {stats.trades_lost} lost)\n"
            f"Win Rate: `{stats.win_rate:.1f}%`\n"
            f"{pnl_emoji} P&L: `{stats.total_pnl_sol:+.4f} SOL`\n"
            f"Best: `{stats.best_trade:+.4f} SOL` | "
            f"Worst: `{stats.worst_trade:+.4f} SOL`\n"
            f"Fees paid: `{stats.fees_paid_sol:.5f} SOL`\n"
            f"Balance: `{stats.starting_balance_sol:.4f}` → "
            f"`{stats.ending_balance_sol:.4f} SOL`"
        )

    async def format_history_message(self, limit: int = 10) -> str:
        """Format /history reply — last N closed trades."""
        trades = await self.get_closed_trades(limit=limit)
        if not trades:
            return "📭 *No closed trades yet.*"

        lines = [f"📜 *Last {limit} Closed Trades*\n"]
        for t in trades:
            pnl_emoji = "🟢" if t.realized_pnl_sol >= 0 else "🔴"
            lines.append(
                f"{pnl_emoji} *{t.symbol or t.mint_address[:8]+'…'}*  "
                f"[{t.exit_reason or '?'}]\n"
                f"   P&L: `{t.realized_pnl_sol:+.4f} SOL` ({t.pnl_pct:+.1f}%) | "
                f"Held {t.hold_duration_str}\n"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

portfolio_tracker = PortfolioTracker()
