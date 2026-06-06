# backtesting/ml_optimizer.py
"""
ML training pipeline for Solana meme coin signal prediction.

Trains three models on historical_tokens data:
  model_2x  — XGBoost: will this token hit 2x within 30 min? (binary)
  model_rug — XGBoost: will this token rug within 30 min? (binary)
  model_rf  — RandomForest: feature importance ranking

All models are time-split (no data leakage) and saved to models/.
A 24-hour retraining scheduler runs in the background.

Usage:
    optimizer = MLOptimizer()
    await optimizer.run()          # full train + evaluate + save
    probs = optimizer.predict(features_dict)  # live inference
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import numpy as np
from loguru import logger

# ---------------------------------------------------------------------------
# Optional heavy imports — degrade gracefully if not installed
# ---------------------------------------------------------------------------

try:
    import pandas as pd
    _PANDAS_OK = True
except ImportError:
    pd = None  # type: ignore
    _PANDAS_OK = False
    logger.warning("pandas not installed — MLOptimizer will use numpy fallbacks.")

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.impute import SimpleImputer
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import (
        precision_score, recall_score, f1_score, roc_auc_score,
        classification_report,
    )
    from sklearn.model_selection import cross_val_score
    _SKLEARN_OK = True
except ImportError:
    _SKLEARN_OK = False
    logger.warning("scikit-learn not installed — MLOptimizer disabled.")

try:
    from xgboost import XGBClassifier
    _XGB_OK = True
except Exception as e:
    _XGB_OK = False
    logger.warning(f"xgboost could not be loaded ({e}) — XGBoost models disabled.")

try:
    import joblib
    _JOBLIB_OK = True
except ImportError:
    joblib = None  # type: ignore
    _JOBLIB_OK = False
    logger.warning("joblib not installed — models will not be saved.")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BACKTEST_DB_PATH = Path(__file__).parent.parent / "data" / "backtest.db"
MODELS_DIR       = Path(__file__).parent.parent / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

MODEL_2X_PATH  = MODELS_DIR / "model_2x.pkl"
MODEL_RUG_PATH = MODELS_DIR / "model_rug.pkl"
MODEL_RF_PATH  = MODELS_DIR / "model_rf.pkl"
SCALER_PATH    = MODELS_DIR / "scaler.pkl"
IMPUTER_PATH   = MODELS_DIR / "imputer.pkl"
ACCURACY_PATH  = MODELS_DIR / "model_accuracy.json"

MIN_TRAINING_ROWS = 10   # reduced for testing/usability
RETRAIN_INTERVAL  = 86_400  # 24 hours

# ---------------------------------------------------------------------------
# Feature definition
# ---------------------------------------------------------------------------

FEATURE_COLUMNS = [
    "initial_liquidity_usd",
    "holder_velocity_1min",
    "sniper_count",
    "buy_sell_ratio_5min",
    "top_holder_percent",
    "lp_burned",           # boolean → 0/1
    "mint_revoked",        # boolean → 0/1
    "dev_cluster_detected",# boolean → 0/1
    "wash_trading_score",
    "telegram_mentions",
    "token_age_seconds",
    "dex_buy_volume_5min",
    "price_change_1min",
    # engineered
    "liquidity_velocity_ratio",
    "risk_score",
]

TARGET_2X  = "hit_2x"
TARGET_RUG = "rug_pulled"

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ModelMetrics:
    model_name: str
    n_train: int
    n_test: int
    precision: float
    recall: float
    f1: float
    roc_auc: float
    positive_rate: float   # class balance
    trained_at: int        # unix timestamp

    def to_dict(self) -> Dict:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"{self.model_name}: P={self.precision:.3f} R={self.recall:.3f} "
            f"F1={self.f1:.3f} AUC={self.roc_auc:.3f} "
            f"(train={self.n_train:,} test={self.n_test:,})"
        )


@dataclass
class PredictionResult:
    prob_2x: float     # probability of hitting 2x
    prob_rug: float    # probability of rug
    ml_available: bool = True


# ---------------------------------------------------------------------------
# MLOptimizer
# ---------------------------------------------------------------------------

class MLOptimizer:
    """
    XGBoost + RandomForest training pipeline with live inference.

    After training, call ``predict(features)`` in the live signal pipeline
    to inject ML probability scores.
    """

    def __init__(self) -> None:
        self._db: Optional[aiosqlite.Connection] = None
        self._model_2x  = None
        self._model_rug = None
        self._model_rf  = None
        self._scaler    = None
        self._imputer   = None
        self._metrics: Dict[str, ModelMetrics] = {}
        self._alert_fn  = None    # async fn(str) — Telegram alert
        self._last_trained = 0.0
        self._retrain_task: Optional[asyncio.Task] = None

        # Try loading pre-trained models
        self._try_load_models()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def set_alert_fn(self, fn) -> None:
        """Inject Telegram alert coroutine."""
        self._alert_fn = fn

    async def start(self) -> None:
        BACKTEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(BACKTEST_DB_PATH))
        self._db.row_factory = aiosqlite.Row
        await self._ensure_tables()
        logger.info("MLOptimizer: DB connection ready.")

    async def stop(self) -> None:
        if self._retrain_task:
            self._retrain_task.cancel()
        if self._db:
            await self._db.close()

    async def _ensure_tables(self) -> None:
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_importance (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name    TEXT NOT NULL,
                feature_name  TEXT NOT NULL,
                importance    REAL NOT NULL DEFAULT 0.0,
                trained_at    INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS ml_accuracy_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name  TEXT NOT NULL,
                precision   REAL NOT NULL DEFAULT 0.0,
                recall      REAL NOT NULL DEFAULT 0.0,
                f1          REAL NOT NULL DEFAULT 0.0,
                roc_auc     REAL NOT NULL DEFAULT 0.0,
                n_train     INTEGER NOT NULL DEFAULT 0,
                n_test      INTEGER NOT NULL DEFAULT 0,
                logged_at   INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        await self._db.commit()

    # ── data loading ───────────────────────────────────────────────────────

    async def _load_data(self) -> Tuple[Optional[Any], Optional[Any], Optional[Any], Optional[Any]]:
        """
        Load and preprocess historical_tokens into feature matrix X and targets y.

        Returns (X_train, X_test, y_2x_train, y_2x_test, y_rug_train, y_rug_test)
        packed as a tuple of (X_tr, X_te, y2x_tr, y2x_te, yrug_tr, yrug_te).
        For simplicity, returns a 4-tuple: (X_arr, y_2x_arr, y_rug_arr, created_at_arr).
        """
        async with self._db.execute(
            """
            SELECT
                initial_liquidity_usd, holder_velocity_1min, sniper_count,
                buy_sell_ratio_5min, top_holder_percent, lp_burned, mint_revoked,
                dev_cluster_detected, wash_trading_score, telegram_mentions,
                token_age_seconds, dex_buy_volume_5min, price_change_1min,
                hit_2x, rug_pulled, created_at
            FROM historical_tokens
            WHERE data_complete = 1
              AND price_at_launch > 0
            ORDER BY created_at ASC
            """
        ) as cur:
            rows = await cur.fetchall()

        if len(rows) < MIN_TRAINING_ROWS:
            logger.warning(
                f"MLOptimizer: only {len(rows)} rows available "
                f"(need {MIN_TRAINING_ROWS}). Training skipped."
            )
            return None, None, None, None

        logger.info(f"MLOptimizer: loaded {len(rows):,} rows for training.")

        # Build numpy arrays
        raw_features = np.array([
            [
                float(r["initial_liquidity_usd"] or 0),
                float(r["holder_velocity_1min"]   or 0),
                float(r["sniper_count"]            or 0),
                float(r["buy_sell_ratio_5min"]     or 1.0),
                float(r["top_holder_percent"]      or 0),
                float(r["lp_burned"]               or 0),
                float(r["mint_revoked"]            or 0),
                float(r["dev_cluster_detected"]    or 0),
                float(r["wash_trading_score"]      or 0),
                float(r["telegram_mentions"]       or 0),
                float(r["token_age_seconds"]       or 0),
                float(r["dex_buy_volume_5min"]     or 0),
                float(r["price_change_1min"]       or 0),
            ]
            for r in rows
        ], dtype=np.float32)

        y_2x  = np.array([int(r["hit_2x"]    or 0) for r in rows], dtype=np.int32)
        y_rug = np.array([int(r["rug_pulled"] or 0) for r in rows], dtype=np.int32)
        created_at = np.array([int(r["created_at"] or 0) for r in rows], dtype=np.int64)

        return raw_features, y_2x, y_rug, created_at

    # ── feature engineering ────────────────────────────────────────────────

    def _engineer_features(self, X_raw: "np.ndarray") -> "np.ndarray":
        """
        Add two interaction features:
          liquidity_velocity_ratio = liquidity / max(holder_velocity, 0.01)
          risk_score               = sniper_count * top_holder_percent / 100
        """
        liq  = X_raw[:, 0]   # initial_liquidity_usd
        hvel = X_raw[:, 1]   # holder_velocity_1min
        snip = X_raw[:, 2]   # sniper_count
        toph = X_raw[:, 4]   # top_holder_percent

        liq_vel_ratio = liq / np.maximum(hvel, 0.01)
        risk_score    = snip * toph / 100.0

        return np.column_stack([X_raw, liq_vel_ratio, risk_score])

    def _time_split(
        self,
        X: "np.ndarray",
        y: "np.ndarray",
        created_at: "np.ndarray",
    ) -> Tuple["np.ndarray", "np.ndarray", "np.ndarray", "np.ndarray"]:
        """
        70/15/15 time-based split.  Returns (X_train, X_test, y_train, y_test).
        Uses the 85th percentile of created_at as the train/test boundary
        (validation set is embedded in train for early stopping).
        """
        n = len(X)
        split_train = int(n * 0.70)
        split_test  = int(n * 0.85)

        X_train = X[:split_train]
        X_val   = X[split_train:split_test]
        X_test  = X[split_test:]
        y_train = y[:split_train]
        y_val   = y[split_train:split_test]
        y_test  = y[split_test:]

        # Combine train+val for final model (standard practice)
        X_tr = np.vstack([X_train, X_val])
        y_tr = np.concatenate([y_train, y_val])

        return X_tr, X_test, y_tr, y_test

    # ── preprocessing ─────────────────────────────────────────────────────

    def _fit_preprocessor(self, X_train: "np.ndarray") -> "np.ndarray":
        """Fit imputer + scaler on training data, transform and return."""
        if not _SKLEARN_OK:
            return X_train
        self._imputer = SimpleImputer(strategy="median")
        self._scaler  = StandardScaler()
        X_imp = self._imputer.fit_transform(X_train)
        X_sc  = self._scaler.fit_transform(X_imp)
        return X_sc

    def _apply_preprocessor(self, X: "np.ndarray") -> "np.ndarray":
        if not _SKLEARN_OK or self._imputer is None or self._scaler is None:
            return X
        return self._scaler.transform(self._imputer.transform(X))

    # ── model training ─────────────────────────────────────────────────────

    def _train_xgb_classifier(
        self,
        X_train: "np.ndarray",
        y_train: "np.ndarray",
        X_test: "np.ndarray",
        y_test: "np.ndarray",
        name: str,
        n_estimators: int = 500,
        max_depth: int = 6,
        learning_rate: float = 0.01,
        subsample: float = 0.8,
    ) -> Tuple[Any, ModelMetrics]:
        """Train an XGBoost classifier and return (model, metrics)."""
        if not _XGB_OK or not _SKLEARN_OK:
            logger.warning(f"MLOptimizer: XGBoost unavailable — skipping {name}.")
            return None, None  # type: ignore

        pos_count = int(y_train.sum())
        neg_count = int(len(y_train) - pos_count)
        scale_pos = neg_count / max(pos_count, 1)

        model = XGBClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            subsample=subsample,
            colsample_bytree=0.8,
            scale_pos_weight=scale_pos,
            eval_metric="logloss",
            use_label_encoder=False,
            n_jobs=-1,
            random_state=42,
            tree_method="hist",   # fast for large datasets
        )
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            verbose=False,
        )

        y_pred      = model.predict(X_test)
        y_prob      = model.predict_proba(X_test)[:, 1]
        precision   = float(precision_score(y_test, y_pred, zero_division=0))
        recall      = float(recall_score(y_test, y_pred, zero_division=0))
        f1          = float(f1_score(y_test, y_pred, zero_division=0))
        try:
            auc = float(roc_auc_score(y_test, y_prob))
        except Exception:
            auc = 0.5

        metrics = ModelMetrics(
            model_name=name,
            n_train=len(X_train),
            n_test=len(X_test),
            precision=precision,
            recall=recall,
            f1=f1,
            roc_auc=auc,
            positive_rate=float(y_test.mean()),
            trained_at=int(time.time()),
        )
        logger.info(f"MLOptimizer: {metrics.summary()}")
        return model, metrics

    def _train_random_forest(
        self,
        X_train: "np.ndarray",
        y_train: "np.ndarray",
        X_test: "np.ndarray",
        y_test: "np.ndarray",
    ) -> Tuple[Any, ModelMetrics]:
        """Train RandomForest for feature importance analysis."""
        if not _SKLEARN_OK:
            return None, None  # type: ignore

        model = RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            class_weight="balanced",
            n_jobs=-1,
            random_state=42,
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_prob = model.predict_proba(X_test)[:, 1]
        precision = float(precision_score(y_test, y_pred, zero_division=0))
        recall    = float(recall_score(y_test, y_pred, zero_division=0))
        f1        = float(f1_score(y_test, y_pred, zero_division=0))
        try:
            auc = float(roc_auc_score(y_test, y_prob))
        except Exception:
            auc = 0.5

        metrics = ModelMetrics(
            model_name="random_forest",
            n_train=len(X_train),
            n_test=len(X_test),
            precision=precision,
            recall=recall,
            f1=f1,
            roc_auc=auc,
            positive_rate=float(y_test.mean()),
            trained_at=int(time.time()),
        )
        logger.info(f"MLOptimizer: {metrics.summary()}")
        return model, metrics

    # ── feature importance ─────────────────────────────────────────────────

    async def _save_feature_importance(
        self, model: Any, model_name: str
    ) -> None:
        """Persist feature importances to DB."""
        if model is None:
            return
        feature_names = FEATURE_COLUMNS[: -2]  # base features (no engineered)
        full_names    = feature_names + ["liquidity_velocity_ratio", "risk_score"]

        importances: List[float] = []
        if hasattr(model, "feature_importances_"):
            importances = list(model.feature_importances_)
        else:
            return

        now = int(time.time())
        await self._db.executemany(
            """
            INSERT INTO feature_importance
                (model_name, feature_name, importance, trained_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                (model_name, name, float(imp), now)
                for name, imp in zip(full_names, importances)
            ],
        )
        await self._db.commit()

        # Log top-5 features
        sorted_pairs = sorted(
            zip(full_names, importances), key=lambda x: x[1], reverse=True
        )
        top5 = sorted_pairs[:5]
        logger.info(
            f"MLOptimizer [{model_name}] top features: "
            + ", ".join(f"{n}={v:.4f}" for n, v in top5)
        )

    # ── metrics persistence ────────────────────────────────────────────────

    async def _save_metrics(self, metrics: ModelMetrics) -> None:
        self._metrics[metrics.model_name] = metrics
        await self._db.execute(
            """
            INSERT INTO ml_accuracy_log
                (model_name, precision, recall, f1, roc_auc, n_train, n_test, logged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metrics.model_name, metrics.precision, metrics.recall,
                metrics.f1, metrics.roc_auc, metrics.n_train, metrics.n_test,
                metrics.trained_at,
            ),
        )
        await self._db.commit()

        # Persist JSON accuracy file for Telegram /model_accuracy
        self._write_accuracy_json()

    def _write_accuracy_json(self) -> None:
        payload = {
            name: m.to_dict()
            for name, m in self._metrics.items()
        }
        try:
            ACCURACY_PATH.write_text(json.dumps(payload, indent=2))
        except Exception as exc:
            logger.error(f"MLOptimizer: failed to write accuracy JSON: {exc}")

    # ── model persistence ──────────────────────────────────────────────────

    def _save_models(self) -> None:
        if not _JOBLIB_OK:
            logger.warning("MLOptimizer: joblib not available — models not saved.")
            return
        for path, obj in [
            (MODEL_2X_PATH,  self._model_2x),
            (MODEL_RUG_PATH, self._model_rug),
            (MODEL_RF_PATH,  self._model_rf),
            (SCALER_PATH,    self._scaler),
            (IMPUTER_PATH,   self._imputer),
        ]:
            if obj is not None:
                try:
                    joblib.dump(obj, path)
                    logger.info(f"MLOptimizer: saved {path.name}")
                except Exception as exc:
                    logger.error(f"MLOptimizer: failed to save {path}: {exc}")

    def _try_load_models(self) -> None:
        if not _JOBLIB_OK:
            return
        for path, attr in [
            (MODEL_2X_PATH,  "_model_2x"),
            (MODEL_RUG_PATH, "_model_rug"),
            (MODEL_RF_PATH,  "_model_rf"),
            (SCALER_PATH,    "_scaler"),
            (IMPUTER_PATH,   "_imputer"),
        ]:
            if path.exists():
                try:
                    setattr(self, attr, joblib.load(path))
                    logger.info(f"MLOptimizer: loaded {path.name}")
                except Exception as exc:
                    logger.warning(f"MLOptimizer: could not load {path}: {exc}")

        if ACCURACY_PATH.exists():
            try:
                raw = json.loads(ACCURACY_PATH.read_text())
                for name, d in raw.items():
                    self._metrics[name] = ModelMetrics(**d)
            except Exception:
                pass

    # ── main training pipeline ─────────────────────────────────────────────

    async def run(self) -> Dict[str, ModelMetrics]:
        """
        Full training pipeline:
          1. Load + preprocess data
          2. Time-split
          3. Train XGBoost 2x model
          4. Train XGBoost rug model
          5. Train RandomForest
          6. Save feature importances + accuracy
          7. Save models to disk
        Returns dict of model_name → ModelMetrics.
        """
        if not _SKLEARN_OK or not _XGB_OK:
            logger.error(
                "MLOptimizer: scikit-learn or xgboost not installed. "
                "Install with: pip install scikit-learn xgboost"
            )
            return {}

        logger.info("MLOptimizer: starting training pipeline...")
        X_raw, y_2x, y_rug, created_at = await self._load_data()
        if X_raw is None:
            return {}

        # Feature engineering
        X_eng = self._engineer_features(X_raw)

        # Time-based split
        X_tr_raw, X_te_raw, y_2x_tr, y_2x_te = self._time_split(X_eng, y_2x, created_at)
        _,        _,         y_rug_tr, y_rug_te = self._time_split(X_eng, y_rug, created_at)

        # Preprocessing (fit on train only)
        X_tr = self._fit_preprocessor(X_tr_raw)
        X_te = self._apply_preprocessor(X_te_raw)

        results: Dict[str, ModelMetrics] = {}

        # --- Model 1: 2x classifier ---
        logger.info("MLOptimizer: training XGBoost 2x model...")
        model_2x, metrics_2x = self._train_xgb_classifier(
            X_tr, y_2x_tr, X_te, y_2x_te,
            name="xgb_2x",
            n_estimators=500, max_depth=6,
            learning_rate=0.01, subsample=0.8,
        )
        if model_2x and metrics_2x:
            self._model_2x = model_2x
            await self._save_metrics(metrics_2x)
            await self._save_feature_importance(model_2x, "xgb_2x")
            results["xgb_2x"] = metrics_2x

        # --- Model 2: rug classifier ---
        logger.info("MLOptimizer: training XGBoost rug model...")
        model_rug, metrics_rug = self._train_xgb_classifier(
            X_tr, y_rug_tr, X_te, y_rug_te,
            name="xgb_rug",
            n_estimators=300, max_depth=4,
            learning_rate=0.02, subsample=0.8,
        )
        if model_rug and metrics_rug:
            self._model_rug = model_rug
            await self._save_metrics(metrics_rug)
            await self._save_feature_importance(model_rug, "xgb_rug")
            results["xgb_rug"] = metrics_rug

        # --- Model 3: RandomForest (feature importance) ---
        logger.info("MLOptimizer: training RandomForest...")
        model_rf, metrics_rf = self._train_random_forest(
            X_tr, y_2x_tr, X_te, y_2x_te
        )
        if model_rf and metrics_rf:
            self._model_rf = model_rf
            await self._save_metrics(metrics_rf)
            await self._save_feature_importance(model_rf, "random_forest")
            results["random_forest"] = metrics_rf

        # Save all to disk
        self._save_models()
        self._last_trained = time.time()

        logger.info(
            "MLOptimizer: training complete. "
            + " | ".join(m.summary() for m in results.values())
        )
        return results

    # ── live inference ─────────────────────────────────────────────────────

    def _features_from_dict(self, features: Dict[str, Any]) -> Optional["np.ndarray"]:
        """Convert a dict of token features to the model's feature vector."""
        base = np.array([[
            float(features.get("initial_liquidity_usd", 0)),
            float(features.get("holder_velocity_1min",  0)),
            float(features.get("sniper_count",          0)),
            float(features.get("buy_sell_ratio_5min",   1.0)),
            float(features.get("top_holder_percent",    0)),
            float(features.get("lp_burned",             0)),
            float(features.get("mint_revoked",          0)),
            float(features.get("dev_cluster_detected",  0)),
            float(features.get("wash_trading_score",    0)),
            float(features.get("telegram_mentions",     0)),
            float(features.get("token_age_seconds",     0)),
            float(features.get("dex_buy_volume_5min",   0)),
            float(features.get("price_change_1min",     0)),
        ]], dtype=np.float32)
        return self._engineer_features(base)

    def predict(self, features: Dict[str, Any]) -> PredictionResult:
        """
        Run ML inference on a live token's features.

        Safe to call even when models aren't loaded (returns neutral probs).
        """
        if not _SKLEARN_OK or not _XGB_OK:
            return PredictionResult(prob_2x=0.5, prob_rug=0.5, ml_available=False)

        X = self._features_from_dict(features)
        X_proc = self._apply_preprocessor(X)

        prob_2x = 0.5
        prob_rug = 0.5

        if self._model_2x is not None:
            try:
                prob_2x = float(self._model_2x.predict_proba(X_proc)[0][1])
            except Exception as exc:
                logger.debug(f"ML predict_2x error: {exc}")

        if self._model_rug is not None:
            try:
                prob_rug = float(self._model_rug.predict_proba(X_proc)[0][1])
            except Exception as exc:
                logger.debug(f"ML predict_rug error: {exc}")

        return PredictionResult(
            prob_2x=prob_2x,
            prob_rug=prob_rug,
            ml_available=True,
        )

    # ── accuracy monitoring + retrain ──────────────────────────────────────

    async def _check_accuracy_drift(
        self, new_metrics: Dict[str, ModelMetrics]
    ) -> None:
        """
        Compare new accuracy against stored baseline.
        Alert via Telegram if any metric drops > 5 percentage points.
        """
        if not ACCURACY_PATH.exists() or not self._alert_fn:
            return
        try:
            old = json.loads(ACCURACY_PATH.read_text())
        except Exception:
            return

        alerts = []
        for name, m in new_metrics.items():
            old_m = old.get(name, {})
            old_f1 = float(old_m.get("f1", m.f1))
            drop = old_f1 - m.f1
            if drop > 0.05:
                alerts.append(
                    f"⚠️ {name}: F1 dropped {drop:.1%} "
                    f"({old_f1:.3f} → {m.f1:.3f})"
                )

        if alerts:
            msg = (
                "🚨 *ML Model Accuracy Alert*\n"
                + "\n".join(alerts)
                + "\nConsider retraining with more data."
            )
            try:
                await self._alert_fn(msg)
            except Exception as exc:
                logger.error(f"MLOptimizer: alert failed: {exc}")

    async def start_retrain_scheduler(self) -> None:
        """
        Background task: retrain models every RETRAIN_INTERVAL seconds.
        Call this once at bot startup.
        """
        async def _loop() -> None:
            while True:
                await asyncio.sleep(RETRAIN_INTERVAL)
                logger.info("MLOptimizer: scheduled retrain starting...")
                try:
                    metrics = await self.run()
                    await self._check_accuracy_drift(metrics)
                except Exception as exc:
                    logger.error(f"MLOptimizer: retrain failed: {exc}")

        self._retrain_task = asyncio.create_task(_loop())
        logger.info(f"MLOptimizer: retrain scheduler started (interval={RETRAIN_INTERVAL}s).")

    # ── feature importance report ──────────────────────────────────────────

    async def get_feature_importance_report(self) -> str:
        """Return a formatted feature importance string for Telegram."""
        if not self._db:
            return "ML models not trained yet."
        try:
            async with self._db.execute(
                """
                SELECT feature_name, AVG(importance) AS avg_imp
                FROM feature_importance
                GROUP BY feature_name
                ORDER BY avg_imp DESC
                LIMIT 10
                """
            ) as cur:
                rows = await cur.fetchall()
            if not rows:
                return "No feature importance data yet."
            lines = ["🔬 *Feature Importance (avg across models)*\n"]
            for i, row in enumerate(rows, 1):
                bar = "█" * int(float(row["avg_imp"]) * 20)
                lines.append(
                    f"{i:2}. `{row['feature_name']:<30}` {bar} {float(row['avg_imp']):.4f}"
                )
            return "\n".join(lines)
        except Exception as exc:
            logger.error(f"get_feature_importance_report error: {exc}")
            return "Error fetching feature importance."

    async def get_accuracy_message(self) -> str:
        """Return a Telegram-ready accuracy summary."""
        if not self._metrics:
            return "📭 *ML models not trained yet.*\nRun `/optimize` to start training."

        lines = ["🤖 *ML Model Accuracy*\n"]
        for name, m in self._metrics.items():
            lines.append(
                f"*{name}*\n"
                f"  Precision: `{m.precision:.3f}` | Recall: `{m.recall:.3f}`\n"
                f"  F1: `{m.f1:.3f}` | AUC: `{m.roc_auc:.3f}`\n"
                f"  Trained on: `{m.n_train:,}` samples\n"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton (used by signal_engine for live inference)
# ---------------------------------------------------------------------------

ml_optimizer = MLOptimizer()


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Train ML models on historical token data")
    parser.add_argument("--retrain", action="store_true", help="Force retrain even if models exist")
    args = parser.parse_args()

    async def main() -> None:
        opt = MLOptimizer()
        await opt.start()
        if args.retrain or not MODEL_2X_PATH.exists():
            metrics = await opt.run()
            if metrics:
                print("\nTraining complete:")
                for name, m in metrics.items():
                    print(f"  {m.summary()}")
            else:
                print("Training failed — check logs.")
        else:
            print("Models already exist. Use --retrain to force retraining.")
        await opt.stop()

    asyncio.run(main())
