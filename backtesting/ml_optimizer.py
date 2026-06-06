"""
Loads trained models and provides predictions for signal_engine.
This REPLACES the current rule-based thresholds when models are available.
"""

import joblib
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from loguru import logger

from config.settings import BASE_DIR
from core.solana_scanner import TokenInfo
from core.token_analyzer import AnalysisResult
from security.scanner import SecurityResult


@dataclass
class MLPrediction:
    pump_probability: float
    rug_probability: float
    entry_minutes: int
    ml_score: float
    should_signal: bool


class MLOptimizer:
    def __init__(self) -> None:
        self.pump_model: Any = None
        self.rug_model: Any = None
        self.entry_model: Any = None
        self.scaler: Any = None
        self._loaded: bool = False

    def load_models(self) -> bool:
        if self._loaded:
            return True
        path = Path(BASE_DIR / "models")
        try:
            pump_path = path / "pump_classifier.pkl"
            rug_path = path / "rug_detector.pkl"
            entry_path = path / "entry_predictor.pkl"
            scaler_path = path / "feature_scaler.pkl"

            if not pump_path.exists():
                logger.warning("ML models not found — using rule-based fallback")
                return False

            self.pump_model = joblib.load(pump_path)
            self.rug_model = joblib.load(rug_path) if rug_path.exists() else None
            self.entry_model = joblib.load(entry_path) if entry_path.exists() else None
            self.scaler = joblib.load(scaler_path) if scaler_path.exists() else None
            self._loaded = True
            logger.info("MLOptimizer: all models loaded successfully")
            return True
        except Exception as exc:
            logger.warning(f"MLOptimizer: failed to load models: {exc}")
            return False

    def _build_feature_vector(
        self,
        token_info: TokenInfo,
        analysis_result: AnalysisResult,
        security_result: SecurityResult,
        wallet_analysis: Any,
    ) -> np.ndarray:
        features = np.array([[
            float(getattr(token_info, "liquidity_usd", 0) or 0),
            float(getattr(token_info, "market_cap", 0) or 0),
            float(getattr(analysis_result, "volume_5m", 0) or 0),
            float(getattr(token_info, "buy_sell_ratio", 1.0) or 1.0),
            int(getattr(analysis_result, "tx_count", 0) or 0),
            float(getattr(analysis_result, "price_change_5m", 0) or 0),
            float(getattr(analysis_result, "price_change_1h", 0) or 0),
            float(getattr(security_result, "score", 0) or 0),
            float(getattr(token_info, "top10_holders_pct", 0) or 0),
            float(int(getattr(token_info, "lp_burned", False) or False)),
            float(int(getattr(token_info, "mint_revoked", False) or False)),
            float(getattr(analysis_result, "volume_spike_ratio", 1.0) or 1.0),
            (float(getattr(token_info, "liquidity_usd", 1) or 1)
             / max(float(getattr(token_info, "market_cap", 1) or 1), 1)),
            float(getattr(token_info, "age_minutes", 0) or 0),
        ]])
        return features

    def predict(
        self,
        token_info: TokenInfo,
        analysis_result: AnalysisResult,
        security_result: SecurityResult,
        wallet_analysis: Any,
    ) -> Optional[MLPrediction]:
        if not self._loaded:
            return None

        try:
            raw = self._build_feature_vector(
                token_info, analysis_result, security_result, wallet_analysis
            )
            X = self.scaler.transform(raw) if self.scaler else raw

            pump_prob = float(self.pump_model.predict_proba(X)[0][1])

            rug_prob = 0.5
            if self.rug_model:
                rug_score = float(self.rug_model.decision_function(X)[0])
                rug_prob = 1.0 / (1.0 + np.exp(-rug_score))

            entry_min = 0
            if self.entry_model:
                entry_min = max(0, int(round(float(self.entry_model.predict(X)[0]))))

            ml_score = (pump_prob * 60.0) + ((1.0 - rug_prob) * 40.0)

            should_signal = pump_prob >= 0.6 and rug_prob <= 0.3 and ml_score >= 0.65

            return MLPrediction(
                pump_probability=pump_prob,
                rug_probability=rug_prob,
                entry_minutes=entry_min,
                ml_score=ml_score * 100.0,
                should_signal=should_signal,
            )
        except Exception as exc:
            logger.error(f"MLOptimizer predict error: {exc}")
            return None
