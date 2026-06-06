# Crypto Signal Bot — Context for AI Assistant

## Stack
- Python 3.11+, asyncio, aiohttp
- aiogram 3.x (Telegram)
- Solana: solders + solana-py + Helius RPC
- Analysis: pandas-ta + numpy==1.26.4 (LOCKED — DO NOT CHANGE)
- DB: aiosqlite + SQLite
- Security: GoPlus API + Honeypot.is API

## Critical Rules
1. Async ONLY — no blocking calls anywhere
2. Strict type hints on every function
3. No hardcoded credentials — always from settings
4. numpy==1.26.4 FIXED (pandas-ta compatibility conflict)
5. Signal mode ONLY — NO auto-trading, NO wallet execution

## Architecture Flow
New Token (Helius WS) 
  → solana_scanner.py 
  → security/scanner.py (GoPlus + Honeypot.is) 
  → token_analyzer.py (Birdeye OHLCV + pandas-ta) 
  → signal_engine.py (score evaluation) 
  → database (SQLite save) 
  → bot/tg_bot.py (Telegram alert)

## Signal Conditions
- SecurityScore >= 70
- confidence_score >= 65
- liquidity_usd >= 10,000
- volume_spike >= 2x average

## Current Phase
Phase 1 — Solana ONLY
Target: detect new Raydium pools via Helius WebSocket,
run security + technical analysis, send signal to Telegram.
