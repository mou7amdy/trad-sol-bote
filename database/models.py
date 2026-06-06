# database/models.py

CREATE_TOKENS_TABLE = """
CREATE TABLE IF NOT EXISTS tokens (
    address TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    liquidity_usd REAL,
    market_cap REAL,
    is_honeypot INTEGER CHECK (is_honeypot IN (0, 1)),
    goplus_score REAL,
    liquidity_locked INTEGER CHECK (liquidity_locked IN (0, 1)),
    top10_holders_pct REAL
);
"""

CREATE_SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    price_at_signal REAL NOT NULL,
    confidence_score REAL NOT NULL,
    composite_score REAL DEFAULT 0.0,
    dex_source TEXT DEFAULT 'Raydium_AMM',
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    result TEXT,
    FOREIGN KEY (token_address) REFERENCES tokens (address)
);
"""

ALTER_SIGNALS_TABLE_ADD_COMPOSITE = """
ALTER TABLE signals ADD COLUMN composite_score REAL DEFAULT 0.0;
"""

ALTER_SIGNALS_TABLE_ADD_DEX_SOURCE = """
ALTER TABLE signals ADD COLUMN dex_source TEXT DEFAULT 'Raydium_AMM';
"""

CREATE_SCANS_TABLE = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    passed INTEGER CHECK (passed IN (0, 1)),
    fail_reason TEXT,
    FOREIGN KEY (token_address) REFERENCES tokens (address)
);
"""

# Indexes — applied after table creation in init_db()
CREATE_SIGNALS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_signals_token_address ON signals (token_address);
"""

CREATE_SCANS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_scans_token_address ON scans (token_address);
"""

# ---------------------------------------------------------------------------
# Enhancement tables
# ---------------------------------------------------------------------------

CREATE_WALLET_ANALYSES_TABLE = """
CREATE TABLE IF NOT EXISTS wallet_analyses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address     TEXT NOT NULL,
    creator_wallet    TEXT,
    wallet_age_days   INTEGER,
    sniper_count      INTEGER,
    sniper_percentage REAL,
    wallet_score      REAL,
    risk_level        TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_address) REFERENCES tokens (address)
);
"""

CREATE_WALLET_ANALYSES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_wallet_analyses_token_address
    ON wallet_analyses (token_address);
"""

CREATE_SOCIAL_SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS social_signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address       TEXT NOT NULL,
    mention_count_1h    INTEGER,
    mention_velocity    REAL,
    sentiment_score     REAL,
    has_viral_tweet     INTEGER CHECK (has_viral_tweet IN (0, 1)),
    social_score        REAL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_address) REFERENCES tokens (address)
);
"""

CREATE_SOCIAL_SIGNALS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_social_signals_token_address
    ON social_signals (token_address);
"""

CREATE_RUG_ANALYSES_TABLE = """
CREATE TABLE IF NOT EXISTS rug_analyses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address    TEXT NOT NULL,
    rug_probability  REAL,
    pattern_score    REAL,
    risk_flags       TEXT,    -- JSON-encoded list of flag strings
    recommendation   TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (token_address) REFERENCES tokens (address)
);
"""

CREATE_RUG_ANALYSES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_rug_analyses_token_address
    ON rug_analyses (token_address);
"""

# ---------------------------------------------------------------------------
# Phase 1 tables — multi-source detection, holder velocity, first-buyer
# ---------------------------------------------------------------------------

CREATE_DETECTED_POOLS_TABLE = """
CREATE TABLE IF NOT EXISTS detected_pools (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    mint_address TEXT NOT NULL,
    dex_name     TEXT NOT NULL,
    signature    TEXT,
    block_time   INTEGER,
    latency_ms   REAL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_DETECTED_POOLS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_detected_pools_mint
    ON detected_pools (mint_address);
"""

CREATE_HOLDER_VELOCITY_TABLE = """
CREATE TABLE IF NOT EXISTS holder_velocity_snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address         TEXT NOT NULL,
    snapshot_time         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    holder_count          INTEGER DEFAULT 0,
    holders_per_minute    REAL    DEFAULT 0.0,
    velocity_label        TEXT    DEFAULT 'STAGNANT',
    is_rug_warning        INTEGER DEFAULT 0,
    holder_velocity_score REAL    DEFAULT 0.0
);
"""

CREATE_HOLDER_VELOCITY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_holder_velocity_token
    ON holder_velocity_snapshots (token_address);
"""

CREATE_FIRST_BUYER_TABLE = """
CREATE TABLE IF NOT EXISTS first_buyer_analyses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address     TEXT NOT NULL,
    first_buyers      TEXT    DEFAULT '[]',
    smart_money_count INTEGER DEFAULT 0,
    smart_money_pct   REAL    DEFAULT 0.0,
    smart_money_score REAL    DEFAULT 0.0,
    analyzed_wallets  INTEGER DEFAULT 0,
    data_source       TEXT    DEFAULT 'rpc',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_FIRST_BUYER_INDEX = """
CREATE INDEX IF NOT EXISTS idx_first_buyer_token
    ON first_buyer_analyses (token_address);
"""

# ---------------------------------------------------------------------------
# Phase 2 tables — tx patterns, liquidity growth, cross-DEX
# ---------------------------------------------------------------------------

CREATE_TX_PATTERN_TABLE = """
CREATE TABLE IF NOT EXISTS tx_pattern_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address       TEXT    NOT NULL,
    buy_count           INTEGER DEFAULT 0,
    sell_count          INTEGER DEFAULT 0,
    total_txs           INTEGER DEFAULT 0,
    buy_ratio           REAL    DEFAULT 0.5,
    wash_trade_count    INTEGER DEFAULT 0,
    is_artificial_pump  INTEGER DEFAULT 0,
    tx_pattern_score    REAL    DEFAULT 50.0,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_TX_PATTERN_INDEX = """
CREATE INDEX IF NOT EXISTS idx_tx_pattern_token
    ON tx_pattern_results (token_address);
"""

CREATE_LIQUIDITY_GROWTH_TABLE = """
CREATE TABLE IF NOT EXISTS liquidity_growth_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address           TEXT    NOT NULL,
    candle_count            INTEGER DEFAULT 0,
    total_volume            REAL    DEFAULT 0.0,
    first_candle_vol_pct    REAL    DEFAULT 0.0,
    volume_cv               REAL    DEFAULT 0.0,
    growth_pattern          TEXT    DEFAULT 'INSUFFICIENT_DATA',
    growth_rate_pct         REAL    DEFAULT 0.0,
    liquidity_growth_score  REAL    DEFAULT 50.0,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_LIQUIDITY_GROWTH_INDEX = """
CREATE INDEX IF NOT EXISTS idx_liquidity_growth_token
    ON liquidity_growth_results (token_address);
"""

CREATE_CROSS_DEX_TABLE = """
CREATE TABLE IF NOT EXISTS cross_dex_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address    TEXT    NOT NULL,
    source_count     INTEGER DEFAULT 0,
    price_gap_pct    REAL    DEFAULT 0.0,
    gap_label        TEXT    DEFAULT 'UNKNOWN',
    is_manipulated   INTEGER DEFAULT 0,
    cross_dex_score  REAL    DEFAULT 50.0,
    prices_json      TEXT    DEFAULT '{}',
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_CROSS_DEX_INDEX = """
CREATE INDEX IF NOT EXISTS idx_cross_dex_token
    ON cross_dex_results (token_address);
"""

# ---------------------------------------------------------------------------
# Phase 3 tables — auto-trading (circuit breaker state)
# ---------------------------------------------------------------------------

CREATE_CIRCUIT_BREAKER_TABLE = """
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
