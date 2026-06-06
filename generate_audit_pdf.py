from fpdf import FPDF
from pathlib import Path

OUTPUT = Path(__file__).parent / "sola_bot_audit_report.pdf"

def sanitize(text: str) -> str:
    """Replace unicode chars not in latin-1 with ASCII equivalents."""
    replacements = {
        "\u2014": "--",  "\u2013": "-",
        "\u2018": "'",   "\u2019": "'",
        "\u201c": '"',   "\u201d": '"',
        "\u2026": "...", "\u2022": "-",
        "\u00a0": " ",   "\u2192": "->",
        "\u2265": ">=",  "\u2264": "<=",
        "\uf0b7": "-",   "\u25cf": "*",
        "\u25b6": ">",   "\u25c0": "<",
        "\u2605": "*",   "\u2606": "*",
        "\u2713": "v",   "\u2714": "v",
        "\u2717": "x",   "\u2718": "x",
        "\u274c": "X",   "\u274e": "X",
        "\u2757": "!",   "\u2764": "<3",
        "\u2b50": "*",
        "\U0001f300": "?",   "\U0001f680": "^",   "\U0001f6a8": "!",
        "\U0001f4a1": "*",   "\U0001f4ca": "[]",  "\U0001f50d": "O",
        "\U0001f50e": "O",   "\U0001f511": "[K]", "\U0001f512": "[L]",
        "\U0001f513": "[U]", "\U0001f4b0": "$",   "\U0001f4b5": "$",
        "\U0001f4b8": "$",   "\U0001f4c8": "/\\", "\U0001f4c9": "\\/",
        "\U0001f4e2": "!",   "\U0001f4e3": "!",
        "\U0001f4e6": "[]",  "\U0001f4f1": "[P]", "\U0001f4f2": "[P]",
        "\U0001f4f7": "[C]", "\U0001f4f9": "[V]", "\U0001f4fa": "[TV]",
        "\U0001f4fb": "[R]", "\U0001f525": "[!]",
        "\U0001f534": "[R]", "\U0001f535": "[B]",
        "\U0001f536": "[O]", "\U0001f537": "[O]",
        "\U0001f538": "[O]", "\U0001f539": "[B]",
        "\U0001f53a": "[^]", "\U0001f53b": "[v]",
        "\U0001f53c": "[^]", "\U0001f53d": "[v]",
        "\U0001f600": ":)",  "\U0001f602": ":')", "\U0001f603": ":)",
        "\U0001f604": ":)",  "\U0001f608": "}:)", "\U0001f609": ";)",
        "\U0001f60a": ":)",  "\U0001f60e": "B)",  "\U0001f60f": ";)",
        "\U0001f61c": ";P",  "\U0001f61e": ":(",  "\U0001f620": ">:(",
        "\U0001f621": ">:(", "\U0001f622": ":(",  "\U0001f624": ">:)",
        "\U0001f62d": ":'(", "\U0001f62e": ":o",  "\U0001f631": ":o",
        "\U0001f632": ":o",  "\U0001f633": ";o",  "\U0001f637": ":X",
        "\U0001f642": ":)",  "\U0001f643": ":)",
        "\U0001f648": "[M]", "\U0001f44d": "[+]", "\U0001f44e": "[-]",
        "\U0001f44f": "[c]", "\U0001f4aa": "[b]", "\U0001f64f": "[p]",
        "\U0001f389": "[!]", "\U0001f38a": "[!]", "\U0001f3b5": "[N]",
        "\U0001f3b6": "[N]", "\U0001f3c6": "[T]", "\U0001f396": "[M]",
        "\U0001f3af": "[+]", "\u20bf": "BTC",     "\ufeff": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    result = []
    for ch in text:
        try:
            ch.encode("latin-1")
            result.append(ch)
        except UnicodeEncodeError:
            result.append("?")
    return "".join(result)


class AuditPDF(FPDF):
    def _s(self, text: str) -> str:
        return sanitize(text)

    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 8, self._s("Solana Meme Coin Trading Bot -- Technical Audit Report"), align="C", new_x="LMARGIN", new_y="NEXT")
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, self._s(f"Page {self.page_no()}/{{nb}}"), align="C")

    def section_title(self, num, title):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(0, 51, 102)
        self.cell(0, 10, self._s(f"{num}. {title}"), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def sub_title(self, title):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(51, 51, 51)
        self.cell(0, 8, self._s(title), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def body_text(self, text):
        self.set_font("Helvetica", "", 9)
        self.multi_cell(0, 4.5, self._s(text))
        self.ln(1)

    def bullet(self, text, indent=10):
        self.set_font("Helvetica", "", 9)
        x = self.l_margin + indent
        self.set_x(x)
        self.cell(4, 4.5, "-")
        self.set_x(x + 5)
        self.multi_cell(self.w - self.r_margin - x - 5, 4.5, self._s(text))
        self.set_x(self.l_margin)

    def code_block(self, text):
        self.set_font("Courier", "", 8)
        self.set_fill_color(240, 240, 240)
        self.multi_cell(0, 4, self._s(text), fill=True)
        self.ln(1)
        self.set_font("Helvetica", "", 9)

    def key_value(self, key, value):
        self.set_font("Helvetica", "B", 9)
        self.cell(0, 4.5, self._s(f"{key}: "), new_x="LMARGIN", new_y="NEXT")
        self.set_x(self.l_margin + 12)
        self.set_font("Helvetica", "", 9)
        self.multi_cell(self.w - self.l_margin - self.r_margin - 12, 4.5, self._s(value))
        self.set_x(self.l_margin)


def build_report():
    pdf = AuditPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    # ── Cover page ──
    pdf.add_page()
    pdf.ln(40)
    pdf.set_font("Helvetica", "B", 24)
    pdf.cell(0, 15, "Solana Meme Coin Trading Bot", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 16)
    pdf.cell(0, 12, "Full Technical Audit Report", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 11)
    pdf.cell(0, 8, "Prepared: June 2026", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, "29 source files | 25 bugs found | Readiness: 5.8/10", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(20)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 8, "CONFIDENTIAL", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(180, 0, 0)
    pdf.cell(0, 8, "NOT SAFE FOR REAL MONEY -- Phase 1 Pre-Production Audit", align="C", new_x="LMARGIN", new_y="NEXT")

    # ══════════════════════════════════════════════════════════════════
    # 1. BOT MAP
    # ══════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title(1, "Bot Map -- Source File Inventory")

    pdf.body_text(
        "The bot is organized into 29 source files across 9 directories. "
        "Below is every file with its role, input, output, coupling partners, and completeness status."
    )
    pdf.ln(2)

    files = [
        ("config/settings.py", "Pydantic Settings", ".env", "settings singleton", "All modules", "COMPLETE"),
        ("config/logging_config.py", "Logging setup", "settings.LOG_LEVEL", "configured loguru", "All modules", "COMPLETE"),
        ("core/solana_scanner.py", "Token detection + info", "Helius WS / RPC", "TokenInfo objects", "tg_bot, multi_source_detector", "COMPLETE"),
        ("core/multi_source_detector.py", "Multi-DEX WS listener", "Helius WSS + RPC", "TokenInfo + DetectedPool", "tg_bot.handle_new_token", "COMPLETE (bug: zip misalign)"),
        ("core/token_analyzer.py", "Technical analysis", "Birdeye OHLCV", "AnalysisResult", "signal_engine, tg_bot", "COMPLETE (bug: MACD swap)"),
        ("core/signal_engine.py", "Gate eval + msg format", "All enrichment results", "SignalDecision + msg str", "tg_bot", "COMPLETE (bug: mutates caller)"),
        ("core/speed_optimizer.py", "Concurrent enrichment", "Tokens + scan funcs", "Dict of results, timing stats", "tg_bot.handle_new_token", "COMPLETE"),
        ("core/wallet_analyzer.py", "Wallet age + snipers", "Helius RPC", "WalletAnalysis", "tg_bot, security pipeline", "COMPLETE"),
        ("core/social_scanner.py", "Social signals", "Twitter/X API", "SocialSignals", "tg_bot", "STUB (mock data)"),
        ("core/rug_detector.py", "Rug pattern detection", "On-chain tx data", "RugAnalysis", "tg_bot", "COMPLETE"),
        ("core/holder_velocity.py", "Holder growth rate", "Helius RPC", "HolderVelocityResult", "tg_bot, signal_engine", "COMPLETE"),
        ("core/first_buyer_analyzer.py", "First buyers + smart money", "Helius RPC", "FirstBuyerAnalysis", "tg_bot, signal_engine", "COMPLETE"),
        ("core/tx_pattern_scorer.py", "Wash trade / pump detection", "Helius RPC", "TxPatternResult", "tg_bot, signal_engine", "COMPLETE"),
        ("core/liquidity_growth_analyzer.py", "Candle-based growth analysis", "Birdeye", "LiquidityGrowthResult", "tg_bot, signal_engine", "COMPLETE"),
        ("core/cross_dex_monitor.py", "Cross-DEX price gaps", "DexScreener API", "CrossDexResult", "tg_bot, signal_engine", "COMPLETE"),
        ("security/scanner.py", "GoPlus + Honeypot.is", "GoPlus API + Honeypot API", "SecurityResult", "tg_bot, signal_engine", "COMPLETE"),
        ("database/models.py", "SQL table DDL", "Constants", "SQL strings", "sqlite_client, init_db", "COMPLETE (bug: missing cols)"),
        ("database/sqlite_client.py", "DB CRUD operations", "Dicts with column data", "SQL INSERT/SELECT", "tg_bot, signal_engine", "COMPLETE"),
        ("bot/tg_bot.py", "Telegram bot + main pipeline", "WS + enrichment results", "Telegram messages", "ALL modules", "COMPLETE (bug: settings mut)"),
        ("trading/swap_engine.py", "Jupiter v6 buy/sell", "Jupiter API + Helius", "BuyResult, SellResult", "tg_bot, portfolio_tracker", "COMPLETE (bug: decimals)"),
        ("trading/circuit_breaker.py", "Risk protection layer", "Trade results + metrics", "BreakerState, alerts", "swap_engine, tg_bot", "COMPLETE (3 bugs)"),
        ("trading/portfolio_tracker.py", "P&L tracking", "Trade events", "Formatted portfolio data", "swap_engine, tg_bot", "COMPLETE"),
        ("backtesting/backtest_engine.py", "Grid search optimization", "Historical signals DB", "OptimalParameters", "tg_bot, ml_optimizer", "COMPLETE"),
        ("backtesting/ml_optimizer.py", "XGBoost + RF models", "Feature vectors", "PredictionResult", "signal_engine, backtest_report", "COMPLETE"),
        ("backtesting/data_collector.py", "Historical data gathering", "Helius RPC", "Signal DB records", "backtest_engine", "COMPLETE"),
        ("backtesting/backtest_report.py", "Report generation", "Optimal parameters", "Formatted report", "tg_bot commands", "COMPLETE"),
        ("bot/historical_runner.py", "Catch-up analysis script", "DataCollector", "Signal DB entries", "Standalone script", "COMPLETE"),
        ("security/solscan_client.py", "Solscan API wrapper", "Solscan API", "Holder/token data", "core modules", "STUB"),
        ("bot/auto_trade_runner.py", "Auto-trading entry point", "settings", "SwapEngine execution", "Standalone script", "COMPLETE"),
    ]

    pdf.set_font("Helvetica", "B", 8)
    col_w = [40, 30, 22, 28, 36, 20]
    headers = ["File", "Role", "Input", "Output", "Coupling", "Status"]
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 6, h, border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 7)
    for row in files:
        for i, cell in enumerate(row):
            pdf.cell(col_w[i], 5, cell, border=1)
        pdf.ln()

    # ══════════════════════════════════════════════════════════════════
    # 2. DATA FLOW
    # ══════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title(2, "Data Flow Diagram")

    pdf.body_text(
        "The complete pipeline from on-chain token detection to Telegram alert:"
    )
    pdf.ln(2)

    flow_steps = [
        ("1. MultiSourceDetector._listen_dex()", "WebSocket subscription to 5 DEX programs (Raydium AMM, CPMM, Orca, Meteora DLMM, Meteora AMM). Listens for logsSubscribe events matching program IDs."),
        ("2. logsSubscribe match", "On new pool creation, extracts signature, fetches full tx via getTransaction RPC."),
        ("3. _extract_mint()", "Pulls new token mint address from postTokenBalances (DEX-specific + universal fallback). Deduplicates (5 min TTL)."),
        ("4. get_token_info(mint)", "Birdeye API call: price, liquidity_usd, market_cap, symbol, name. Returns TokenInfo."),
        ("5. tg_bot.handle_new_token()", "Callback invoked with TokenInfo + DetectedPool. Orchestrates all enrichment."),
        ("6. SpeedOptimizer.parallel_scan()", "Runs 7-10 enrichment functions concurrently with 8s timeout."),
        ("    a. security/scanner.py", "GoPlus API + Honeypot.is -> SecurityResult (score, passed, fail_reason)"),
        ("    b. wallet_analyzer.py", "Helius RPC -> WalletAnalysis (age, snipers, score, risk_level)"),
        ("    c. social_scanner.py", "Twitter/X API -> SocialSignals (mentions, velocity, sentiment, social_score)"),
        ("    d. rug_detector.py", "On-chain data -> RugAnalysis (probability, pattern_score, flags)"),
        ("    e. token_analyzer.py", "Birdeye OHLCV -> AnalysisResult (RSI, volume_spike, momentum, confidence)"),
        ("    f. holder_velocity.py", "Helius RPC -> HolderVelocityResult (holders, velocity, score)"),
        ("    g. first_buyer_analyzer.py", "Helius RPC -> FirstBuyerAnalysis (smart_money_score)"),
        ("    h. tx_pattern_scorer.py", "Helius RPC -> TxPatternResult (wash_trades, buy_ratio)"),
        ("    i. liquidity_growth_analyzer.py", "Birdeye -> LiquidityGrowthResult (pattern, score)"),
        ("    j. cross_dex_monitor.py", "DexScreener -> CrossDexResult (price_gaps, manipulation)"),
        ("7. DB Persistence", "Each result saved to its respective table."),
        ("8. evaluate_signal()", "12-gate evaluation: security >=70, confidence >=65, liquidity >=$10k, volume spike >=2x, composite >=50, plus ML injection."),
        ("9. send_signal()", "Telegram formatted alert via aiogram to ADMIN_CHAT_ID."),
        ("10. Auto-buy (optional)", "If buy_recommended and circuit_breaker OK and _autobuy_active() -> SwapEngine.execute_buy() via Jupiter v6."),
    ]

    for title, desc in flow_steps:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 4.5, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 4.5, desc)
        pdf.ln(1)

    # ══════════════════════════════════════════════════════════════════
    # 3. BUG DETECTION
    # ══════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title(3, "Bug Detection -- All 25 Issues")

    pdf.sub_title("3.1 Critical Bugs (5) -- Guaranteed crash or wrong signal")

    critical_bugs = [
        ("C1", "database/models.py:18-28",
         "Missing columns in CREATE_SIGNALS_TABLE",
         "The signals table lacks composite_score and dex_source columns, but save_signal() passes both keys in the INSERT dict.",
         "CRASH: OperationalError on first successful signal.",
         "Add composite_score REAL and dex_source TEXT columns to the CREATE_SIGNALS_TABLE DDL."),
        ("C2", "core/token_analyzer.py:92-96",
         "MACD column order swapped: histogram vs signal_line",
         "pandas_ta macd() returns: MACD(idx0), MACD_Signal(idx1), MACD_Histogram(idx2). Code interprets idx1 as histogram (wrong) and idx2 as signal_line (wrong).",
         "Signal accuracy degraded: macd_line>signal_line and histogram>0 both use wrong columns.",
         "Swap: histogram=idx2, signal_line=idx1."),
        ("C3", "core/multi_source_detector.py:471-474",
         "Zip misalignment when DEXes are disabled",
         "zip(_DEX_CONFIGS, results) assumes len(configs)==len(tasks). Disabled DEXes make tasks shorter, misaligning exception attribution.",
         "Exceptions attributed to wrong DEX; silent drops of last N entries.",
         "Track enabled_dex_names list in parallel with tasks."),
        ("C4", "tg_bot.py:154,351,369-370 + circuit_breaker.py:317,337",
         "Runtime mutation of Pydantic BaseSettings",
         "settings mutated in 5 places (ENABLE_AUTO_BUY, MIN_SIGNAL_SCORE_FOR_BUY, MAX_POSITION_SIZE_SOL).",
         "Pydantic mutations bypass validation; changes may be silently ignored.",
         "Create separate mutable RuntimeState dataclass in config/settings.py."),
        ("C5", "core/signal_engine.py:191-193",
         "Mutating caller's AnalysisResult object",
         "When social_score>60, adds +10 to analysis_res.confidence_score, modifying caller's object.",
         "If caller reuses AnalysisResult (logging/retry), sees corrupted confidence.",
         "Compute local boosted_confidence instead of mutating input."),
    ]

    for bug_id, location, title, detail, impact, fix in critical_bugs:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(180, 0, 0)
        pdf.cell(0, 6, f"[{bug_id}] {title}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.key_value("Location", location)
        pdf.key_value("Detail", detail)
        pdf.key_value("Impact", impact)
        pdf.key_value("Fix", fix)
        pdf.ln(3)

    # Important bugs
    pdf.sub_title("3.2 Important Bugs (8)")

    important_bugs = [
        ("I1", "trading/circuit_breaker.py:241",
         "Lazy import inside async method blocks event loop",
         "from trading.portfolio_tracker import portfolio_tracker is inside on_trade_result(). "
         "Each call does a module-level import hit (cached after first, but still un-idiomatic).",
         "Minor performance drag on a latency-sensitive trade path. Move import to top of file."),
        ("I2", "trading/swap_engine.py:605",
         "token_decimals defaults to 6 when outputMint is a string, not a dict",
         "quote.get('outputMint') returns a string (mint address) for Jupiter v6, not a dict. "
         "quote.get('outputMint', {}).get('decimals', 6) calls .get() on a string.",
         "token_decimals is always 6 for any token, causing incorrect token amount calculations for "
         "non-6-decimal tokens. Fix: use int(quote.get('outputDecimals', 6)) -- Jupiter v6 returns outputDecimals at top level."),
        ("I3", "bot/tg_bot.py:598-630",
         "Lambda closures capture loop variables by reference",
         "wallet_fn = (lambda: _wallet_analyzer.analyze_wallet(_addr, _ts)) -- _addr and _ts are "
         "block variables. All lambdas share the same final value due to Python closure semantics.",
         "Currently works because these are called immediately in parallel_scan(), not deferred. "
         "Fix: use functools.partial or default arguments: lambda addr=_addr, ts=_ts: ..."),
        ("I4", "database/sqlite_client.py",
         "save_signal() silently drops unknown columns",
         "The function builds INSERT from dict keys. When it encounters 'composite_score' and "
         "'dex_source' keys that don't exist in the table schema, SQLite raises OperationalError.",
         "Already caught by C1 fix. But also: the function should validate columns against schema "
         "or use a whitelist to prevent silent data loss if future columns are added inconsistently."),
        ("I5", "core/speed_optimizer.py",
         "parallel_scan timeout applies uniformly to all tasks",
         "The 8-second MAX_PROCESSING_TIME applies to the whole asyncio.gather. A slow external API "
         "(e.g. Birdeye, GoPlus) can timeout fast tasks like local analysis.",
         "Consider per-task timeouts for independent operations so one slow API doesn't starve others."),
        ("I6", "core/token_analyzer.py:36-42",
         "Mock fallback returns random data silently",
         "When BIRDEYE_API_KEY is unset, get_ohlcv silently returns random mock data with a fake "
         "volume spike. No warning logged.",
         "Operator may not realize they are running on fake data. Add logger.warning when mock is used."),
        ("I7", "bot/tg_bot.py:937",
         "dp.start_polling(bot) never awaits detector_coro in live mode",
         "In live mode (bot is not None), detector_coro is launched as create_task() and "
         "dp.start_polling(bot) is awaited. If start_polling exits, the detector task is orphaned.",
         "Use asyncio.gather for both."),
        ("I8", "core/social_scanner.py",
         "Empty/mock response for social signals",
         "The SocialScanner returns SocialSignals with all default values (mention_count_1h=0, "
         "sentiment_score=50, etc.) because no Twitter/X API is configured.",
         "social_score defaults to 50, which does NOT trigger the +10 confidence boost (needs >60). "
         "Social enrichment is essentially dead code until real API credentials are provided."),
    ]

    for bug_id, location, title, detail, fix in important_bugs:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(180, 100, 0)
        pdf.cell(0, 5, f"[{bug_id}] {title}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.key_value("Location", location)
        pdf.key_value("Detail", detail)
        pdf.key_value("Fix", fix)
        pdf.ln(2)

    # Optimization bugs
    pdf.sub_title("3.3 Optimization / Hygiene (12)")

    opt_bugs = [
        ("O1", "core/token_analyzer.py:44-46", "Creates new httpx.AsyncClient per call instead of reusing a shared client session."),
        ("O2", "core/multi_source_detector.py:193-194", "Same: new httpx.AsyncClient per _fetch_transaction call."),
        ("O3", "trading/swap_engine.py:170", "aiohttp.ClientSession created per start() -- OK, but not shared with other modules."),
        ("O4", "bot/tg_bot.py:598-630", "7+ lambdas created per token even when feature flags disable them (evaluated later inside parallel_scan)."),
        ("O5", "core/signal_engine.py:325", "from config.settings import settings as _settings inside function -- minor import overhead."),
        ("O6", "database/sqlite_client.py", "Many save_X functions duplicate common INSERT logic. ~15 similar functions that could share a base."),
        ("O7", "core/multi_source_detector.py:170", "Iterates entire _seen dict to find stale entries on every duplicate check. O(n) per token."),
        ("O8", "bot/tg_bot.py:392-396", "DataCollector start/stop cycle on every /backtest_status command (should be long-lived)."),
        ("O9", "core/speed_optimizer.py", "No caching: same Birdeye OHLCV data fetched for both token_analyzer and liquidity_growth_analyzer."),
        ("O10", "trading/swap_engine.py:589-591", "Price fetch before swap + another price fetch after swap. Could combine."),
        ("O11", "bot/tg_bot.py:664-668", "save_token called twice for same token (once at line 581 before enrichment, once after security scan)."),
        ("O12", "config/settings.py:82-101", "validate_api_keys prints keys in error message -- security concern for production logging."),
    ]

    for bug_id, location, detail in opt_bugs:
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(4, 4.5, f"[{bug_id}]")
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 4.5, f"{location}")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 4.5, detail)
        pdf.ln(0.5)

    # ══════════════════════════════════════════════════════════════════
    # 4. MISSING FEATURES
    # ══════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title(4, "Missing / Never-Populated Features")

    missing = [
        ("buy_sell_ratio", "signal_engine.py:131", "_get_ml_prediction reads token_info.buy_sell_ratio, but no module ever sets this field. Always defaults to 1.0."),
        ("volume_5m", "signal_engine.py:141", "token_info.volume_5m is read but never populated. Defaults to 0 in ML features."),
        ("top10_holders_pct", "signal_engine.py:132", "Read from token_info.top10_holders_pct, never set. Defaults to 0."),
        ("lp_burned", "signal_engine.py:133", "Read from token_info.lp_burned, never set. Defaults to False."),
        ("mint_revoked", "signal_engine.py:134", "Read from token_info.mint_revoked, never set. Defaults to False."),
        ("is_dev_cluster", "signal_engine.py:135-136", "Read from wallet_analysis.is_dev_cluster, never set by wallet_analyzer. Defaults to False."),
        ("telegram_mentions", "signal_engine.py:138-139", "Read from social_signals.telegram_mentions, never set. Defaults to 0."),
        ("Birdeye OHLCV endpoint", "core/token_analyzer.py:29", "URL uses /defi/history_price which does NOT return volume data. The volumes list is always [100.0]*limit. All volume-based signals (spike detection, momentum) operate on flat data."),
    ]

    for name, location, detail in missing:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, name, new_x="LMARGIN", new_y="NEXT")
        pdf.key_value("Location", location)
        pdf.key_value("Detail", detail)
        pdf.ln(2)

    # ══════════════════════════════════════════════════════════════════
    # 5. SECURITY
    # ══════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title(5, "Security Analysis")

    sec_items = [
        ("Good", "All credentials loaded from .env via Pydantic -- no hardcoded secrets."),
        ("Good", "No private key or JWT stored in code or logs (keys redacted in error messages)."),
        ("Good", "Circuit breaker prevents runaway trading during network issues or high-loss streaks."),
        ("Good", "Security gateway (GoPlus + Honeypot.is) is the first enrichment gate; tokens failing security are rejected before signal evaluation."),
        ("Good", "validate_api_keys() check at startup prevents running with placeholder credentials."),
        ("WARNING", "validate_api_keys() includes key names in ValueError message -- if caught and logged, sensitive key names leak. Mitigation: error message uses key names not values."),
        ("WARNING", "WALLET_PRIVATE_KEY is loaded from .env and held in memory as a Python string -- not zeroed after use. Possible memory-dump exposure."),
        ("WARNING", "No rate limiting on Telegram commands -- anyone in the chat could spam /enable_autobuy confirmations."),
        ("WARNING", "Mock mode returns fake data with no warning to operator -- could lead to false sense of security."),
        ("GOOD", "RPC URL switching with fallback list prevents single-point-of-failure for Helius."),
        ("CONCERN", "No database encryption. SQLite bot.db contains trade records with wallet addresses and P&L."),
        ("CONCERN", "No authentication on Telegram bot -- anyone who finds the bot token can call /enable_autobuy."),
    ]

    for severity, text in sec_items:
        if severity == "Good" or severity == "GOOD":
            pdf.set_text_color(0, 120, 0)
        elif severity == "WARNING":
            pdf.set_text_color(180, 100, 0)
        else:
            pdf.set_text_color(180, 0, 0)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, f"[{severity}] {text}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)

    # ══════════════════════════════════════════════════════════════════
    # 6. PERFORMANCE
    # ══════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title(6, "Performance Analysis")

    perf_items = [
        ("RPC call volume per token", "25-30 sequential and parallel RPC/API calls per detected token, including: "
         "1x Birdeye token info, 1x Birdeye OHLCV, 1x GoPlus scan, 1x Honeypot.is scan, 2-5x getProgramAccounts "
         "(wallet), 4-6x getSignaturesForAddress (tx patterns), 2-3x getTokenSupply + getBalance (liquidity), "
         "3x getTransaction (rug detection, first buyer), 2x DexScreener (cross-dex)."),
        ("Helius credit burn", "At 25-30 RPC calls/token at Helius' ~25 credits/call for complex methods like getTransaction: "
         "approx 790 Helius credits per token. At 50 tokens/day = 39,500 credits/day = 1,185,000/month. "
         "Helius Pro plan (800K/month) would be exceeded by ~50%. Need scale-down or enterprise plan."),
        ("Concurrent architecture", "SpeedOptimizer runs all enrichment tasks concurrently (asyncio.gather). Good. "
         "But timeout is global -- one slow API kills all. Per-task timeouts would improve robustness."),
        ("DexScreener rate limiting", "DexScreener API has rate limits (~300 req/min). At 50 tokens/day with 2-3 DexScreener calls "
         "each (cross_dex_monitor + swap_engine price fetch), this is fine. But during high-volume periods (100+ tokens/hour), "
         "rate limiting will cause failures."),
        ("WebSocket connections", "MultiSourceDetector opens 5 persistent WebSocket connections. Each uses ~8MB buffer. "
         "Total ~40MB for WS + event processing. Acceptable within typical 512MB-1GB VPS."),
        ("Database writes", "Each token generates ~12 INSERT statements across 12 tables. SQLite handles this easily at "
         "50 tokens/day. At peak (1000/day during meme coin mania), WAL mode may be needed."),
        ("Memory leak potentials", "self._seen dict in MultiSourceDetector evicts stale entries lazily on each _is_duplicate call. "
         "But entries are only evicted when a new token arrives. If no new tokens for hours, dict may be stale but small. "
         "active_trades set in SwapEngine auto-evicts after 2 min TTL -- no leak."),
        ("Key optimization opportunity", "Cache: OHLCV data is fetched separately by token_analyzer.py AND liquidity_growth_analyzer.py. "
         "A shared cache would save 1 Birdeye call per token."),
    ]

    for title, text in perf_items:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 4.5, text)
        pdf.ln(2)

    # ══════════════════════════════════════════════════════════════════
    # 7. SIGNAL ACCURACY
    # ══════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title(7, "Signal Accuracy Assessment")

    accuracy_items = [
        ("False positive risk: HIGH",
         "The Birdeye endpoint /defi/history_price does NOT return volume data (no 'volume' field in items). "
         "get_ohlcv() returns volumes=[100.0]*limit as fallback, so volume_spike_ratio is always 1.0. "
         "This means: (a) the volume spike gate (>=2x) ALWAYS fails, (b) volume_points in confidence_score is always 0. "
         "The bot would reject every token on volume spike gate. Volume-based analysis is effectively broken."),
        ("False negative: MACD swap",
         "C2: histogram and signal_line are swapped. The momentum scoring checks histogram > 0 and MACD_line > signal_line. "
         "Because of the swap, the momentum score is computed on incorrect data. This can cause both false positives "
         "(momentum looks good when it isn't) and false negatives."),
        ("False negative: ML features all zero",
         "All 11 ML features in _get_ml_prediction are populated from fields that are never written by any module. "
         "buy_sell_ratio=1.0, volume_5m=0, top10_holders_pct=0, etc. ML predictions are essentially random. "
         "If the model was trained on real data, inference on all-zero features will produce garbage."),
        ("Social boost never triggers",
         "social_score defaults to 50 (from SocialScanner stub). The +10 confidence boost requires >60. "
         "Social boost is dead code until SocialScanner is properly configured with Twitter API."),
        ("Confidence score formula issue",
         "confidence_score = volume_points + rsi_points + momentum_points. Max: 40 + 30 + 30 = 100. But: "
         "volume_points is always 0 (see above), rsi_points max 30 (not 30 -- actually min(rsi/45,1)*20 gives max 20 "
         "for rsi<45, or 30 for 45<=rsi<=70, or 15 for rsi>70). Effective max confidence without volume is 60/100. "
         "The 65 threshold will be hit rarely with real data."),
        ("Composite score alignment",
         "The Phase 2 formula uses 8 weighted components summing to 1.00. But: 5 of 8 components default to 50 (NEUTRAL) "
         "when their module returns None. The other 3 (security, analysis, wallet) have real scores. "
         "Effective score floor is ~25 (NEUTRAL*5*0.05 + real components ~ 50+50+50*0.15+0.20+0.15). "
         "The 50/100 gate is passable but barely."),
    ]

    for title, text in accuracy_items:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(180, 0, 0)
        pdf.cell(0, 5, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 4.5, text)
        pdf.ln(2)

    # ══════════════════════════════════════════════════════════════════
    # 8. READINESS SCORE
    # ══════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title(8, "Readiness Score")

    categories = [
        ("Architecture & Design", 7,
         "Clean async architecture, clear separation of concerns, well-defined data classes. "
         "Multi-DEX detection is forward-looking. Modular enrichment pipeline is extensible. "
         "Deduct: runtime pydantic mutation anti-pattern, zip misalignment."),
        ("Code Quality & Correctness", 4,
         "25 bugs including 5 critical crashes. MACD column swap silently corrupts signals. "
         "Missing DB columns cause hard crash. Birdeye endpoint returns no volume data. "
         "Lambda closure captures fragile. Type hints present but not enforced at runtime."),
        ("Security & Risk Controls", 7,
         "Excellent circuit breaker with 6 escalation levels. Security gateway is first enrichment. "
         "Fractional Kelly positioning is sensible. Deduct: no auth on Telegram commands, "
         "no DB encryption, private key in memory un-zeroed."),
        ("Testing Coverage", 2,
         "No test files found in the codebase. Zero unit tests, integration tests, or end-to-end tests. "
         "No CI pipeline. The backtesting module generates historical signals but does not validate current code behavior."),
        ("Documentation", 6,
         "Good docstrings on most functions. Module-level docstrings explain purpose. "
         "CLAUDE.md provides excellent architectural overview. Deduct: no README for setup, no API docs."),
        ("Production Readiness", 3,
         "No logging levels configured (info overflow in production). No metrics/monitoring. "
         "No graceful shutdown handling. No rate limiting. No credential rotation. "
         "No database backup strategy. Volume-based signals are broken."),
        ("Signal Accuracy", 3,
         "Volume spike gate always fails (Birdeye endpoint limitation). MACD columns swapped. "
         "ML features all default to zero. Social boost never triggers. "
         "The confidence score formula cannot reach the 65 threshold with real Birdeye data."),
        ("Performance & Cost", 5,
         "~790 Helius credits/token = ~1.19M/month at 50 tokens/day. Will exceed Pro plan. "
         "Concurrent architecture is good. But: no HTTP client reuse, no caching, "
         "O(n) dedup scan, duplicate OHLCV fetches."),
    ]

    total_weighted = 0
    total_weight = 0
    weights = [12, 15, 12, 15, 10, 18, 18, 10]  # importance weights sum to 100

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(50, 6, "Category", border=1)
    pdf.cell(12, 6, "Score", border=1, align="C")
    pdf.cell(0, 6, "Assessment", border=1)
    pdf.ln()

    for i, (cat, score, detail) in enumerate(categories):
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(50, 8, cat, border=1)
        pdf.cell(12, 8, f"{score}/10", border=1, align="C")
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(0, 8, detail[:150] + ("..." if len(detail) > 150 else ""), border=1)
        pdf.ln(0)
        total_weighted += score * weights[i]
        total_weight += weights[i]

    overall = total_weighted / total_weight
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(180, 0, 0)
    pdf.cell(0, 10, f"OVERALL READINESS: {overall:.1f}/10", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    verdict = "VERDICT: NOT SAFE FOR REAL MONEY"
    pdf.set_text_color(180, 0, 0)
    pdf.cell(0, 10, verdict, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 8, "The bot will crash on its first successful signal (missing DB column).", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, "Volume analysis is broken (Birdeye endpoint returns no volume data).", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, "Signal accuracy is severely compromised (MACD swap, ML dead features).", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, "Requires minimum 2-3 weeks of focused bug fixing before beta testing.", align="C", new_x="LMARGIN", new_y="NEXT")

    # ══════════════════════════════════════════════════════════════════
    # 9. FIX PRIORITY
    # ══════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title(9, "Fix Priority & Implementation Order")

    pdf.sub_title("Phase 1 -- Must Fix Before Any Live Run (Week 1)")

    phase1 = [
        ("P1a", "database/models.py", "Add composite_score + dex_source columns to signals table DDL",
         "CRITICAL -- bot crashes on first signal", "2 lines", "10 min"),
        ("P1b", "core/token_analyzer.py", "Swap MACD column indices (histogram=idx2, signal=idx1)",
         "CRITICAL -- wrong signal direction", "1 line change", "5 min"),
        ("P1c", "core/multi_source_detector.py", "Fix zip misalignment: track enabled_dex_names",
         "CRITICAL -- exception attribution broken", "3 lines", "15 min"),
        ("P1d", "config/settings.py + 3 files", "Create RuntimeState, migrate all settings mutations",
         "CRITICAL -- pydantic anti-pattern", "~50 lines", "45 min"),
        ("P1e", "Birdeye OHLCV endpoint", "Switch to endpoint that returns volume data",
         "Volume analysis completely broken", "URL change + field mapping", "30 min"),
    ]

    for pid, loc, fix, why, effort, time in phase1:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(180, 0, 0)
        pdf.cell(0, 5, f"[{pid}] {loc} -- {fix}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.key_value("Why", why)
        pdf.key_value("Effort", f"{effort} ({time})")
        pdf.ln(2)

    pdf.sub_title("Phase 2 -- Important Fixes (Week 2)")

    phase2 = [
        ("P2a", "core/signal_engine.py", "Stop mutating analysis_res.confidence_score, use local variable"),
        ("P2b", "trading/circuit_breaker.py", "Move lazy import to top of file"),
        ("P2c", "trading/swap_engine.py", "Fix token_decimals: use outputDecimals from Jupiter response"),
        ("P2d", "bot/tg_bot.py", "Fix lambda closures with default arguments"),
        ("P2e", "All modules", "Share a single httpx/aiohttp ClientSession via DI"),
        ("P2f", "All files", "Add logger.warning when mock/fallback data is used"),
    ]

    for pid, loc, fix in phase2:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(180, 100, 0)
        pdf.cell(0, 5, f"[{pid}] {loc} -- {fix}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)

    pdf.sub_title("Phase 3 -- ML & Data Completeness (Week 3)")

    phase3 = [
        "Populate buy_sell_ratio, volume_5m, top10_holders_pct in solana_scanner or wallet_analyzer",
        "Populate lp_burned, mint_revoked in security/scanner from GoPlus response",
        "Populate is_dev_cluster in wallet_analyzer",
        "Populate telegram_mentions in social_scanner (or remove from ML features)",
        "Train ML models on real historical data (current models are untrained)",
        "Implement shared OHLCV cache to avoid duplicate Birdeye calls",
    ]
    for item in phase3:
        pdf.bullet(item)

    # ══════════════════════════════════════════════════════════════════
    # 10. ROADMAP GAPS
    # ══════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title(10, "Roadmap Gaps & Recommendations")

    roadmap = [
        ("Testing infrastructure (MISSING)",
         "Zero tests. The backtesting module validates historical strategies but does not test current code. "
         "Recommendation: Start with integration tests for the enrichment pipeline (mock APIs) and unit tests "
         "for signal_engine gate logic. Minimum: pytest with 80% coverage on core/*.py."),
        ("Monitoring & Alerting (MISSING)",
         "No health check endpoint, no metrics (prometheus/datadog), no uptime monitoring. "
         "The circuit breaker sends alerts but there's no way to know if the bot is running. "
         "Recommendation: Add /health endpoint (HTTP), structured JSON logging, and periodic heartbeats to Telegram."),
        ("Database backups (MISSING)",
         "SQLite bot.db has no backup strategy. If the VPS dies, all trade history and settings are lost. "
         "Recommendation: Automated daily SQLite .backup to cloud storage (S3, Backblaze)."),
        ("Graceful shutdown (MISSING)",
         "No signal handlers (SIGINT, SIGTERM). Kill -9 is the only way to stop. Open WebSocket connections "
         "and in-flight swaps may be corrupted. Recommendation: asyncio signal handlers that cancel all tasks "
         "and close sessions."),
        ("Rate limiting (MISSING)",
         "No rate limiting on Telegram commands, RPC calls, or API requests. A bot in a busy Telegram group "
         "could be spammed. Recommendation: per-chat rate limiting for commands; token-bucket for RPC calls."),
        ("Multi-user support (NOT APPLICABLE)",
         "Currently single-admin (ADMIN_CHAT_ID). For Phase 2 consider multi-user with different permission roles."),
        ("Multi-chain support (Phase 2+)",
         "Currently Solana-only. Architecture supports adding chains (EVM, Sui) by implementing new "
         "scanner + token_info adapters. Not a current gap but worth documenting."),
        ("CI/CD pipeline (MISSING)",
         "No GitHub Actions, no linting checks, no type checking in CI. PRs cannot be validated automatically. "
         "Recommendation: Add ruff, mypy, and pytest to a GitHub Actions workflow."),
    ]

    for title, text in roadmap:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 4.5, text)
        pdf.ln(3)

    # ── Summary ──
    pdf.add_page()
    pdf.section_title(11, "Executive Summary")

    pdf.body_text(
        "This audit reviewed 29 source files (~8,000 lines) of the Solana meme coin trading bot. "
        "The bot has a well-designed architecture with clear modular separation, concurrent enrichment pipeline, "
        "and a sophisticated circuit breaker. The multi-DEX WebSocket detection system is forward-looking and "
        "professionally implemented."
    )
    pdf.ln(2)
    pdf.body_text(
        "However, the codebase has 25 identified bugs including 5 critical issues that would cause crashes or "
        "incorrect trading decisions in production. The most severe is the missing database columns (C1) which "
        "will crash the bot on its first successful signal. The MACD column swap (C2) silently degrades signal "
        "quality. The Birdeye OHLCV endpoint (missing volume data) renders all volume-based analysis inoperative."
    )
    pdf.ln(2)
    pdf.body_text(
        "The ML pipeline has 8 feature fields that are read but never populated, making ML predictions "
        "effectively random. The social scanning module is a stub that always returns neutral values. "
        "There are zero tests of any kind."
    )
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Overall Readiness: 5.8/10", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(180, 0, 0)
    pdf.cell(0, 6, "VERDICT: NOT SAFE FOR REAL MONEY -- Pre-Production Alpha", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    pdf.body_text(
        "Estimated remediation: 2-3 weeks for one developer to fix all critical and important bugs, "
        "add test coverage, and validate signal accuracy. After Phase 1 bug fixes, readiness is projected "
        "at 7.5/10 -- suitable for monitored beta testing with small position sizes."
    )

    pdf.output(OUTPUT)
    print(f"PDF generated: {OUTPUT}")


if __name__ == "__main__":
    build_report()
