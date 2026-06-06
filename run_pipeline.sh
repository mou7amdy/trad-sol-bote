#!/bin/bash
set -e

echo "Step 1: Collecting historical data..."
python -m backtesting.data_collector_v2

echo "Step 2: Training ML models..."
python -m backtesting.train_models

echo "Step 3: Running backtest..."
python -m backtesting.backtest_runner

echo "Done! Check backtest_results.csv"
