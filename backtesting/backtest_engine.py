# backtesting/backtest_engine.py
"""
Event-driven backtesting engine for Solana meme coin signals.

Simulates the live bot's full decision pipeline on historical data,
then performs a grid-search across 336 parameter combinations to find
the optimal trading configuration.

Usage:
    engine = BacktestEngine()
    await engine.run()
    results = await engine.get_best_params()
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field, asdict
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import numpy as np
from loguru import logger

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore

# ---------------------------------------------------------------------------
# Paths + constants
# ---------------------------------------------------------------------------

BACKTEST_DB_PATH  = Path(__file__).parent.parent / "data" / "backtest.db"
MODELS_DIR        = Path(__file__).parent.parent / "models"
OPT_PARAMS_PATH   = MODELS_DIR / "optimal_parameters.json"

MODELS_DIR.mkdir(parents=True, exist_ok=True)

SOL_PRICE_USD     = 150.0     # approximate; used to convert USD liquidity to SOL
REALISTIC_FEE_SOL = 0.002     # per trade (both buy + sell)

# ---------------------------------------------------------------------------
# Grid-search parameter space (336 combinations)
# ---------------------------------------------------------------------------

SIGNAL_THRESHOLDS  = [0.60, 0.65, 0.70, 0.72, 0.75, 0.80, 0.85]
POSITION_SIZES_SOL = [0.02, 0.03, 0.04, 0.05]
STOP_LOSSES        = [0.20, 0.30, 0.40, 0.50]
TP2X_SIZES         = [0.20, 0.25, 0.30]

# Take-profit ladder definition
TP_LADDER = [(2.0, None), (5.0, None), (10.0, None)]   # multiplier; fraction filled per run

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SimParams:
    """One set of simulation parameters."""
    signal_threshold: float
    position_size_sol: float
    stop_loss_pct: float         # e.g. 0.30 = -30% stop
    tp2x_fraction: float         # fraction to sell at 2x
    # derived
    tp5x_fraction: float = 0.25
    tp10x_fraction: float = 0.25
    trailing_fraction: float = 0.25  # remainder on trailing stop

    def __post_init__(self) -> None:
        # Normalise TP fractions to sum to 1.0
        total = self.tp2x_fraction + self.tp5x_fraction + self.tp10x_fraction
        remaining = 1.0 - total
        self.trailing_fraction = max(0.0, remaining)


@dataclass
class SimTrade:
    """Result of one simulated trade."""
    token_id: int
    mint_address: str
    signal_score: float
    entry_price: float
    exit_price: float
    pnl_pct: float           # e.g. 0.50 = +50%
    pnl_sol: float
    exit_reason: str         # take_profit_2x / trailing_stop / emergency / time_stop
    hold_time_min: int
    was_rug: bool
    hit_2x: bool
    hit_5x: bool
    hit_10x: bool
    position_size_sol: float
    params_id: int           # FK to simulation run


@dataclass
class SimResult:
    """Aggregate result for one parameter combination."""
    params: SimParams
    params_id: int
    total_trades: int        = 0
    wins: int                = 0
    losses: int              = 0
    gross_profit: float      = 0.0
    gross_loss: float        = 0.0
    total_pnl_sol: float     = 0.0
    max_drawdown_pct: float  = 0.0
    sharpe_ratio: float      = 0.0
    win_rate: float          = 0.0
    profit_factor: float     = 0.0
    avg_win_pct: float       = 0.0
    avg_loss_pct: float      = 0.0

    def finalise(self) -> None:
        if self.total_trades == 0:
            return
        self.win_rate = self.wins / self.total_trades
        self.profit_factor = (
            self.gross_profit / self.gross_loss
            if self.gross_loss > 0 else float("inf")
        )
        if self.wins > 0:
            self.avg_win_pct = self.gross_profit / self.wins
        if self.losses > 0:
            self.avg_loss_pct = self.gross_loss / self.losses


# ---------------------------------------------------------------------------
# SQLite DDL for backtest tables
# ---------------------------------------------------------------------------

_DDL_BACKTEST_TRADES = """
CREATE TABLE IF NOT EXISTS backtest_trades (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    params_id        INTEGER NOT NULL,
    token_id         INTEGER NOT NULL,
    mint_address     TEXT    NOT NULL,
    signal_score     REAL    NOT NULL DEFAULT 0.0,
    entry_price      REAL    NOT NULL DEFAULT 0.0,
    exit_price       REAL    NOT NULL DEFAULT 0.0,
    pnl_pct          REAL    NOT NULL DEFAULT 0.0,
    pnl_sol          REAL    NOT NULL DEFAULT 0.0,
    exit_reason      TEXT    NOT NULL DEFAULT '',
    hold_time_min    INTEGER NOT NULL DEFAULT 0,
    was_rug          INTEGER NOT NULL DEFAULT 0,
    hit_2x           INTEGER NOT NULL DEFAULT 0,
    hit_5x           INTEGER NOT NULL DEFAULT 0,
    hit_10x          INTEGER NOT NULL DEFAULT 0,
    position_size_sol REAL   NOT NULL DEFAULT 0.0
);
"""

_DDL_BACKTEST_PARAMS = """
CREATE TABLE IF NOT EXISTS backtest_params (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_threshold  REAL NOT NULL,
    position_size_sol REAL NOT NULL,
    stop_loss_pct     REAL NOT NULL,
    tp2x_fraction     REAL NOT NULL,
    run_at            INTEGER NOT NULL DEFAULT 0
);
"""

_DDL_BACKTEST_SUMMARY = """
CREATE TABLE IF NOT EXISTS backtest_summary (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    params_id      INTEGER NOT NULL,
    total_trades   INTEGER NOT NULL DEFAULT 0,
    wins           INTEGER NOT NULL DEFAULT 0,
    losses         INTEGER NOT NULL DEFAULT 0,
    win_rate       REAL    NOT NULL DEFAULT 0.0,
    profit_factor  REAL    NOT NULL DEFAULT 0.0,
    sharpe_ratio   REAL    NOT NULL DEFAULT 0.0,
    max_drawdown   REAL    NOT NULL DEFAULT 0.0,
    total_pnl_sol  REAL    NOT NULL DEFAULT 0.0,
    avg_win_pct    REAL    NOT NULL DEFAULT 0.0,
    avg_loss_pct   REAL    NOT NULL DEFAULT 0.0,
    gross_profit   REAL    NOT NULL DEFAULT 0.0,
    gross_loss     REAL    NOT NULL DEFAULT 0.0,
    is_best        INTEGER NOT NULL DEFAULT 0,
    run_at         INTEGER NOT NULL DEFAULT 0
);
"""

_DDL_MONTHLY_PERF = """
CREATE TABLE IF NOT EXISTS monthly_performance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    params_id     INTEGER NOT NULL,
    year_month    TEXT    NOT NULL,   -- YYYY-MM
    trades        INTEGER NOT NULL DEFAULT 0,
    wins          INTEGER NOT NULL DEFAULT 0,
    pnl_sol       REAL    NOT NULL DEFAULT 0.0,
    win_rate      REAL    NOT NULL DEFAULT 0.0
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bt_trades_params ON backtest_trades (params_id);",
    "CREATE INDEX IF NOT EXISTS idx_bt_trades_token  ON backtest_trades (token_id);",
    "CREATE INDEX IF NOT EXISTS idx_bt_summary_params ON backtest_summary (params_id);",
]


# ---------------------------------------------------------------------------
# Signal reconstructor
# ---------------------------------------------------------------------------

def _get_live_weights() -> dict[str, float]:
    """Import live weights from signal_engine, falling back to defaults."""
    try:
        from core.signal_engine import get_signal_weights
        return get_signal_weights()
    except Exception:
        pass
    return {
        "security": 0.20, "wallet": 0.15, "rug": 0.15,
        "holder_velocity": 0.15, "smart_money": 0.15,
        "tx_pattern": 0.10, "social": 0.05, "cross_dex": 0.05,
    }

_LIVE_W = _get_live_weights()

def _reconstruct_signal_score(token: Dict[str, Any]) -> float:
    """
    Reconstruct a composite signal score (0–100) from historical on-chain data.

    Uses weights imported from the live signal_engine at import time.
    Sub-scores are proxies based on fields available in historical_tokens.
    """
    # --- security_score (0-100) ---
    lp_ok  = int(bool(token.get("lp_burned")))
    rev_ok = int(bool(token.get("mint_revoked")))
    security_score = (lp_ok * 50.0 + rev_ok * 50.0)

    # --- wallet_score: penalise dev cluster ---
    dev_cluster = int(bool(token.get("dev_cluster_detected")))
    sniper_cnt  = float(token.get("sniper_count") or 0)
    wallet_score = max(0.0, 100.0 - dev_cluster * 40.0 - min(sniper_cnt * 5.0, 30.0))

    # --- rug_score: proxy ---
    top_holder = float(token.get("top_holder_percent") or 0)
    rug_score  = max(0.0, 100.0 - top_holder * 0.8)

    # --- holder_velocity_score ---
    hv = float(token.get("holder_velocity_1min") or 0)
    holder_velocity_score = min(100.0, hv * 10.0)

    # --- smart_money_score: first buyers relative to snipers ---
    first_buyers = float(token.get("first_buyer_count") or 0)
    total_buyers = max(1.0, first_buyers)
    sniper_frac  = sniper_cnt / total_buyers
    smart_money_score = max(0.0, 100.0 - sniper_frac * 100.0)

    # --- tx_pattern_score: buy/sell ratio ---
    bsr = float(token.get("buy_sell_ratio_5min") or 1.0)
    tx_pattern_score = min(100.0, max(0.0, (bsr - 0.5) / 2.5 * 100.0))

    # --- social_score: telegram mentions ---
    mentions    = float(token.get("telegram_mentions") or 0)
    social_score = min(100.0, mentions * 5.0)

    # --- cross_dex_score: inverse of wash trading ---
    wash = float(token.get("wash_trading_score") or 0)
    cross_dex_score = max(0.0, 100.0 - wash)

    # --- composite (weights from live signal_engine) ---
    composite = (
        security_score        * _LIVE_W["security"]
        + wallet_score        * _LIVE_W["wallet"]
        + rug_score           * _LIVE_W["rug"]
        + holder_velocity_score * _LIVE_W["holder_velocity"]
        + smart_money_score   * _LIVE_W["smart_money"]
        + tx_pattern_score    * _LIVE_W["tx_pattern"]
        + social_score        * _LIVE_W["social"]
        + cross_dex_score     * _LIVE_W["cross_dex"]
    )
    return round(composite, 2)


def _passes_gates(token: Dict[str, Any], threshold: float) -> Tuple[bool, str]:
    """
    Apply the hard gates from signal_engine (reconstructed for backtest).
    Returns (passes, reason).
    """
    liq = float(token.get("initial_liquidity_usd") or 0)
    if liq < 10_000:
        return False, f"liquidity_too_low (${liq:,.0f})"

    if token.get("dev_cluster_detected"):
        return False, "dev_cluster_critical"

    score = _reconstruct_signal_score(token)
    if score < threshold * 100.0:
        return False, f"score_too_low ({score:.1f}/100 < {threshold*100:.0f})"

    return True, "passed"


# ---------------------------------------------------------------------------
# Position simulator
# ---------------------------------------------------------------------------

def _simulate_position(
    token: Dict[str, Any],
    params: SimParams,
    position_sol: float,
) -> SimTrade:
    """
    Simulate a full position lifecycle using historical price checkpoints.

    Price checkpoints used (in order):
        T+1min, T+5min, T+15min, T+30min, T+60min

    Exit logic (evaluated at each checkpoint):
      1. Emergency stop   — price < entry * (1 - stop_loss_pct)
      2. TP ladder        — price >= entry * 2/5/10×
      3. Time stop        — at T+30min with <50% profit
      4. Trailing (final) — held to T+60min checkpoint
    """
    entry = float(token.get("price_at_1min") or 0)
    if entry <= 0:
        entry = float(token.get("price_at_launch") or 1e-12)
    if entry <= 0:
        entry = 1e-12

    checkpoints: List[Tuple[int, float]] = [
        (1,  float(token.get("price_at_1min")  or entry)),
        (5,  float(token.get("price_at_5min")  or entry)),
        (15, float(token.get("price_at_15min") or entry)),
        (30, float(token.get("price_at_30min") or entry)),
        (60, float(token.get("price_at_60min") or entry)),
    ]

    remaining_fraction = 1.0
    realized_pnl_sol   = 0.0
    tp_rungs_hit       = set()
    highest_price      = entry
    exit_reason        = "time_end"
    final_exit_price   = entry
    final_hold_min     = 0
    fees               = REALISTIC_FEE_SOL  # buy fee

    for t_min, price in checkpoints:
        if price <= 0:
            price = entry
        highest_price = max(highest_price, price)

        # --- 1. Emergency stop ---
        if price < entry * (1 - params.stop_loss_pct):
            pnl_this_tranche = position_sol * remaining_fraction * (price / entry - 1.0)
            realized_pnl_sol += pnl_this_tranche
            fees += REALISTIC_FEE_SOL
            exit_reason    = "emergency_stop"
            final_exit_price = price
            final_hold_min = t_min
            remaining_fraction = 0.0
            break

        # --- 2. TP ladder ---
        if price >= entry * 10.0 and 10.0 not in tp_rungs_hit:
            frac = params.tp10x_fraction * remaining_fraction
            realized_pnl_sol += position_sol * frac * (price / entry - 1.0)
            fees += REALISTIC_FEE_SOL * frac
            remaining_fraction -= frac
            tp_rungs_hit.add(10.0)
            exit_reason    = "take_profit_10x"
            final_exit_price = price
            final_hold_min = t_min
            if remaining_fraction <= 0.01:
                remaining_fraction = 0.0
                break

        if price >= entry * 5.0 and 5.0 not in tp_rungs_hit:
            frac = params.tp5x_fraction * remaining_fraction
            realized_pnl_sol += position_sol * frac * (price / entry - 1.0)
            fees += REALISTIC_FEE_SOL * frac
            remaining_fraction -= frac
            tp_rungs_hit.add(5.0)
            if exit_reason == "time_end":
                exit_reason = "take_profit_5x"
            final_exit_price = price
            final_hold_min = t_min
            if remaining_fraction <= 0.01:
                remaining_fraction = 0.0
                break

        if price >= entry * 2.0 and 2.0 not in tp_rungs_hit:
            frac = params.tp2x_fraction * remaining_fraction
            realized_pnl_sol += position_sol * frac * (price / entry - 1.0)
            fees += REALISTIC_FEE_SOL * frac
            remaining_fraction -= frac
            tp_rungs_hit.add(2.0)
            if exit_reason == "time_end":
                exit_reason = "take_profit_2x"
            final_exit_price = price
            final_hold_min = t_min
            if remaining_fraction <= 0.01:
                remaining_fraction = 0.0
                break

        # --- 3. Time stop at T+30 ---
        if t_min >= 30 and remaining_fraction > 0:
            profit_pct = (price / entry - 1.0) * 100.0
            if profit_pct < 50.0:
                pnl_this_tranche = position_sol * remaining_fraction * (price / entry - 1.0)
                realized_pnl_sol += pnl_this_tranche
                fees += REALISTIC_FEE_SOL * remaining_fraction
                exit_reason    = "time_stop"
                final_exit_price = price
                final_hold_min = t_min
                remaining_fraction = 0.0
                break

        # --- 4. Trailing stop at T+60 (last checkpoint) ---
        if t_min == 60 and remaining_fraction > 0:
            # Use actual price at 60 min
            profit_mult = price / entry
            # Trailing floor depends on profit level
            if profit_mult >= 4.0:
                floor = 0.85
            elif profit_mult >= 2.0:
                floor = 0.80
            elif profit_mult >= 1.5:
                floor = 0.75
            else:
                floor = 0.70
            exit_price_trail = max(price, highest_price * floor)
            pnl_this_tranche = position_sol * remaining_fraction * (exit_price_trail / entry - 1.0)
            realized_pnl_sol += pnl_this_tranche
            fees += REALISTIC_FEE_SOL * remaining_fraction
            exit_reason    = "trailing_stop" if exit_price_trail < price else "trailing_hold"
            final_exit_price = exit_price_trail
            final_hold_min = t_min
            remaining_fraction = 0.0
            break

    # If still holding (all checkpoints exhausted, remaining > 0), exit at last price
    if remaining_fraction > 0:
        last_price = checkpoints[-1][1] if checkpoints else entry
        pnl_this_tranche = position_sol * remaining_fraction * (last_price / entry - 1.0)
        realized_pnl_sol += pnl_this_tranche
        fees += REALISTIC_FEE_SOL * remaining_fraction
        final_exit_price = last_price
        final_hold_min = checkpoints[-1][0] if checkpoints else 60

    net_pnl_sol = realized_pnl_sol - fees
    pnl_pct     = net_pnl_sol / position_sol if position_sol > 0 else 0.0

    return SimTrade(
        token_id=int(token.get("id") or 0),
        mint_address=str(token.get("mint_address") or ""),
        signal_score=_reconstruct_signal_score(token),
        entry_price=entry,
        exit_price=final_exit_price,
        pnl_pct=pnl_pct,
        pnl_sol=net_pnl_sol,
        exit_reason=exit_reason,
        hold_time_min=final_hold_min,
        was_rug=bool(token.get("rug_pulled")),
        hit_2x=bool(token.get("hit_2x")),
        hit_5x=bool(token.get("hit_5x")),
        hit_10x=bool(token.get("hit_10x")),
        position_size_sol=position_sol,
        params_id=0,  # filled in later
    )


def _calc_sharpe(pnl_series: List[float], risk_free: float = 0.0) -> float:
    """Annualised Sharpe ratio from a list of per-trade P&L SOL values."""
    if len(pnl_series) < 2:
        return 0.0
    arr  = np.array(pnl_series, dtype=float)
    mean = float(np.mean(arr))
    std  = float(np.std(arr, ddof=1))
    if std == 0:
        return 0.0
    # Annualise assuming ~20 trades/day
    return float((mean - risk_free) / std * math.sqrt(20 * 365))


def _calc_max_drawdown(equity_curve: List[float]) -> float:
    """Maximum drawdown as a fraction (e.g. 0.18 = 18%)."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """
    Runs the full backtest simulation across a parameter grid.

    Loads historical_tokens from data/backtest.db, reconstructs signals,
    simulates position lifecycle, and finds optimal parameters via
    Sharpe-ratio-ranked grid search.
    """

    def __init__(self) -> None:
        self._db: Optional[aiosqlite.Connection] = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Open DB connection and ensure tables exist."""
        BACKTEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(BACKTEST_DB_PATH))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode = WAL;")
        await self._db.execute("PRAGMA synchronous = NORMAL;")
        await self._db.execute("PRAGMA cache_size = -32000;")   # 32 MB
        for ddl in [_DDL_BACKTEST_TRADES, _DDL_BACKTEST_PARAMS,
                     _DDL_BACKTEST_SUMMARY, _DDL_MONTHLY_PERF]:
            await self._db.execute(ddl)
        for idx in _INDEXES:
            await self._db.execute(idx)
        await self._db.commit()
        logger.info("BacktestEngine: DB ready.")

    async def stop(self) -> None:
        if self._db:
            await self._db.close()

    # ── data loader ────────────────────────────────────────────────────────

    async def _load_tokens(self, limit: int = 0) -> List[Dict[str, Any]]:
        """
        Load historical tokens ordered by created_at.
        Only returns tokens with data_complete=1 and price_at_launch > 0.
        """
        q = """
            SELECT * FROM historical_tokens
            WHERE data_complete = 1
              AND price_at_launch > 0
            ORDER BY created_at ASC
        """
        if limit > 0:
            q += f" LIMIT {limit}"
        async with self._db.execute(q) as cur:
            rows = await cur.fetchall()
        tokens = [dict(r) for r in rows]
        logger.info(f"BacktestEngine: loaded {len(tokens):,} tokens for simulation.")
        return tokens

    # ── single simulation run ──────────────────────────────────────────────

    async def _run_single(
        self,
        tokens: List[Dict[str, Any]],
        params: SimParams,
        params_id: int,
    ) -> SimResult:
        """
        Run one complete simulation for a given parameter set.
        """
        result = SimResult(params=params, params_id=params_id)
        equity        = 1.0   # starting capital (normalised to 1 SOL)
        equity_curve  = [equity]
        pnl_series: List[float] = []
        trade_buf: List[tuple] = []
        open_positions = 0

        for token in tokens:
            passes, reason = _passes_gates(token, params.signal_threshold)
            if not passes:
                continue
            if open_positions >= 3:    # max concurrent positions
                continue

            position_sol = params.position_size_sol
            if equity < position_sol + REALISTIC_FEE_SOL:
                continue  # insufficient capital

            trade = _simulate_position(token, params, position_sol)
            trade.params_id = params_id
            equity += trade.pnl_sol
            equity_curve.append(equity)
            pnl_series.append(trade.pnl_sol)
            result.total_trades += 1

            if trade.pnl_sol > 0:
                result.wins += 1
                result.gross_profit += trade.pnl_sol
            else:
                result.losses += 1
                result.gross_loss  += abs(trade.pnl_sol)

            result.total_pnl_sol += trade.pnl_sol

            # buffer for bulk insert
            trade_buf.append((
                params_id, trade.token_id, trade.mint_address,
                trade.signal_score, trade.entry_price, trade.exit_price,
                trade.pnl_pct, trade.pnl_sol, trade.exit_reason,
                trade.hold_time_min, int(trade.was_rug),
                int(trade.hit_2x), int(trade.hit_5x), int(trade.hit_10x),
                trade.position_size_sol,
            ))

        # Flush trade buffer
        if trade_buf:
            await self._db.executemany(
                """
                INSERT INTO backtest_trades (
                    params_id, token_id, mint_address, signal_score,
                    entry_price, exit_price, pnl_pct, pnl_sol, exit_reason,
                    hold_time_min, was_rug, hit_2x, hit_5x, hit_10x,
                    position_size_sol
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                trade_buf,
            )
            await self._db.commit()

        result.sharpe_ratio   = _calc_sharpe(pnl_series)
        result.max_drawdown_pct = _calc_max_drawdown(equity_curve)
        result.finalise()
        return result

    # ── grid search ────────────────────────────────────────────────────────

    async def run_grid_search(
        self, tokens: Optional[List[Dict[str, Any]]] = None, limit: int = 0
    ) -> SimResult:
        """
        Run full 336-combination grid search and return the best result.

        The best combination is selected by Sharpe ratio (ties broken by
        profit factor).
        """
        if tokens is None:
            tokens = await self._load_tokens(limit=limit)

        if not tokens:
            logger.warning("BacktestEngine: no tokens available — aborting grid search.")
            raise RuntimeError("No historical tokens available. Run data_collector first.")

        grid = list(product(SIGNAL_THRESHOLDS, POSITION_SIZES_SOL, STOP_LOSSES, TP2X_SIZES))
        total_combos = len(grid)
        logger.info(f"BacktestEngine: grid search over {total_combos} combinations, {len(tokens):,} tokens each.")

        best: Optional[SimResult] = None
        iterator = tqdm(grid, desc="Grid search", unit="combo") if tqdm else grid

        for combo in iterator:
            threshold, pos_size, stop_loss, tp2x = combo
            params = SimParams(
                signal_threshold=threshold,
                position_size_sol=pos_size,
                stop_loss_pct=stop_loss,
                tp2x_fraction=tp2x,
            )
            # Record params
            cur = await self._db.execute(
                """
                INSERT INTO backtest_params
                    (signal_threshold, position_size_sol, stop_loss_pct, tp2x_fraction, run_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (threshold, pos_size, stop_loss, tp2x, int(time.time())),
            )
            await self._db.commit()
            params_id = cur.lastrowid

            result = await self._run_single(tokens, params, params_id)

            # Save summary
            await self._db.execute(
                """
                INSERT INTO backtest_summary (
                    params_id, total_trades, wins, losses, win_rate,
                    profit_factor, sharpe_ratio, max_drawdown, total_pnl_sol,
                    avg_win_pct, avg_loss_pct, gross_profit, gross_loss, run_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    params_id, result.total_trades, result.wins, result.losses,
                    result.win_rate, result.profit_factor, result.sharpe_ratio,
                    result.max_drawdown_pct, result.total_pnl_sol,
                    result.avg_win_pct, result.avg_loss_pct,
                    result.gross_profit, result.gross_loss, int(time.time()),
                ),
            )
            await self._db.commit()

            if (
                best is None
                or result.sharpe_ratio > best.sharpe_ratio
                or (
                    result.sharpe_ratio == best.sharpe_ratio
                    and result.profit_factor > best.profit_factor
                )
            ):
                best = result
                logger.info(
                    f"New best: threshold={threshold} pos={pos_size} "
                    f"stop={stop_loss} tp2x={tp2x} → "
                    f"Sharpe={result.sharpe_ratio:.3f} WR={result.win_rate:.1%} "
                    f"PnL={result.total_pnl_sol:+.4f} SOL"
                )

        if best is None:
            raise RuntimeError("Grid search produced no valid results.")

        # Mark best in DB
        await self._db.execute(
            "UPDATE backtest_summary SET is_best = 1 WHERE params_id = ?",
            (best.params_id,),
        )
        await self._db.commit()

        await self._export_optimal_params(best)
        logger.info(
            f"BacktestEngine: grid search complete. Best Sharpe={best.sharpe_ratio:.3f}"
        )
        return best

    # ── export ────────────────────────────────────────────────────────────

    async def _export_optimal_params(self, best: SimResult) -> None:
        """Write optimal_parameters.json used by signal_engine + live bot."""
        params = best.params
        output = {
            "generated_at": int(time.time()),
            "backtest_summary": {
                "total_trades": best.total_trades,
                "win_rate": round(best.win_rate, 4),
                "sharpe_ratio": round(best.sharpe_ratio, 4),
                "profit_factor": round(best.profit_factor, 4),
                "max_drawdown_pct": round(best.max_drawdown_pct, 4),
                "total_pnl_sol": round(best.total_pnl_sol, 6),
            },
            "min_signal_score": params.signal_threshold,
            "position_size_sol": params.position_size_sol,
            "stop_loss_pct": params.stop_loss_pct,
            "tp2x_fraction": params.tp2x_fraction,
            "tp5x_fraction": params.tp5x_fraction,
            "tp10x_fraction": params.tp10x_fraction,
            "trailing_fraction": params.trailing_fraction,
            # Derived Kelly hint
            "position_size_kelly": round(params.position_size_sol * 5.0, 3),
            # Trailing stop tiers (unchanged from live bot defaults)
            "trailing_stop_profit_300": 0.15,
            "trailing_stop_profit_100": 0.20,
            "trailing_stop_profit_50":  0.25,
            "trailing_stop_default":    0.30,
            # Signal weights — optimised by backtest (placeholder for ML tuning)
            "signal_weights": {
                "security":        0.20,
                "wallet":          0.15,
                "rug":             0.15,
                "holder_velocity": 0.15,
                "smart_money":     0.15,
                "tx_pattern":      0.10,
                "social":          0.05,
                "cross_dex":       0.05,
            },
        }
        OPT_PARAMS_PATH.write_text(json.dumps(output, indent=2))
        logger.info(f"Optimal parameters saved to {OPT_PARAMS_PATH}")

    # ── monthly breakdown ──────────────────────────────────────────────────

    async def compute_monthly_breakdown(self, params_id: int) -> List[Dict]:
        """Compute per-month stats for the given params_id."""
        async with self._db.execute(
            """
            SELECT
                strftime('%Y-%m', datetime(t.hold_time_min * 60, 'unixepoch')) AS ym,
                COUNT(*) AS trades,
                SUM(CASE WHEN pnl_sol > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(pnl_sol) AS pnl_sol
            FROM backtest_trades t
            JOIN historical_tokens ht ON t.token_id = ht.id
            WHERE t.params_id = ?
            GROUP BY ym
            ORDER BY ym
            """,
            (params_id,),
        ) as cur:
            rows = await cur.fetchall()

        monthly = []
        for row in rows:
            d = dict(row)
            trades = d.get("trades") or 1
            d["win_rate"] = (d.get("wins") or 0) / trades
            monthly.append(d)
            await self._db.execute(
                """
                INSERT INTO monthly_performance
                    (params_id, year_month, trades, wins, pnl_sol, win_rate)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    params_id, d.get("ym", ""), d.get("trades", 0),
                    d.get("wins", 0), d.get("pnl_sol", 0.0), d.get("win_rate", 0.0),
                ),
            )
        await self._db.commit()
        return monthly

    # ── convenience run ───────────────────────────────────────────────────

    async def run(self, limit: int = 0) -> SimResult:
        """Full pipeline: load tokens → grid search → export."""
        await self.start()
        try:
            best = await self.run_grid_search(limit=limit)
            await self.compute_monthly_breakdown(best.params_id)
            return best
        finally:
            await self.stop()

    async def get_best_params(self) -> Optional[Dict]:
        """Load optimal_parameters.json if it exists."""
        if OPT_PARAMS_PATH.exists():
            return json.loads(OPT_PARAMS_PATH.read_text())
        return None


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Run backtest simulation")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max tokens to simulate (0 = all)",
    )
    parser.add_argument(
        "--single", action="store_true",
        help="Run a single default-params simulation instead of full grid",
    )
    args = parser.parse_args()

    async def main() -> None:
        engine = BacktestEngine()
        await engine.start()
        try:
            tokens = await engine._load_tokens(limit=args.limit)
            if not tokens:
                print("No tokens found in backtest.db. Run data_collector.py first.")
                sys.exit(1)

            if args.single:
                params = SimParams(
                    signal_threshold=0.72,
                    position_size_sol=0.05,
                    stop_loss_pct=0.30,
                    tp2x_fraction=0.25,
                )
                params_id = 1
                result = await engine._run_single(tokens, params, params_id)
                result.finalise()
                print(f"\nSingle run result:")
                print(f"  Trades:    {result.total_trades:,}")
                print(f"  Win rate:  {result.win_rate:.1%}")
                print(f"  Sharpe:    {result.sharpe_ratio:.3f}")
                print(f"  Total P&L: {result.total_pnl_sol:+.4f} SOL")
            else:
                best = await engine.run_grid_search(tokens=tokens)
                print(f"\nBest parameters found:")
                print(f"  Threshold: {best.params.signal_threshold}")
                print(f"  Pos size:  {best.params.position_size_sol} SOL")
                print(f"  Stop loss: {best.params.stop_loss_pct:.0%}")
                print(f"  Sharpe:    {best.sharpe_ratio:.3f}")
                print(f"  Win rate:  {best.win_rate:.1%}")
                print(f"  P&L:       {best.total_pnl_sol:+.4f} SOL")
                print(f"  Saved to:  {OPT_PARAMS_PATH}")
        finally:
            await engine.stop()

    asyncio.run(main())
