"""
Trains 3 models and saves them to models/ directory.
Run: python -m backtesting.train_models
"""

import asyncio
import sqlite3
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from loguru import logger
from sklearn.ensemble import IsolationForest, RandomForestClassifier, GradientBoostingRegressor
from sklearn.metrics import classification_report, roc_auc_score, mean_absolute_error
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler

from config.settings import BASE_DIR
from backtesting.feature_engineer import FeatureEngineer

MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

BACKTEST_DB_PATH = BASE_DIR / "data" / "backtest.db"


class ModelTrainer:

    def load_data(self) -> pd.DataFrame:
        if not BACKTEST_DB_PATH.exists():
            logger.error(f"Backtest DB not found at {BACKTEST_DB_PATH}")
            return pd.DataFrame()
        conn = sqlite3.connect(str(BACKTEST_DB_PATH))
        df = pd.read_sql_query(
            "SELECT * FROM historical_tokens WHERE label_pump IS NOT NULL AND initial_price > 0",
            conn,
        )
        conn.close()
        logger.info(f"Loaded {len(df)} labeled rows")
        return df

    def train_pump_classifier(self, X: np.ndarray, y: np.ndarray) -> object:
        import xgboost as xgb
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
            scale_pos_weight=len(y[y == 0]) / max(len(y[y == 1]), 1),
            eval_metric="auc",
            early_stopping_rounds=20,
            random_state=42,
            use_label_encoder=False,
        )
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            verbose=False,
        )
        y_pred = model.predict(X_test)
        y_prob = model.predict_proba(X_test)[:, 1]
        print("PUMP MODEL:")
        print(classification_report(y_test, y_pred))
        print(f"AUC: {roc_auc_score(y_test, y_prob):.3f}")
        joblib.dump(model, MODELS_DIR / "pump_classifier.pkl")
        logger.info("Saved pump_classifier.pkl")
        return model

    def train_rug_detector(self, X: np.ndarray, y: np.ndarray) -> object:
        X_clean = X[y == 0]
        model = IsolationForest(
            n_estimators=200, contamination=0.1, random_state=42
        )
        model.fit(X_clean)

        scores = model.decision_function(X)
        rug_mean = scores[y == 1].mean() if y.sum() > 0 else 0
        clean_mean = scores[y == 0].mean()
        print(f"RUG DETECTOR - Mean score rugs: {rug_mean:.3f} vs clean: {clean_mean:.3f}")
        joblib.dump(model, MODELS_DIR / "rug_detector.pkl")
        logger.info("Saved rug_detector.pkl")
        return model

    def train_entry_predictor(self, X: np.ndarray, y: np.ndarray) -> object:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        model = GradientBoostingRegressor(
            n_estimators=150, max_depth=4, learning_rate=0.05, random_state=42
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        mae = mean_absolute_error(y_test, y_pred)
        print(f"ENTRY MODEL - MAE: {mae:.1f} minutes")
        joblib.dump(model, MODELS_DIR / "entry_predictor.pkl")
        logger.info("Saved entry_predictor.pkl")
        return model

    def save_scaler(self, scaler: MinMaxScaler) -> None:
        joblib.dump(scaler, MODELS_DIR / "feature_scaler.pkl")
        logger.info("Saved feature_scaler.pkl")

    def run(self) -> None:
        df = self.load_data()
        if df.empty:
            logger.error("No training data available")
            return
        print(f"Dataset: {len(df)} labeled tokens")

        fe = FeatureEngineer()
        X = fe.build_features(df)
        scaler = MinMaxScaler()
        X_scaled = scaler.fit_transform(X)
        self.save_scaler(scaler)

        y_pump = fe.get_labels(df, "pump").values.astype(int)
        y_rug = fe.get_labels(df, "rug").values.astype(int)
        y_entry = fe.get_labels(df, "entry").values.astype(int)

        self.train_pump_classifier(X_scaled, y_pump)
        self.train_rug_detector(X_scaled, y_rug)
        self.train_entry_predictor(X_scaled, y_entry)
        print("All models saved to models/")


def main() -> None:
    trainer = ModelTrainer()
    trainer.run()


if __name__ == "__main__":
    main()
