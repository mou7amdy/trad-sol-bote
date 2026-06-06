#!/usr/bin/env python3
"""Generate a PDF technical summary of the backtesting & ML training system."""

from fpdf import FPDF
from pathlib import Path

OUTPUT = Path(__file__).parent / "backtest_ml_technical_report.pdf"

REPLACEMENTS = {
    "\u2014": "--",  "\u2013": "-",
    "\u2018": "'",   "\u2019": "'",
    "\u201c": '"',   "\u201d": '"',
    "\u2026": "...", "\u2022": "-",
    "\u00a0": " ",   "\u2192": "->",
    "\u2265": ">=",  "\u2264": "<=",
    "\uf0b7": "-",   "\u25cf": "*",
    "\u25b6": ">",   "\u25c0": "<",
    "\u2605": "*",   "\u2606": "*",
    "\u2713": "v",   "\u2714": "v",
    "\u2717": "x",   "\u2718": "x",
    "\u274c": "X",   "\u274e": "X",
    "\u2757": "!",   "\u2764": "<3",
    "\u2b50": "*",
    "\U0001f300": "?",   "\U0001f680": "^",   "\U0001f6a8": "!",
    "\U0001f4a1": "*",   "\U0001f4ca": "[]",  "\U0001f50d": "O",
    "\U0001f50e": "O",   "\U0001f511": "[K]", "\U0001f512": "[L]",
    "\U0001f513": "[U]", "\U0001f4b0": "$",   "\U0001f4b5": "$",
    "\U0001f4b8": "$",   "\U0001f4c8": "/\\", "\U0001f4c9": "\\/",
    "\U0001f4e2": "!",   "\U0001f4e3": "!",
    "\U0001f4e6": "[]",  "\U0001f4f1": "[P]", "\U0001f4f2": "[P]",
    "\U0001f4f7": "[C]", "\U0001f4f9": "[V]", "\U0001f4fa": "[TV]",
    "\U0001f4fb": "[R]", "\U0001f525": "[!]",
    "\U0001f534": "[R]", "\U0001f535": "[B]",
    "\U0001f536": "[O]", "\U0001f537": "[O]",
    "\U0001f538": "[O]", "\U0001f539": "[B]",
    "\U0001f53a": "[^]", "\U0001f53b": "[v]",
    "\U0001f53c": "[^]", "\U0001f53d": "[v]",
    "\U0001f600": ":)",  "\U0001f602": ":')", "\U0001f603": ":)",
    "\U0001f604": ":)",  "\U0001f608": "}:)", "\U0001f609": ";)",
    "\U0001f60a": ":)",  "\U0001f60e": "B)",  "\U0001f60f": ";)",
    "\U0001f61c": ";P",  "\U0001f61e": ":(",  "\U0001f620": ">:(",
    "\U0001f621": ">:(", "\U0001f622": ":(",  "\U0001f624": ">:)",
    "\U0001f62d": ":'(", "\U0001f62e": ":o",  "\U0001f631": ":o",
    "\U0001f632": ":o",  "\U0001f633": ";o",  "\U0001f637": ":X",
    "\U0001f642": ":)",  "\U0001f643": ":)",
    "\U0001f648": "[M]", "\U0001f44d": "[+]", "\U0001f44e": "[-]",
    "\U0001f44f": "[c]", "\U0001f4aa": "[b]", "\U0001f64f": "[p]",
    "\U0001f389": "[!]", "\U0001f38a": "[!]", "\U0001f3b5": "[N]",
    "\U0001f3b6": "[N]", "\U0001f3c6": "[T]", "\U0001f396": "[M]",
    "\U0001f3af": "[+]", "\u20bf": "BTC",     "\ufeff": "",
}

def S(text: str) -> str:
    for k, v in REPLACEMENTS.items():
        text = text.replace(k, v)
    # Also strip characters outside latin-1
    return text.encode("latin-1", "replace").decode("latin-1")


class ReportPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "I", 8)
            self.cell(0, 5, "Backtesting & ML Training System - Technical Report", align="C")
            self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def chapter_title(self, title: str):
        self.set_font("Helvetica", "B", 14)
        self.set_fill_color(30, 60, 114)
        self.set_text_color(255, 255, 255)
        self.cell(0, 10, S(f"  {title}"), fill=True, ln=True)
        self.set_text_color(0, 0, 0)
        self.ln(3)

    def section_title(self, title: str):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(30, 60, 114)
        self.cell(0, 7, S(title), ln=True)
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def body_text(self, text: str):
        self.set_font("Courier", "", 8)
        for line in text.split("\n"):
            line = line.rstrip()
            if not line:
                self.ln(2)
                continue
            # Check if line would overflow width (170mm usable)
            w = self.get_string_width(line)
            if w > 170:
                # Simple wrap at 160 chars
                while len(line) > 0:
                    chunk = line[:160]
                    self.cell(0, 3.8, S(chunk), ln=True)
                    line = line[160:]
            else:
                self.cell(0, 3.8, S(line), ln=True)
        self.ln(2)

    def code_block(self, text: str):
        self.set_font("Courier", "", 7)
        self.set_fill_color(240, 240, 240)
        for line in text.split("\n"):
            line = line.rstrip()
            if not line:
                self.ln(1.8)
                continue
            self.cell(0, 3.2, S("  " + line), fill=True, ln=True)
        self.set_fill_color(255, 255, 255)
        self.ln(2)

    def bullet(self, text: str, indent: int = 5):
        self.set_font("Helvetica", "", 9)
        x = self.get_x()
        self.cell(indent, 5, "")
        self.set_font("Courier", "", 8)
        self.cell(4, 5, S("-"))
        self.multi_cell(0, 4, S(text))

    def kv(self, key: str, val: str):
        self.set_font("Courier", "B", 8)
        self.cell(0, 4, S(f"  {key}: "), ln=False)
        self.set_font("Courier", "", 8)
        self.cell(0, 4, S(val), ln=True)


def build():
    pdf = ReportPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # ── Title page ──
    pdf.ln(30)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(30, 60, 114)
    pdf.cell(0, 12, "Backtesting & ML Training System", align="C", ln=True)
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 8, "Technical Reference Report", align="C", ln=True)
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 6, "Solana Meme Coin Signal Bot", align="C", ln=True)
    pdf.cell(0, 6, "Generated: June 2026", align="C", ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(15)

    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "Files analyzed:", ln=True)
    for f in ["backtesting/__init__.py", "backtesting/data_collector.py (1433 lines)",
              "backtesting/backtest_engine.py (920 lines)", "backtesting/ml_optimizer.py (854 lines)",
              "backtesting/backtest_report.py (848 lines)", "core/signal_engine.py (563 lines)",
              "models/optimal_parameters.json", "models/model_accuracy.json",
              "models/*.pkl (5 trained model files)"]:
        pdf.cell(10, 4, "")
        pdf.cell(0, 4, S(f"- {f}"), ln=True)

    # ════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("1. BACKTESTING MODULE OVERVIEW")
    pdf.body_text(
        "The backtesting module lives entirely in /backtesting/ and consists of 4 source files "
        "totaling ~4,055 lines. It simulates the live bot's full signal pipeline on historical "
        "data, then performs a grid-search across 336 parameter combinations to find the "
        "optimal trading configuration."
    )

    pdf.section_title("File Inventory")
    pdf.body_text(
        "backtesting/__init__.py         Package docstring only\n"
        "backtesting/data_collector.py    Multi-source historical data scraper (5 free APIs)\n"
        "backtesting/backtest_engine.py   Event-driven simulation + grid-search (336 combos)\n"
        "backtesting/ml_optimizer.py      XGBoost (2x + rug) + RandomForest training pipeline\n"
        "backtesting/backtest_report.py   Metrics aggregation, Telegram summary, JSON export"
    )

    # ════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("2. DATA COLLECTOR (data_collector.py)")
    pdf.section_title("Data Sources (all free)")
    pdf.body_text(
        "1. GeckoTerminal     - Paginate /networks/solana/pools (100 pages x 50 = 5,000+)\n"
        "2. DexScreener       - /latest/dex/search + /tokens/{mint} (50 pages)\n"
        "3. Birdeye           - /public/history_price 1-minute OHLCV (first hour)\n"
        "4. Helius REST       - /v0/addresses/{mint}/transactions (800k credit budget)\n"
        "5. Solscan           - /token/holders top-20 holder snapshot"
    )

    pdf.section_title("Collection Pipeline (method run())")
    pdf.body_text(
        "  Step 1: Collect pools from GeckoTerminal + DexScreener concurrently\n"
        "  Step 2: Deduplicate by mint address (prefer DexScreener data)\n"
        "  Step 3: For each unique token, run 3 enrichments IN PARALLEL:\n"
        "            - Birdeye OHLCV candles\n"
        "            - Helius chain transactions\n"
        "            - Solscan holder snapshot\n"
        "  Step 4: Compute outcome labels via _compute_outcome()\n"
        "  Step 5: Batch upsert into SQLite (500 rows/batch)"
    )

    pdf.section_title("Core Constants")
    pdf.code_block(
        "BACKTEST_DB_PATH   = data/backtest.db\n"
        "MAX_HELIUS_CREDITS = 800,000\n"
        "DEX_RATE_LIMIT     = 300 req/min\n"
        "BATCH_INSERT_SIZE  = 500 rows\n"
        "PARALLEL_REQUESTS  = 8\n"
        "MAX_PAGES_GECKO    = 100\n"
        "MAX_PAGES_DEX      = 50"
    )

    pdf.section_title("historical_tokens Table Schema")
    pdf.code_block(
        "Column                   Type    Description\n"
        "----------------------------------------------------------------------\n"
        "id                       INTEGER  PK autoincrement\n"
        "mint_address             TEXT     UNIQUE NOT NULL (Solana mint)\n"
        "symbol, name, source     TEXT     Basic metadata\n"
        "created_at               INTEGER  Unix timestamp of creation\n"
        "-- Initial state (T+0):\n"
        "initial_liquidity_usd    REAL     USD liquidity at launch\n"
        "initial_market_cap       REAL     FDV at launch\n"
        "initial_holders          INTEGER  Holder count at T+0\n"
        "first_buyer_count        INTEGER  Unique buyers in first 60s\n"
        "sniper_count             INTEGER  Wallets in first 3 blocks\n"
        "-- Price checkpoints:\n"
        "price_at_launch          REAL     T+0\n"
        "price_at_1min            REAL     T+1min\n"
        "price_at_5min            REAL     T+5min\n"
        "price_at_15min           REAL     T+15min\n"
        "price_at_30min           REAL     T+30min\n"
        "price_at_60min           REAL     T+60min\n"
        "price_at_24hr            REAL     T+24h\n"
        "max_price_ever           REAL     Peak price\n"
        "max_price_time_min       INTEGER  Time of peak (min from launch)\n"
        "-- Outcome labels:\n"
        "hit_2x, hit_5x, hit_10x INTEGER   1 if price hit 2x/5x/10x\n"
        "rug_pulled               INTEGER   1 if any checkpoint < 20% launch\n"
        "rug_time_min             INTEGER   When rug was detected\n"
        "final_outcome            TEXT      RUG/MOON/PUMP/DEAD/UNKNOWN\n"
        "-- On-chain signals:\n"
        "lp_burned                INTEGER   0/1\n"
        "mint_revoked             INTEGER   0/1\n"
        "top_holder_percent       REAL      Top holder concentration\n"
        "holder_velocity_1min     REAL      Unique buyers/min\n"
        "buy_sell_ratio_5min      REAL      Buy/sell ratio\n"
        "wash_trading_detected    INTEGER   0/1\n"
        "dev_cluster_detected     INTEGER   0/1 (top5 > 60%)\n"
        "wash_trading_score       REAL      0-100\n"
        "telegram_mentions        INTEGER   From social scanner\n"
        "dex_buy_volume_5min      REAL      Volume in first 5min\n"
        "price_change_1min        REAL      % change T+0 to T+1\n"
        "token_age_seconds        INTEGER   Age at collection time\n"
        "-- Metadata:\n"
        "data_complete            INTEGER   1 when all enrichments succeeded\n"
        "synthetic_data           INTEGER   1 when fallbacks used"
    )

    pdf.section_title("Outcome Classification (_compute_outcome)")
    pdf.body_text(
        "RUG   -> Any checkpoint price < 20% of launch price\n"
        "MOON  -> max_price >= 10x launch\n"
        "PUMP  -> max_price >= 2x launch\n"
        "DEAD  -> 24hr or 60min price < 50% of launch\n"
        "UNKNOWN -> None of the above"
    )

    # ════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("3. BACKTEST ENGINE (backtest_engine.py)")
    pdf.section_title("Grid-Search Parameter Space (336 combinations)")
    pdf.code_block(
        "SIGNAL_THRESHOLDS   = [0.60, 0.65, 0.70, 0.72, 0.75, 0.80, 0.85]  (7)\n"
        "POSITION_SIZES_SOL  = [0.02, 0.03, 0.04, 0.05]                     (4)\n"
        "STOP_LOSSES         = [0.20, 0.30, 0.40, 0.50]                     (4)\n"
        "TP2X_FRACTIONS      = [0.20, 0.25, 0.30]                           (3)\n"
        "Total: 7 x 4 x 4 x 3 = 336 combinations"
    )

    pdf.section_title("Key Dataclasses")
    pdf.code_block(
        "@dataclass\n"
        "class SimParams:\n"
        "    signal_threshold: float       # e.g. 0.72\n"
        "    position_size_sol: float      # e.g. 0.05\n"
        "    stop_loss_pct: float          # e.g. 0.30 = -30%\n"
        "    tp2x_fraction: float          # fraction to sell at 2x\n"
        "    tp5x_fraction: float = 0.25   # fraction to sell at 5x\n"
        "    tp10x_fraction: float = 0.25  # fraction to sell at 10x\n"
        "    trailing_fraction: float      # remainder on trailing stop\n\n"
        "@dataclass\n"
        "class SimTrade:\n"
        "    token_id, mint_address: int/str\n"
        "    signal_score: float           # 0-100\n"
        "    entry_price, exit_price: float\n"
        "    pnl_pct, pnl_sol: float\n"
        "    exit_reason: str              # take_profit_2x/trailing_stop/etc\n"
        "    hold_time_min: int\n"
        "    was_rug, hit_2x/5x/10x: bool\n"
        "    position_size_sol: float\n"
        "    params_id: int\n\n"
        "@dataclass\n"
        "class SimResult:\n"
        "    params: SimParams\n"
        "    total_trades, wins, losses: int\n"
        "    gross_profit, gross_loss: float\n"
        "    total_pnl_sol, max_drawdown_pct: float\n"
        "    sharpe_ratio, win_rate, profit_factor: float\n"
        "    avg_win_pct, avg_loss_pct: float"
    )

    pdf.section_title("Gate Logic (_passes_gates)")
    pdf.code_block(
        "def _passes_gates(token, threshold) -> (bool, str):\n"
        "    liq = initial_liquidity_usd\n"
        "    if liq < 10,000:  return False, \"liquidity_too_low\"\n"
        "    if dev_cluster:   return False, \"dev_cluster_critical\"\n"
        "    score = _reconstruct_signal_score(token)  # 0-100\n"
        "    if score < threshold * 100:  return False, \"score_too_low\"\n"
        "    return True, \"passed\""
    )

    pdf.section_title("Signal Score Reconstruction")
    pdf.body_text(
        "Weights are imported LIVE from core/signal_engine at import time via "
        "get_signal_weights(). Sub-scores are proxied from historical fields:"
    )
    pdf.code_block(
        "security_score  = lp_burned * 50  + mint_revoked * 50           # 0-100\n"
        "wallet_score    = 100 - dev_cluster * 40 - min(sniper_cnt*5,30) # 0-100\n"
        "rug_score       = max(0, 100 - top_holder_pct * 0.8)            # 0-100\n"
        "holder_vel_score= min(100, holder_velocity_1min * 10)           # 0-100\n"
        "smart_money_scr = max(0, 100 - (sniper_frac * 100))             # 0-100\n"
        "tx_pattern_scr  = clamp((bsr-0.5)/2.5*100, 0, 100)             # 0-100\n"
        "social_score    = min(100, telegram_mentions * 5)               # 0-100\n"
        "cross_dex_score = max(0, 100 - wash_trading_score)             # 0-100\n\n"
        "composite = sum(score_i * weight_i for all 8 components)"
    )

    pdf.section_title("Position Simulation (_simulate_position)")
    pdf.body_text(
        "Uses 5 price checkpoints: T+1, T+5, T+15, T+30, T+60 minutes.\n\n"
        "Exit logic evaluated AT EACH checkpoint:\n"
        "  1. Emergency stop  -> price < entry * (1 - stop_loss_pct)\n"
        "  2. TP ladder       -> price >= entry * 2/5/10x (partial fills)\n"
        "  3. Time stop       -> at T+30 with < 50% profit\n"
        "  4. Trailing stop   -> at T+60 with profit-dependent floor (70-85%)"
    )

    pdf.section_title("Sharpe Ratio Calculation")
    pdf.code_block(
        "def _calc_sharpe(pnl_series, risk_free=0.0) -> float:\n"
        "    arr  = np.array(pnl_series)\n"
        "    mean = np.mean(arr)\n"
        "    std  = np.std(arr, ddof=1)\n"
        "    if std == 0: return 0.0\n"
        "    # Annualised assuming ~20 trades/day\n"
        "    return (mean - risk_free) / std * sqrt(20 * 365)"
    )

    # ════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("4. ML TRAINING PIPELINE (ml_optimizer.py)")
    pdf.section_title("Models Trained")
    pdf.body_text(
        "Model           Algorithm        Target          Description\n"
        "xgb_2x          XGBClassifier    hit_2x          Will this token 2x within 30min?\n"
        "xgb_rug         XGBClassifier    rug_pulled      Will this token rug within 30min?\n"
        "random_forest   RandomForest     hit_2x          Feature importance ranking"
    )

    pdf.section_title("Libraries & Versions")
    pdf.code_block(
        "scikit-learn==1.5.0   StandardScaler, SimpleImputer, RandomForestClassifier, metrics\n"
        "xgboost==2.1.0        XGBClassifier\n"
        "joblib==1.4.0         Model serialization (.pkl files)\n"
        "numpy==1.26.4         Feature arrays, math\n"
        "pandas==2.2.0         Optional (numpy fallback)"
    )

    pdf.section_title("Feature Vector (13 base + 2 engineered = 15 total)")
    pdf.code_block(
        "FEATURE_COLUMNS:\n"
        "  [0]  initial_liquidity_usd     float  USD liquidity at launch\n"
        "  [1]  holder_velocity_1min      float  Unique buyers per minute\n"
        "  [2]  sniper_count              int    Wallets in first 3 blocks\n"
        "  [3]  buy_sell_ratio_5min       float  Buys/sells ratio\n"
        "  [4]  top_holder_percent        float  Top holder concentration\n"
        "  [5]  lp_burned                 0/1\n"
        "  [6]  mint_revoked              0/1\n"
        "  [7]  dev_cluster_detected      0/1\n"
        "  [8]  wash_trading_score        float  0-100\n"
        "  [9]  telegram_mentions         int\n"
        "  [10] token_age_seconds         int\n"
        "  [11] dex_buy_volume_5min       float\n"
        "  [12] price_change_1min         float  % change T+0 to T+1\n"
        "  -- Engineered (added by _engineer_features):\n"
        "  [13] liquidity_velocity_ratio  = liq / max(hvel, 0.01)\n"
        "  [14] risk_score                = sniper_count * top_holder_pct / 100"
    )

    pdf.section_title("Training Pipeline (async run())")
    pdf.code_block(
        "1. _load_data()\n"
        "     SELECT 15 columns FROM historical_tokens WHERE data_complete=1\n"
        "     Returns numpy arrays (X_raw, y_2x, y_rug, created_at)\n\n"
        "2. _engineer_features(X_raw)\n"
        "     Adds liquidity_velocity_ratio + risk_score (2 extra columns)\n\n"
        "3. _time_split(X, y, created_at)\n"
        "     70/15/15 CHRONOLOGICAL split (no data leakage)\n"
        "     Train = first 70%, Val = next 15%, Test = last 15%\n"
        "     Train+Val combined for final model fitting\n\n"
        "4. _fit_preprocessor(X_train)\n"
        "     SimpleImputer(strategy='median')\n"
        "     StandardScaler()\n"
        "     Fitted on TRAIN only, transforms train+test\n\n"
        "5. Train XGBoost 2x\n"
        "     XGBClassifier(n_estimators=500, max_depth=6, lr=0.01,\n"
        "       subsample=0.8, colsample_bytree=0.8,\n"
        "       scale_pos_weight=neg/pos, eval_metric='logloss')\n\n"
        "6. Train XGBoost rug\n"
        "     XGBClassifier(n_estimators=300, max_depth=4, lr=0.02,\n"
        "       subsample=0.8, colsample_bytree=0.8)\n\n"
        "7. Train RandomForest\n"
        "     RandomForestClassifier(n_estimators=200, max_depth=8,\n"
        "       class_weight='balanced')\n\n"
        "8. _save_feature_importance()  -> SQLite feature_importance table\n"
        "9. _save_models()              -> joblib.dump to models/*.pkl\n"
        "10. _write_accuracy_json()     -> models/model_accuracy.json"
    )

    pdf.section_title("Live Inference (predict())")
    pdf.code_block(
        "def predict(self, features: Dict[str, Any]) -> PredictionResult:\n"
        "    X = _features_from_dict(features)    # Build 15-col numpy array\n"
        "    X = _apply_preprocessor(X)             # Impute + scale\n"
        "    prob_2x  = model_2x.predict_proba(X)[1]  # Default 0.5\n"
        "    prob_rug = model_rug.predict_proba(X)[1] # Default 0.5\n"
        "    return PredictionResult(prob_2x, prob_rug, ml_available=True)"
    )

    pdf.section_title("Current Model Accuracy (models/model_accuracy.json)")
    pdf.code_block(
        "Trained on only 17 samples (14 train + 3 test):\n\n"
        "Model           Precision  Recall   F1      AUC     n_train\n"
        "xgb_2x          1.000      0.500    0.667   0.500   14\n"
        "xgb_rug         0.000      0.000    0.000   0.500   14\n"
        "random_forest   1.000      0.500    0.667   0.500   14"
    )

    pdf.section_title("Retrain Scheduler")
    pdf.code_block(
        "start_retrain_scheduler():\n"
        "    Background asyncio task (asyncio.create_task)\n"
        "    Retrains every 86,400s (24 hours)\n"
        "    Checks accuracy drift against prior run\n"
        "    Alerts via Telegram if F1 drops >5pp"
    )

    # ════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("5. BACKTEST REPORTER (backtest_report.py)")
    pdf.body_text(
        "Reads from backtest_trades + backtest_summary tables, computes full metrics, "
        "formats Telegram summary, exports optimal_parameters.json."
    )

    pdf.section_title("Key Dataclasses")
    pdf.code_block(
        "@dataclass\n"
        "class ExitBreakdown:\n"
        "    take_profit_2x, take_profit_5x, take_profit_10x: int\n"
        "    trailing_stop, emergency_stop, time_stop, other: int\n\n"
        "@dataclass\n"
        "class ScoreThresholdRow:\n"
        "    threshold: float     # signal score threshold\n"
        "    trades, wins: int\n"
        "    win_rate, avg_pnl_sol, profit_factor: float\n\n"
        "@dataclass\n"
        "class BacktestReport:\n"
        "    tokens_analyzed, total_trades: int\n"
        "    win_rate, avg_win_pct, avg_loss_pct: float\n"
        "    profit_factor, sharpe_ratio, max_drawdown_pct: float\n"
        "    starting_sol, ending_sol, total_pnl_sol, total_pnl_pct: float\n"
        "    optimal_score, optimal_pos_size, optimal_stop_loss: float\n"
        "    rugs_encountered, rugs_caught_pct, avg_rug_loss_pct: float\n"
        "    top_feature, rug_detection_acc: str/float\n"
        "    avg_hold_win_min, avg_hold_loss_min: float\n"
        "    exit_breakdown: ExitBreakdown\n"
        "    monthly_rows: list[MonthlyRow]\n"
        "    score_thresholds: list[ScoreThresholdRow]\n"
        "    telegram_summary: str"
    )

    pdf.section_title("Key Methods")
    pdf.code_block(
        "BacktestReporter:\n"
        "    generate(params_id=None) -> BacktestReport\n"
        "        Loads best params by Sharpe ratio\n"
        "        Recomputes equity curve + Sharpe from per-trade P&L\n"
        "        Computes rug analysis, monthly breakdown, score thresholds\n"
        "        Formats Telegram summary\n"
        "        Caches to report_cache table\n"
        "        Updates optimal_parameters.json\n\n"
        "    apply_optimal_params_to_settings() -> bool\n"
        "        Reads optimal_parameters.json\n"
        "        Writes MIN_SIGNAL_SCORE_FOR_BUY + MAX_POSITION_SIZE_SOL\n"
        "        into live config.settings at runtime"
    )

    # ════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("6. SIGNAL ENGINE INTEGRATION")
    pdf.section_title("Scoring Formula (signal_engine.py)")
    pdf.code_block(
        "Weights (sum to 1.00):\n"
        "  security_score         x 0.20\n"
        "  wallet_score           x 0.15\n"
        "  rug_score              x 0.15\n"
        "  holder_velocity_score  x 0.15\n"
        "  smart_money_score      x 0.15\n"
        "  tx_pattern_score       x 0.10\n"
        "  social_score           x 0.05\n"
        "  cross_dex_score        x 0.05"
    )

    pdf.section_title("Gate Evaluation Order")
    pdf.code_block(
        " 1. Social confidence boost (+10 pts if social_score > 60)\n"
        " 2. Security score gate       (< 70 -> fail)\n"
        " 3. Confidence gate           (< 65 -> fail, uses boosted value)\n"
        " 4. Liquidity gate            (< $10k -> fail)\n"
        " 5. Volume spike gate         (< 2x -> fail)\n"
        " 6. Wallet CRITICAL gate      (risk_level == CRITICAL -> fail)\n"
        " 7. Rug RUG gate              (recommendation == RUG -> fail)\n"
        " 8. Holder-velocity rug gate  (is_rug_warning -> fail)\n"
        " 9. Cross-DEX manipulation    (is_manipulated -> fail)\n"
        "10. Liquidity growth soft     (ARTIFICIAL_SPIKE -> warn, log only)\n"
        "11. TX pattern soft           (is_artificial_pump -> warn, log only)\n"
        "12. Composite score gate      (< 50/100 -> fail)"
    )

    pdf.section_title("ML Integration Point")
    pdf.code_block(
        "_get_ml_prediction() in signal_engine.py:131-166:\n\n"
        "    Lazy-imports MLOptimizer from backtesting.ml_optimizer\n"
        "    Calls ml_optimizer.predict(features_dict)\n"
        "    Returns PredictionResult or None (silent degrade)\n\n"
        "    If ML available:\n"
        "      - prob_rug > 0.70  -> adds fail_reason (BLOCKS signal)\n"
        "      - prob_2x  > 0.60  -> boosts composite by up to +5 pts\n\n"
        "    Features built from live analysis results:\n"
        "      - token_info.liquidity_usd\n"
        "      - holder_velocity.holder_velocity_score / 10\n"
        "      - wallet_analysis.sniper_count\n"
        "      - token_info.buy_sell_ratio / top_holders_pct / volume\n"
        "      - token_info.lp_burned / mint_revoked\n"
        "      - wallet_analysis.is_dev_cluster\n"
        "      - social_signals.telegram_mentions"
    )

    pdf.section_title("Weight Loading at Import Time")
    pdf.code_block(
        "signal_engine.py line 124:\n"
        "    load_weights_from_optimal_params()  # called at MODULE LEVEL\n\n"
        "Reads from models/optimal_parameters.json:\n"
        "    signal_weights dict  -> updates _W_SECURITY etc globals\n"
        "    min_signal_score    -> updates _MIN_COMPOSITE_SCORE\n\n"
        "Backtest engine also imports these weights via:\n"
        "    from core.signal_engine import get_signal_weights()"
    )

    # ════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("7. INTEGRATION POINTS FOR ML REPLACEMENT")

    pdf.section_title("Already Wired (pre-existing)")
    pdf.code_block(
        "File                          Function           What it does\n"
        "core/signal_engine.py:131     _get_ml_prediction  Calls ml_optimizer.predict()\n"
        "core/signal_engine.py:315-339 ML injection        Boosts composite / blocks rug\n"
        "backtesting/ml_optimizer.py   predict()           Feature pipeline + inference"
    )

    pdf.section_title("Files That Change for Full ML Replacement")
    pdf.code_block(
        "File                           Change\n"
        "----                           ------\n"
        "core/signal_engine.py          Replace evaluate_signal() weighted sum with\n"
        "                               model inference output; adjust gates\n\n"
        "backtesting/ml_optimizer.py    Add new targets (hit_5x, price_change_1h),\n"
        "                               tune hyperparams, stack models\n\n"
        "backtesting/data_collector.py  Add new features to historical_tokens schema\n"
        "                               + collection logic for new data sources\n\n"
        "backtesting/backtest_engine.py Update _reconstruct_signal_score() for new\n"
        "                               signal weights grid search targets\n\n"
        "trading/swap_engine.py         Replace fixed position size with ML Kelly\n"
        "                               fraction from PredictionResult\n\n"
        "database/sqlite_client.py      New tables for ML prediction persistence,\n"
        "                               predicted vs actual tracking\n\n"
        "models/model_accuracy.json     Auto-updated by _write_accuracy_json()"
    )

    pdf.section_title("Feature Engineering Notes")
    pdf.body_text(
        "The existing 15 features (13 base + 2 engineered) are already extracted by "
        "DataCollector from free APIs. The two engineered features are:\n\n"
        "  - liquidity_velocity_ratio = initial_liquidity_usd / max(holder_velocity_1min, 0.01)\n"
        "  - risk_score = sniper_count * top_holder_percent / 100\n\n"
        "New features would require new data sources:\n"
        "  - Real-time price change -> Birdeye websocket / Helius webhook\n"
        "  - Social features -> Twitter API v2 (requires $100/mo paid tier)\n"
        "  - Slippage -> Jupiter v6 quote API\n"
        "  - Cross-DEX pricing -> DexScreener multi-pool comparison"
    )

    # ════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("8. BACKTEST DATABASE SCHEMA (data/backtest.db)")

    pdf.section_title("Tables")
    pdf.code_block(
        "Table                    Rows  Description\n"
        "-----                    ----  -----------\n"
        "historical_tokens        N     All collected token data (schema in Ch.2)\n"
        "backtest_trades          N     One row per simulated trade\n"
        "backtest_params          ~336  Parameter combinations\n"
        "backtest_summary         ~336  Aggregated results per params_id\n"
        "monthly_performance      var   Per-month breakdown\n"
        "collection_progress      1     Data collector progress tracking\n"
        "feature_importance       var   ML feature importances across models\n"
        "ml_accuracy_log          var   ML model accuracy over time\n"
        "report_cache             1     Cached backtest report JSON"
    )

    pdf.section_title("backtest_trades Schema")
    pdf.code_block(
        "id, params_id, token_id, mint_address,\n"
        "signal_score, entry_price, exit_price,\n"
        "pnl_pct, pnl_sol, exit_reason,\n"
        "hold_time_min, was_rug,\n"
        "hit_2x, hit_5x, hit_10x,\n"
        "position_size_sol"
    )

    pdf.section_title("backtest_params Schema")
    pdf.code_block(
        "id, signal_threshold, position_size_sol,\n"
        "stop_loss_pct, tp2x_fraction, run_at"
    )

    # ════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("9. OPTIMAL PARAMETERS EXPORT")
    pdf.body_text(
        "The file models/optimal_parameters.json is the bridge between backtesting "
        "and live trading. Written by backtest_engine._export_optimal_params() and "
        "read by signal_engine.load_weights_from_optimal_params() at import time."
    )

    pdf.section_title("Current optimal_parameters.json")
    pdf.code_block(
        "{\n"
        '  "generated_at": 1780715391,\n'
        '  "backtest_summary": {\n'
        '    "total_trades": 0,\n'
        '    "win_rate": 0.0,\n'
        '    "sharpe_ratio": 0.0,\n'
        '    "profit_factor": 0.0,\n'
        '    "max_drawdown_pct": 0.0,\n'
        '    "total_pnl_sol": 0.0\n'
        "  },\n"
        '  "min_signal_score": 0.6,\n'
        '  "position_size_sol": 0.02,\n'
        '  "stop_loss_pct": 0.2,\n'
        '  "tp2x_fraction": 0.2,\n'
        '  "tp5x_fraction": 0.25,\n'
        '  "tp10x_fraction": 0.25,\n'
        '  "trailing_fraction": 0.3,\n'
        '  "position_size_kelly": 0.1,\n'
        '  "signal_weights": {\n'
        '    "security": 0.2, "wallet": 0.15, "rug": 0.15,\n'
        '    "holder_velocity": 0.15, "smart_money": 0.15,\n'
        '    "tx_pattern": 0.1, "social": 0.05, "cross_dex": 0.05\n'
        "  },\n"
        '  "top_feature": "liquidity_velocity_ratio",\n'
        '  "rug_detection_accuracy": 50.0\n'
        "}"
    )

    pdf.section_title("Saved Model Files (models/*.pkl)")
    pdf.code_block(
        "File                   Size     Content\n"
        "----                   ----     -------\n"
        "model_2x.pkl           384 KB   XGBoost 2x booster\n"
        "model_rug.pkl          225 KB   XGBoost rug booster\n"
        "model_rf.pkl           187 KB   RandomForest classifier\n"
        "scaler.pkl             927 B    Fitted StandardScaler\n"
        "imputer.pkl            563 B    Fitted SimpleImputer\n"
        "model_accuracy.json    735 B    Metrics for all models\n"
        "optimal_parameters.json 886 B   Best params + weights"
    )

    # ── Save ──
    pdf.output(str(OUTPUT))
    print(f"PDF report saved to: {OUTPUT}")


if __name__ == "__main__":
    build()
