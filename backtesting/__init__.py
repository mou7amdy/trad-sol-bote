# backtesting/__init__.py
"""
Backtesting engine for Solana meme coin signal bot.

Components:
  data_collector  — multi-source historical data scraper (free APIs)
  backtest_engine — event-driven simulation + grid-search optimisation
  ml_optimizer    — XGBoost / RandomForest training pipeline
  backtest_report — metrics aggregation + Telegram summary + JSON export
"""
