"""
Full backtesting engine.
Run: python -m backtesting.backtest_runner
Simulates the bot running on historical data and reports results.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from config.settings import BASE_DIR

BACKTEST_DB = BASE_DIR / "data" / "backtest.db"
MODELS_DIR = BASE_DIR / "models"
RESULTS_FILE = BASE_DIR / "backtest_results.csv"


@dataclass
class TradeSimulation:
    mint: str
    symbol: str
    entry_time: datetime
    entry_price: float
    exit_price: float
    exit_reason: str          # 'take_profit' | 'stop_loss' | 'timeout'
    pnl_pct: float
    hold_minutes: int
    ml_score: float
    pump_prob: float
    rug_prob: float
    was_correct: bool          # did ML predict correctly?


@dataclass
class BacktestResult:
    total_tokens_scanned: int = 0
    signals_triggered: int = 0
    trades_simulated: int = 0
    wins: int = 0
    losses: int = 0
    rugs_caught: int = 0       # rug detected and skipped
    rugs_missed: int = 0       # rug NOT detected, would have lost
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    total_pnl_pct: float = 0.0
    sharpe_ratio: float = 0.0
    avg_hold_minutes: float = 0.0
    avg_entry_minutes: float = 0.0
    trades: list = field(default_factory=list)


class BacktestEngine:

    TAKE_PROFIT_PCT = 0.50
    STOP_LOSS_PCT = -0.25
    MAX_HOLD_HOURS = 4
    ML_PUMP_THRESHOLD = 0.55
    ML_RUG_THRESHOLD = 0.35
    ML_SCORE_THRESHOLD = 0.60

    def __init__(self) -> None:
        self.pump_model: Optional[object] = None
        self.rug_model: Optional[object] = None
        self.entry_model: Optional[object] = None
        self.scaler: Optional[object] = None

    def load_models(self) -> bool:
        required = ["pump_classifier.pkl", "rug_detector.pkl",
                     "entry_predictor.pkl", "feature_scaler.pkl"]
        if not all((MODELS_DIR / f).exists() for f in required):
            print(" Models not found. Run train_models.py first.")
            return False
        self.pump_model = joblib.load(MODELS_DIR / "pump_classifier.pkl")
        self.rug_model = joblib.load(MODELS_DIR / "rug_detector.pkl")
        self.entry_model = joblib.load(MODELS_DIR / "entry_predictor.pkl")
        self.scaler = joblib.load(MODELS_DIR / "feature_scaler.pkl")
        print("Models loaded")
        return True

    def load_historical_data(self) -> pd.DataFrame:
        if not BACKTEST_DB.exists():
            print(f"Backtest DB not found at {BACKTEST_DB}")
            return pd.DataFrame()
        conn = sqlite3.connect(str(BACKTEST_DB))
        df = pd.read_sql("""
            SELECT * FROM historical_tokens
            WHERE price_24h IS NOT NULL
              AND price_1h IS NOT NULL
              AND initial_price > 0
            ORDER BY collected_at ASC
        """, conn)
        conn.close()
        print(f"Loaded {len(df)} labeled tokens for backtesting")
        return df

    def build_features(self, row: dict) -> np.ndarray:
        liq = float(row.get("initial_liquidity", 0))
        mcap = float(row.get("initial_mcap", 1))
        return np.array([[
            liq,
            mcap,
            float(row.get("initial_volume_1h", 0)),
            float(row.get("buy_sell_ratio", 1.0)),
            float(row.get("tx_count_1h", 0)),
            float(row.get("price_change_5m", 0)),
            float(row.get("price_change_1h", 0)),
            float(row.get("security_score", 0)),
            float(row.get("top10_holders_pct", 0)),
            float(row.get("lp_burned", 0)),
            float(row.get("mint_revoked", 0)),
            float(row.get("volume_spike_ratio", liq / max(mcap, 1))),
            liq / max(mcap, 1),
            float(row.get("age_minutes", 0)),
        ]])

    def simulate_trade(
        self,
        row: dict,
        pump_prob: float,
        rug_prob: float,
        entry_minutes: int,
        ml_score: float,
    ) -> TradeSimulation:
        entry_price = float(row["initial_price"])

        if entry_minutes <= 60:
            exit_pool_price = float(row.get("price_1h", entry_price))
        elif entry_minutes <= 360:
            exit_pool_price = float(row.get("price_6h", entry_price))
        else:
            exit_pool_price = float(row.get("price_24h", entry_price))

        max_price = float(row.get("max_price_24h", exit_pool_price))
        max_gain = (max_price - entry_price) / max(entry_price, 1e-10)
        final_price = float(row.get("price_24h", entry_price))
        final_pnl = (final_price - entry_price) / max(entry_price, 1e-10)

        if max_gain >= self.TAKE_PROFIT_PCT:
            exit_price = entry_price * (1.0 + self.TAKE_PROFIT_PCT)
            pnl = self.TAKE_PROFIT_PCT
            reason = "take_profit"
        elif final_pnl <= self.STOP_LOSS_PCT:
            exit_price = entry_price * (1.0 + self.STOP_LOSS_PCT)
            pnl = self.STOP_LOSS_PCT
            reason = "stop_loss"
        else:
            exit_price = final_price
            pnl = final_pnl
            reason = "timeout"

        return TradeSimulation(
            mint=str(row["mint"]),
            symbol=str(row.get("symbol", "???")),
            entry_time=datetime.now(),
            entry_price=entry_price,
            exit_price=exit_price,
            exit_reason=reason,
            pnl_pct=pnl * 100.0,
            hold_minutes=min(entry_minutes + 240, 1440),
            ml_score=ml_score,
            pump_prob=pump_prob,
            rug_prob=rug_prob,
            was_correct=(pnl > 0),
        )

    def run(self) -> Optional[BacktestResult]:
        if not self.load_models():
            return None

        df = self.load_historical_data()
        if df.empty:
            print("No labeled data found.")
            return None

        result = BacktestResult()
        result.total_tokens_scanned = len(df)
        pnl_list: list[float] = []

        for _, row_raw in df.iterrows():
            row = row_raw.to_dict()

            X = self.build_features(row)
            X_scaled = self.scaler.transform(X)

            pump_prob = float(self.pump_model.predict_proba(X_scaled)[0][1])
            rug_score = float(self.rug_model.decision_function(X_scaled)[0])
            rug_prob = float(1.0 / (1.0 + np.exp(-rug_score)))
            entry_min = max(0, int(round(float(self.entry_model.predict(X_scaled)[0]))))
            ml_score = (pump_prob * 0.6) + ((1.0 - rug_prob) * 0.4)

            actual_rug = int(row.get("label_rug", 0)) == 1

            if rug_prob > self.ML_RUG_THRESHOLD:
                if actual_rug:
                    result.rugs_caught += 1
                continue

            if actual_rug:
                result.rugs_missed += 1

            if pump_prob < self.ML_PUMP_THRESHOLD or ml_score < self.ML_SCORE_THRESHOLD:
                continue

            result.signals_triggered += 1

            trade = self.simulate_trade(row, pump_prob, rug_prob, entry_min, ml_score)
            result.trades_simulated += 1
            result.trades.append(trade)
            pnl_list.append(trade.pnl_pct)

            if trade.pnl_pct > 0:
                result.wins += 1
            else:
                result.losses += 1

        if pnl_list:
            result.win_rate = result.wins / max(result.trades_simulated, 1)
            result.avg_pnl_pct = float(np.mean(pnl_list))
            result.best_trade_pct = float(max(pnl_list))
            result.worst_trade_pct = float(min(pnl_list))
            result.total_pnl_pct = float(sum(pnl_list))
            result.avg_hold_minutes = float(np.mean([t.hold_minutes for t in result.trades]))
            if float(np.std(pnl_list)) > 0:
                result.sharpe_ratio = (float(np.mean(pnl_list)) / float(np.std(pnl_list))) * np.sqrt(252)

        self.print_report(result)
        self.save_report(result)
        return result

    def print_report(self, r: BacktestResult) -> None:
        print("\n" + "=" * 50)
        print("         BACKTEST REPORT")
        print("=" * 50)
        print(f"Tokens Scanned:     {r.total_tokens_scanned}")
        print(f"Rugs Caught:        {r.rugs_caught}")
        print(f"Rugs Missed:        {r.rugs_missed}")
        print(f"Signals Triggered:  {r.signals_triggered}")
        print(f"Trades Simulated:   {r.trades_simulated}")
        print("-" * 50)
        print(f"Win Rate:           {r.win_rate:.1%}")
        print(f"Avg PnL:            {r.avg_pnl_pct:+.1f}%")
        print(f"Best Trade:         {r.best_trade_pct:+.1f}%")
        print(f"Worst Trade:        {r.worst_trade_pct:+.1f}%")
        print(f"Total PnL:          {r.total_pnl_pct:+.1f}%")
        print(f"Sharpe Ratio:       {r.sharpe_ratio:.2f}")
        print(f"Avg Hold:           {r.avg_hold_minutes:.0f} min")
        print("-" * 50)

        top5 = sorted(r.trades, key=lambda x: x.pnl_pct, reverse=True)[:5]
        print("TOP 5 TRADES:")
        for t in top5:
            print(f"  {t.symbol:10} {t.pnl_pct:+.0f}%  pump={t.pump_prob:.0%} rug={t.rug_prob:.0%}")
        print("=" * 50 + "\n")

    def save_report(self, r: BacktestResult) -> None:
        rows = [
            {
                "symbol": t.symbol,
                "pnl_pct": round(t.pnl_pct, 2),
                "exit_reason": t.exit_reason,
                "hold_minutes": t.hold_minutes,
                "pump_prob": round(t.pump_prob, 4),
                "rug_prob": round(t.rug_prob, 4),
                "ml_score": round(t.ml_score, 4),
                "was_correct": t.was_correct,
            }
            for t in r.trades
        ]
        if rows:
            pd.DataFrame(rows).to_csv(RESULTS_FILE, index=False)
            print(f"Full trade log saved to {RESULTS_FILE}")


if __name__ == "__main__":
    engine = BacktestEngine()
    engine.run()
