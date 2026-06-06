import os
from dataclasses import dataclass
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# Get the project root directory
BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class RuntimeState:
    """Mutable runtime state — never mutate Settings fields directly at runtime."""
    enable_auto_buy: bool = False
    min_signal_score_for_buy: float = 70.0
    max_position_size_sol: float = 0.1


runtime_state = RuntimeState()

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    TELEGRAM_TOKEN: str = Field(default="your_telegram_token_here")
    ADMIN_CHAT_ID: str = Field(default="your_telegram_chat_id_here")
    SOLANA_RPC_URL: str = Field(default="https://api.mainnet-beta.solana.com")
    SOLANA_WSS_URL: str = Field(default="wss://api.mainnet-beta.solana.com")
    GOPLUS_API_KEY: str = Field(default="your_goplus_api_key_here")
    HONEYPOT_API_KEY: str = Field(default="your_honeypot_api_key_here")
    SQLITE_DB_PATH: str = Field(default=str(BASE_DIR / "data" / "bot.db"))
    DATABASE_URL: str = Field(default="")

    # --- Enhancement settings ---
    # Twitter / X API v2 bearer token for social signal scanning
    TWITTER_BEARER_TOKEN: str = Field(default="your_twitter_bearer_token_here")
    # Helius API key (extracted from your Helius RPC URL after the last '/')
    HELIUS_API_KEY: str = Field(default="your_helius_api_key_here")
    # Hard deadline for any single token-processing pipeline (seconds)
    MAX_PROCESSING_TIME: float = Field(default=8.0)
    # Feature flags — set to False in .env to disable individual enrichments
    ENABLE_SOCIAL_SIGNALS: bool = Field(default=True)
    ENABLE_WALLET_ANALYSIS: bool = Field(default=True)
    ENABLE_RUG_DETECTION: bool = Field(default=True)

    # --- Phase 1 feature flags ---
    ENABLE_MULTI_SOURCE_DETECTION: bool = Field(default=True)
    ENABLE_HOLDER_VELOCITY: bool = Field(default=True)
    ENABLE_FIRST_BUYER_ANALYSIS: bool = Field(default=True)
    # Per-DEX WebSocket source flags (all default True)
    ENABLE_RAYDIUM_AMM_DETECTION: bool = Field(default=True)
    ENABLE_RAYDIUM_CPMM_DETECTION: bool = Field(default=True)
    ENABLE_ORCA_DETECTION: bool = Field(default=True)
    ENABLE_METEORA_DETECTION: bool = Field(default=True)

    # --- Phase 2 feature flags ---
    ENABLE_TX_PATTERN_SCORING: bool = Field(default=True)
    ENABLE_LIQUIDITY_GROWTH_ANALYSIS: bool = Field(default=True)
    ENABLE_CROSS_DEX_MONITORING: bool = Field(default=True)

    # --- Phase 3: Auto-buy / Auto-sell (SAFETY: both default False) ---
    # Set via Telegram /enable_autobuy + confirmation, or manually in .env
    ENABLE_AUTO_BUY: bool = Field(default=False)
    ENABLE_AUTO_SELL: bool = Field(default=False)

    # Circuit breaker
    ENABLE_CIRCUIT_BREAKER: bool = Field(default=True)
    DAILY_LOSS_LIMIT_PERCENT: float = Field(default=15.0)

    # Position sizing
    MAX_POSITION_SIZE_SOL: float = Field(default=0.2)
    MIN_POSITION_SIZE_SOL: float = Field(default=0.05)
    MAX_OPEN_POSITIONS: int = Field(default=3)

    # Signal quality gate for auto-buy (composite score / 100)
    MIN_SIGNAL_SCORE_FOR_BUY: float = Field(default=0.72)

    # Wallet private key — ONLY loaded from .env, never hardcoded
    # Format: base58 string OR JSON byte-array e.g. [1,2,3,...]
    WALLET_PRIVATE_KEY: str = Field(default="")

    # Dashboard API
    ENABLE_DASHBOARD: bool = Field(default=True)
    DASHBOARD_PORT: int = Field(default=8000)
    DASHBOARD_API_KEY: str = Field(default="change-me-in-production")
    CORS_ORIGINS: list[str] = Field(default=["http://localhost:3000"])

    def check_placeholders(self) -> list[str]:
        """Returns a list of setting names that still have placeholder values."""
        placeholders = []
        for key in self.model_fields.keys():
            val = getattr(self, key)
            if isinstance(val, str) and (not val or "here" in val.lower() or val.lower().startswith("your_")):
                placeholders.append(key)
        return placeholders  # FIX BUG-15

    def validate_api_keys(self) -> None:
        """
        Raises ValueError if any critical API key is empty or still a placeholder.
        Call this at startup before live operations begin.
        """
        _PLACEHOLDER_PREFIX = "your_"
        critical_keys = {
            "HONEYPOT_API_KEY": self.HONEYPOT_API_KEY,
            "GOPLUS_API_KEY": self.GOPLUS_API_KEY,
        }
        missing: list[str] = []
        for name, value in critical_keys.items():
            if not value or value.startswith(_PLACEHOLDER_PREFIX):
                missing.append(name)
        if missing:
            raise ValueError(
                f"Missing or unconfigured API keys: {', '.join(missing)}. "
                "Please set real values in your .env file before starting the bot."
            )

# Instantiate settings singleton
settings = Settings()

# Ensure database directory exists
db_dir = Path(settings.SQLITE_DB_PATH).parent
db_dir.mkdir(parents=True, exist_ok=True)
