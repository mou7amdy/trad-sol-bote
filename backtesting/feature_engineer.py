import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
from typing import Optional, Tuple


class FeatureEngineer:

    FEATURE_COLUMNS = [
        "initial_liquidity", "initial_mcap", "initial_volume_1h",
        "buy_sell_ratio", "tx_count_1h", "price_change_5m",
        "price_change_1h", "security_score", "top10_holders_pct",
        "lp_burned", "mint_revoked", "volume_spike_ratio",
        "liquidity_to_mcap_ratio", "age_minutes",
    ]

    def build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        df["volume_spike_ratio"] = (
            df["initial_volume_1h"] / df["initial_liquidity"].clip(1)
        )
        df["liquidity_to_mcap_ratio"] = (
            df["initial_liquidity"] / df["initial_mcap"].clip(1)
        )
        df["age_minutes"] = 0

        for col in self.FEATURE_COLUMNS:
            if col not in df.columns:
                df[col] = 0.0

        df[self.FEATURE_COLUMNS] = df[self.FEATURE_COLUMNS].fillna(
            df[self.FEATURE_COLUMNS].median()
        )

        return df[self.FEATURE_COLUMNS]

    def get_labels(self, df: pd.DataFrame, task: str = "pump") -> pd.Series:
        if task == "pump":
            return df["label_pump"]
        if task == "rug":
            return df["label_rug"]
        if task == "entry":
            return df["label_entry_minutes"]
        raise ValueError(f"Unknown task: {task}")

    def fit_transform(self, df: pd.DataFrame, scaler: Optional[MinMaxScaler] = None) -> Tuple[pd.DataFrame, MinMaxScaler]:
        X = self.build_features(df)
        if scaler is None:
            scaler = MinMaxScaler()
            X_scaled = scaler.fit_transform(X)
        else:
            X_scaled = scaler.transform(X)
        return pd.DataFrame(X_scaled, columns=self.FEATURE_COLUMNS), scaler
