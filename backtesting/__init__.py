# backtesting/__init__.py
"""
Backtesting engine for Solana meme coin signal bot.

Components:
  data_collector      — multi-source historical data scraper (free APIs)
  data_collector_v2   — DexScreener-based historical data collector (no API key needed)
  feature_engineer    — feature engineering + normalization pipeline
  train_models        — trains pump/rug/entry models, saves to models/
  backtest_engine     — event-driven simulation + grid-search optimisation
  ml_optimizer        — loads trained models; provides live MLPrediction for signal_engine
  backtest_report     — metrics aggregation + Telegram summary + JSON export
"""
