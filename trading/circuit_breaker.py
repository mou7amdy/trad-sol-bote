# trading/circuit_breaker.py
"""
Circuit Breaker — automated risk protection layer.

Monitors:
  • Daily loss limit  (15 % of starting balance → stop all trading)
  • Consecutive losses (3/5/7 → pause 30 min / 2 h / 24 h)
  • Network health    (RPC latency, WebSocket drop rate)
  • Market conditions (rug rate of last 10 tokens, SOL price drop)

All state is in-memory (fast reads) but persisted to SQLite so the
breaker survives a bot restart.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Callable, Coroutine, List, Optional, Any

import aiosqlite
from loguru import logger

from config.settings import settings, runtime_state

# ---------------------------------------------------------------------------
# Enums + state dataclass
# ---------------------------------------------------------------------------

class BreakerLevel(str, Enum):
    OK            = "OK"
    PAUSE_30M     = "PAUSE_30M"      # 3 consecutive losses
    PAUSE_2H      = "PAUSE_2H"       # 5 consecutive losses
    STOP_24H      = "STOP_24H"       # 7 consecutive losses
    DAILY_LIMIT   = "DAILY_LIMIT"    # daily loss > 15 %
    NETWORK_DOWN  = "NETWORK_DOWN"   # all RPCs offline
    MANUAL_PAUSE  = "MANUAL_PAUSE"   # /pause command


@dataclass
class BreakerState:
    level: BreakerLevel          = BreakerLevel.OK
    paused_until: Optional[float] = None      # monotonic timestamp
    consecutive_losses: int       = 0
    daily_loss_pct: float         = 0.0
    last_10_rug_rate: float       = 0.0
    rpc_latency_ms: float         = 0.0
    ws_drops_10m: int             = 0
    reason: str                   = ""
    triggered_at: float           = 0.0

    def is_active(self) -> bool:
        """True when trading should be halted."""
        if self.level == BreakerLevel.OK:
            return False
        if self.paused_until and time.monotonic() > self.paused_until:
            # Timed pause has expired — auto-reset if only a timed pause
            if self.level in (BreakerLevel.PAUSE_30M, BreakerLevel.PAUSE_2H,
                               BreakerLevel.STOP_24H):
                return False          # caller should call reset_if_expired()
        return True

    def status_str(self) -> str:
        if self.level == BreakerLevel.OK:
            return "🟢 OK — trading active"
        if self.paused_until:
            remaining = max(0.0, self.paused_until - time.monotonic())
            m = int(remaining // 60)
            s = int(remaining % 60)
            return f"🔴 {self.level.value} — resumes in {m}m {s}s\nReason: {self.reason}"
        return f"🔴 {self.level.value}\nReason: {self.reason}"


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """
    Thread-safe (asyncio) circuit breaker.

    Usage::

        circuit_breaker = CircuitBreaker()
        circuit_breaker.set_db_connection(conn)
        circuit_breaker.set_alert_fn(send_telegram_alert)

        # in trade logic:
        if circuit_breaker.is_active():
            return  # skip buy
    """

    # SOL mint for price check
    _SOL_PRICE_URL = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=solana&vs_currencies=usd"
    )

    def __init__(self) -> None:
        self._state  = BreakerState()
        self._lock   = asyncio.Lock()
        self._db: Optional[aiosqlite.Connection] = None
        self._alert_fn: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None

        # market-condition tracking
        self._last_sol_price: float     = 0.0
        self._sol_price_ts: float       = 0.0
        self._ws_drop_times: List[float] = []   # monotonic timestamps of drops

        # Runtime overrides (NEVER mutate settings directly)
        self._runtime_min_score: Optional[float] = None
        self._runtime_max_position: Optional[float] = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def set_db_connection(self, conn: aiosqlite.Connection) -> None:
        self._db = conn

    def set_alert_fn(
        self, fn: Callable[[str], Coroutine[Any, Any, None]]
    ) -> None:
        """Inject the Telegram alert coroutine (signature: async fn(text))."""
        self._alert_fn = fn

    # ── public read ────────────────────────────────────────────────────────

    def is_active(self) -> bool:
        """
        Fast synchronous check — call this before every buy/sell.
        Returns True when trading should be blocked.
        """
        self._maybe_auto_reset()
        return self._state.is_active()

    def get_state(self) -> BreakerState:
        return self._state

    # ── private helpers ────────────────────────────────────────────────────

    def _maybe_auto_reset(self) -> None:
        """Reset timed pauses that have expired (no await, safe to call sync)."""
        s = self._state
        if s.paused_until and time.monotonic() > s.paused_until:
            if s.level in (BreakerLevel.PAUSE_30M,
                           BreakerLevel.PAUSE_2H,
                           BreakerLevel.STOP_24H):
                s.level       = BreakerLevel.OK
                s.paused_until = None
                s.reason      = "auto-reset after pause expiry"
                logger.info("CircuitBreaker: timed pause expired — trading resumed.")

    async def _trigger(
        self,
        level: BreakerLevel,
        reason: str,
        pause_seconds: Optional[float] = None,
    ) -> None:
        """Activate (or escalate) the circuit breaker and send an alert."""
        async with self._lock:
            # Never downgrade level
            level_order = [
                BreakerLevel.OK,
                BreakerLevel.PAUSE_30M,
                BreakerLevel.PAUSE_2H,
                BreakerLevel.MANUAL_PAUSE,
                BreakerLevel.STOP_24H,
                BreakerLevel.DAILY_LIMIT,
                BreakerLevel.NETWORK_DOWN,
            ]
            if level_order.index(level) <= level_order.index(self._state.level):
                return   # already at same or higher level

            self._state.level        = level
            self._state.reason       = reason
            self._state.triggered_at = time.monotonic()
            if pause_seconds:
                self._state.paused_until = time.monotonic() + pause_seconds

        logger.warning(f"CircuitBreaker TRIGGERED: {level.value} — {reason}")
        await self._persist_state()
        await self._send_alert(
            f"🚨 *Circuit Breaker: {level.value}*\n"
            f"Reason: {reason}\n"
            + (f"Pause: {int(pause_seconds or 0)//60} min" if pause_seconds else "")
        )

    async def _send_alert(self, text: str) -> None:
        if self._alert_fn:
            try:
                await self._alert_fn(text)
            except Exception as exc:
                logger.error(f"CircuitBreaker: alert send failed: {exc}")

    async def _persist_state(self) -> None:
        """Write current state to circuit_breaker_state table."""
        if not self._db:
            return
        try:
            await self._db.execute(
                """
                INSERT OR REPLACE INTO circuit_breaker_state
                    (id, level, reason, consecutive_losses,
                     daily_loss_pct, triggered_at, paused_until_ts)
                VALUES (1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._state.level.value,
                    self._state.reason,
                    self._state.consecutive_losses,
                    self._state.daily_loss_pct,
                    datetime.now(timezone.utc).isoformat(),
                    self._state.paused_until or 0.0,
                ),
            )
            await self._db.commit()
        except Exception as exc:
            logger.error(f"CircuitBreaker: persist error: {exc}")

    # ── rule evaluators (called from external monitoring loops) ───────────

    async def on_trade_result(
        self,
        is_win: bool,
        pnl_sol: float,
        starting_balance_sol: float,
    ) -> None:
        """
        Call after every closed trade.
        Updates consecutive-loss counter + daily-loss-limit check.
        """
        async with self._lock:
            if is_win:
                self._state.consecutive_losses = 0
            else:
                self._state.consecutive_losses += 1

        losses = self._state.consecutive_losses

        # Daily loss limit check
        if starting_balance_sol > 0:
            # We use pnl from portfolio_tracker, so this is cumulative
            from trading.portfolio_tracker import portfolio_tracker
            daily_pnl = await portfolio_tracker.get_daily_pnl()
            loss_pct  = abs(daily_pnl) / starting_balance_sol * 100.0
            self._state.daily_loss_pct = loss_pct

            if daily_pnl < 0 and loss_pct >= settings.DAILY_LOSS_LIMIT_PERCENT:
                await self._trigger(
                    BreakerLevel.DAILY_LIMIT,
                    f"Daily loss {loss_pct:.1f}% ≥ limit {settings.DAILY_LOSS_LIMIT_PERCENT}%",
                )
                return

        # Consecutive-loss tiers
        if losses >= 7:
            await self._trigger(
                BreakerLevel.STOP_24H,
                f"{losses} consecutive losses",
                pause_seconds=86_400,
            )
        elif losses >= 5:
            await self._trigger(
                BreakerLevel.PAUSE_2H,
                f"{losses} consecutive losses",
                pause_seconds=7_200,
            )
        elif losses >= 3:
            await self._trigger(
                BreakerLevel.PAUSE_30M,
                f"{losses} consecutive losses",
                pause_seconds=1_800,
            )

    async def on_rpc_latency(self, latency_ms: float) -> None:
        """
        Report RPC latency.  Triggers network protection at > 800 ms.
        The actual RPC-switching is handled by swap_engine; this just flags.
        """
        self._state.rpc_latency_ms = latency_ms
        if latency_ms > 800:
            logger.warning(f"CircuitBreaker: RPC latency {latency_ms:.0f}ms > 800ms threshold.")

    async def on_ws_drop(self) -> None:
        """Record a WebSocket drop event.  >3 in 10 min → warn."""
        now = time.monotonic()
        self._ws_drop_times = [t for t in self._ws_drop_times if now - t < 600]
        self._ws_drop_times.append(now)
        self._state.ws_drops_10m = len(self._ws_drop_times)

        if len(self._ws_drop_times) > 3:
            logger.warning(
                f"CircuitBreaker: {len(self._ws_drop_times)} WS drops in 10 min."
            )
            await self._send_alert(
                f"⚠️ *Circuit Breaker Warning*\n"
                f"{len(self._ws_drop_times)} WebSocket drops in 10 minutes.\n"
                f"Consider restarting the bot if this continues."
            )

    async def on_all_rpcs_down(self) -> None:
        """Call when every configured RPC is unreachable."""
        await self._trigger(
            BreakerLevel.NETWORK_DOWN,
            "All RPC endpoints unreachable",
        )

    async def on_rug_rate_update(self, rug_rate_pct: float) -> None:
        """
        Call with the rug-rate of the last 10 detected tokens.
        > 70 % → raise minimum score to 0.85 (handled in settings at runtime).
        """
        self._state.last_10_rug_rate = rug_rate_pct
        if rug_rate_pct > 70.0:
            logger.warning(
                f"CircuitBreaker: {rug_rate_pct:.0f}% rug rate on last 10 tokens. "
                f"Raising MIN_SIGNAL_SCORE_FOR_BUY to 0.85."
            )
            runtime_state.min_signal_score_for_buy = 85.0
            await self._send_alert(
                f"⚠️ *Circuit Breaker: High Rug Rate*\n"
                f"Last 10 tokens: {rug_rate_pct:.0f}% rugs.\n"
                f"Minimum signal score raised to 85/100."
            )

    async def on_sol_price_drop(self, current_price_usd: float) -> None:
        """
        Compare against 1-hour-ago price.
        > 10 % drop → cut position size 50 % (runtime setting override).
        """
        now = time.monotonic()
        if self._last_sol_price > 0 and (now - self._sol_price_ts) >= 3600:
            drop_pct = (self._last_sol_price - current_price_usd) / self._last_sol_price * 100.0
            if drop_pct >= 10.0:
                logger.warning(
                    f"CircuitBreaker: SOL dropped {drop_pct:.1f}% in 1h. "
                    f"Halving MAX_POSITION_SIZE_SOL."
                )
                current_max = runtime_state.max_position_size_sol
                runtime_state.max_position_size_sol = max(0.01, current_max / 2.0)
                await self._send_alert(
                    f"⚠️ *Circuit Breaker: SOL Price Drop*\n"
                    f"SOL fell {drop_pct:.1f}% in 1 hour.\n"
                    f"Max position size halved to {self._runtime_max_position:.3f} SOL."
                )
        # Update baseline every hour
        if (now - self._sol_price_ts) >= 3600:
            self._last_sol_price = current_price_usd
            self._sol_price_ts   = now

    # ── manual controls ────────────────────────────────────────────────────

    async def manual_pause(self, reason: str = "operator command") -> None:
        """Triggered by /pause Telegram command."""
        async with self._lock:
            self._state.level  = BreakerLevel.MANUAL_PAUSE
            self._state.reason = reason
            self._state.paused_until = None   # indefinite
        await self._persist_state()
        logger.info("CircuitBreaker: manual pause activated.")

    async def manual_resume(self) -> None:
        """Triggered by /resume Telegram command."""
        async with self._lock:
            if self._state.level == BreakerLevel.MANUAL_PAUSE:
                self._state.level        = BreakerLevel.OK
                self._state.paused_until = None
                self._state.reason       = "manually resumed"
        await self._persist_state()
        logger.info("CircuitBreaker: manual resume.")

    async def reset_consecutive_losses(self) -> None:
        """Reset the consecutive loss counter (e.g. after a win)."""
        async with self._lock:
            self._state.consecutive_losses = 0

    # ── table DDL ─────────────────────────────────────────────────────────

    async def ensure_table(self) -> None:
        """Create circuit_breaker_state table if not exists."""
        if not self._db:
            return
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS circuit_breaker_state (
                id                  INTEGER PRIMARY KEY DEFAULT 1,
                level               TEXT    NOT NULL DEFAULT 'OK',
                reason              TEXT    NOT NULL DEFAULT '',
                consecutive_losses  INTEGER NOT NULL DEFAULT 0,
                daily_loss_pct      REAL    NOT NULL DEFAULT 0.0,
                triggered_at        TEXT,
                paused_until_ts     REAL    NOT NULL DEFAULT 0.0
            );
            """
        )
        await self._db.commit()

    async def load_persisted_state(self) -> None:
        """Restore state from SQLite on bot restart."""
        if not self._db:
            return
        try:
            async with self._db.execute(
                "SELECT * FROM circuit_breaker_state WHERE id = 1"
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return
                d = dict(row)
                self._state.level              = BreakerLevel(d["level"])
                self._state.reason             = d["reason"]
                self._state.consecutive_losses = d["consecutive_losses"]
                self._state.daily_loss_pct     = d["daily_loss_pct"]
                saved_paused_until = d.get("paused_until_ts", 0.0)
                # saved_paused_until is a wall-clock offset; convert to monotonic
                # We can't perfectly reconstruct monotonic, so we clear it if past
                if saved_paused_until > time.time():
                    self._state.paused_until = (
                        time.monotonic() + (saved_paused_until - time.time())
                    )
                else:
                    self._state.paused_until = None
                    if self._state.level in (
                        BreakerLevel.PAUSE_30M,
                        BreakerLevel.PAUSE_2H,
                        BreakerLevel.STOP_24H,
                    ):
                        self._state.level = BreakerLevel.OK

            logger.info(
                f"CircuitBreaker: loaded state={self._state.level.value} "
                f"cons_losses={self._state.consecutive_losses}"
            )
        except Exception as exc:
            logger.error(f"CircuitBreaker: failed to load persisted state: {exc}")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

circuit_breaker = CircuitBreaker()
