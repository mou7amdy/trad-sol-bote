# bot/tg_bot.py
import uvloop
uvloop.install()
"""
Telegram bot + main token-handling pipeline (Phase 1 enhanced).

Detection
---------
``MultiSourceDetector`` replaces the single-source ``monitor_new_listings``
from ``solana_scanner.py``.  It connects simultaneously to Raydium AMM,
Raydium CPMM, Orca Whirlpool, Meteora DLMM, and Meteora AMM WebSocket
streams and deduplicates detected tokens across sources.

Enrichment pipeline (per token, all concurrent via SpeedOptimizer)
------------------------------------------------------------------
  security      — GoPlus + Honeypot.is security scan
  wallet        — creator wallet age + sniper detection
  social        — Twitter/X social signal score
  rug           — on-chain rug-pull pattern score
  analysis      — technical analysis (RSI, volume, momentum)
  holder_velocity — holder growth rate (Phase 1)
  smart_money    — first-buyer wallet profiling (Phase 1)

Signal scoring (Phase 1 formula, weights sum to 1.00)
------------------------------------------------------
  SIGNAL_SCORE =
      security_score        × 0.25
    + wallet_score          × 0.20
    + rug_score             × 0.20
    + holder_velocity_score × 0.15
    + smart_money_score     × 0.15
    + social_score          × 0.05

Feature flags (set in .env)
---------------------------
  ENABLE_WALLET_ANALYSIS          — run WalletAnalyzer
  ENABLE_SOCIAL_SIGNALS           — run SocialScanner
  ENABLE_RUG_DETECTION            — run RugDetector
  ENABLE_MULTI_SOURCE_DETECTION   — use MultiSourceDetector (else single Raydium)
  ENABLE_HOLDER_VELOCITY          — run HolderVelocityTracker
  ENABLE_FIRST_BUYER_ANALYSIS     — run FirstBuyerAnalyzer
  ENABLE_RAYDIUM_AMM_DETECTION    — Raydium AMM v4 source
  ENABLE_RAYDIUM_CPMM_DETECTION   — Raydium CPMM source
  ENABLE_ORCA_DETECTION           — Orca Whirlpool source
  ENABLE_METEORA_DETECTION        — Meteora DLMM + Dynamic AMM sources
"""

import asyncio
import time
from typing import Optional

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from loguru import logger

from config.settings import settings
from database.sqlite_client import (
    init_db,
    save_token,
    save_signal,
    save_scan,
    save_wallet_analysis,
    save_social_signals,
    save_rug_analysis,
    save_detected_pool,
    save_holder_velocity_snapshot,
    save_first_buyer_analysis,
    save_tx_pattern,
    save_liquidity_growth,
    save_cross_dex_result,
    get_recent_signals,
    token_exists,
)
from core.solana_scanner import TokenInfo, monitor_new_listings
from core.multi_source_detector import MultiSourceDetector, DetectedPool
from security.scanner import full_security_scan
from core.token_analyzer import analyze_token
from core.signal_engine import evaluate_signal, format_signal_message
from core.wallet_analyzer import WalletAnalyzer
from core.social_scanner import SocialScanner
from core.rug_detector import RugDetector
from core.speed_optimizer import SpeedOptimizer
from core.holder_velocity import HolderVelocityTracker
from core.first_buyer_analyzer import FirstBuyerAnalyzer
from api.event_bus import event_bus
from core.tx_pattern_scorer import TxPatternScorer
from core.liquidity_growth_analyzer import LiquidityGrowthAnalyzer
from core.cross_dex_monitor import CrossDexMonitor
# --- Phase 3: Auto-trading ---
from trading.swap_engine       import swap_engine, SwapEngine
from trading.circuit_breaker   import circuit_breaker
from trading.portfolio_tracker import portfolio_tracker

# ---------------------------------------------------------------------------
# Shared singleton instances (created once at import time)
# ---------------------------------------------------------------------------
_wallet_analyzer    = WalletAnalyzer()
_social_scanner     = SocialScanner()
_rug_detector       = RugDetector()
_speed_optimizer    = SpeedOptimizer()
_holder_tracker     = HolderVelocityTracker()
_buyer_analyzer     = FirstBuyerAnalyzer()
_tx_scorer          = TxPatternScorer()
_liq_analyzer       = LiquidityGrowthAnalyzer()
_cross_dex          = CrossDexMonitor()
_multi_detector     = MultiSourceDetector()


# ---------------------------------------------------------------------------
# Bot state
# ---------------------------------------------------------------------------
class _BotState:
    monitoring_active:      bool  = True
    processed_tokens_count: int   = 0
    # Auto-buy enable/disable tracking
    autobuy_enabled_until:  float = 0.0   # monotonic timestamp; 0 = disabled
    autobuy_confirm_pending: bool = False  # waiting for "CONFIRM BUY" reply


_state = _BotState()

router = Router()
dp     = Dispatcher()

bot: Bot | None = None
if settings.TELEGRAM_TOKEN and not settings.TELEGRAM_TOKEN.startswith("your_"):
    bot = Bot(token=settings.TELEGRAM_TOKEN)
    dp.include_router(router)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _send_admin(text: str) -> None:
    """Send a message to the admin chat (used by trading modules for alerts)."""
    await event_bus.emit("alert", {"text": text, "source": "telegram"})
    if bot:
        try:
            await bot.send_message(
                chat_id=settings.ADMIN_CHAT_ID,
                text=text,
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error(f"_send_admin failed: {exc}")


def _autobuy_active() -> bool:
    """True when auto-buy has been explicitly enabled and not expired."""
    if not settings.ENABLE_AUTO_BUY:
        return False
    if _state.autobuy_enabled_until <= 0:
        return False
    if time.monotonic() > _state.autobuy_enabled_until:
        # Auto-disable
        settings.ENABLE_AUTO_BUY = False
        _state.autobuy_enabled_until = 0.0
        logger.info("Auto-buy 24h window expired — disabled.")
        asyncio.create_task(_send_admin(
            "⏰ *Auto-buy disabled* — 24-hour window expired.\n"
            "Use /enable_autobuy to re-enable."
        ))
        return False
    return True


# ---------------------------------------------------------------------------
# Telegram command handlers
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    _state.monitoring_active = True
    await message.answer(
        "🤖 **Multi-DEX Signal Bot started!**\n"
        "Monitoring Raydium, Orca, and Meteora pools in real-time."
    )


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    _state.monitoring_active = False
    await message.answer("⏸️ **Monitoring paused.** Use /start to resume.")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    status_text   = "🟢 Active" if _state.monitoring_active else "🔴 Paused"
    avg_time      = _speed_optimizer.get_avg_processing_time()
    cb_state      = circuit_breaker.get_state()
    autobuy_state = "🟢 ON" if _autobuy_active() else "🔴 OFF"
    autosell_state = "🟢 ON" if settings.ENABLE_AUTO_SELL else "🔴 OFF"
    await message.answer(
        f"🤖 **Bot Status:** {status_text}\n"
        f"🔍 **Tokens Scanned:** `{_state.processed_tokens_count}`\n"
        f"⚡ **Avg Pipeline Time:** `{avg_time:.2f}s`\n\n"
        f"🛡️ **Circuit Breaker:** `{cb_state.status_str()}`\n"
        f"💸 **Auto-Buy:** {autobuy_state} | **Auto-Sell:** {autosell_state}\n"
        f"📊 **Open Positions:** see /portfolio"
    )


@router.message(Command("sources"))
async def cmd_sources(message: Message) -> None:
    """Show per-DEX detection stats and latency."""
    stats = _multi_detector.get_source_stats()
    if not stats:
        await message.answer("No DEX source data available yet.")
        return
    lines = ["📡 **DEX Source Stats:**\n"]
    for s in stats:
        icon = "🟢" if s.is_connected else "🔴"
        lines.append(
            f"{icon} **{s.dex_name}**\n"
            f"   Pools: `{s.pools_detected}` | "
            f"Avg latency: `{s.avg_latency_ms:.0f}ms` | "
            f"Errors: `{s.connection_errors}`"
        )
    await message.answer("\n".join(lines))


@router.message(Command("lastsignals"))
async def cmd_last_signals(message: Message) -> None:
    try:
        signals = await get_recent_signals(limit=5)
        if not signals:
            await message.answer("No signals recorded yet.")
            return
        reply = "📊 **Last 5 Signals:**\n\n"
        for sig in signals:
            score_str = (
                f" | Score: {sig.get('composite_score', 0):.0f}/100"
                if sig.get("composite_score") else ""
            )
            reply += (
                f"🔹 **{sig['symbol']}** ({sig.get('dex_source', sig['signal_type'])})\n"
                f"   Price: ${sig['price_at_signal']:,.6f} | "
                f"Conf: {sig['confidence_score']:.1f}%{score_str}\n"
                f"   Sent at: {sig['sent_at']}\n\n"
            )
        await message.answer(reply)
    except Exception as exc:
        logger.error(f"Error fetching last signals: {exc}")
        await message.answer("Error retrieving signals from database.")


# ── Phase 3 commands ────────────────────────────────────────────────────────

@router.message(Command("portfolio"))
async def cmd_portfolio(message: Message) -> None:
    """Show all open positions with current P&L."""
    try:
        msg = await portfolio_tracker.format_portfolio_message()
        await message.answer(msg, parse_mode="Markdown")
    except Exception as exc:
        logger.error(f"portfolio command error: {exc}")
        await message.answer("⚠️ Could not fetch portfolio.")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Show today's performance stats."""
    try:
        msg = await portfolio_tracker.format_stats_message()
        await message.answer(msg, parse_mode="Markdown")
    except Exception as exc:
        logger.error(f"stats command error: {exc}")
        await message.answer("⚠️ Could not fetch stats.")


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    """Show last 10 closed trades."""
    try:
        msg = await portfolio_tracker.format_history_message(limit=10)
        await message.answer(msg, parse_mode="Markdown")
    except Exception as exc:
        logger.error(f"history command error: {exc}")
        await message.answer("⚠️ Could not fetch history.")


@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    """Show current SOL wallet balance."""
    try:
        balance = await swap_engine.get_sol_balance()
        wallet  = swap_engine.wallet_pubkey or "not configured"
        await message.answer(
            f"💰 *Wallet Balance*\n"
            f"Address: `{wallet}`\n"
            f"Balance: `{balance:.6f} SOL`",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error(f"balance command error: {exc}")
        await message.answer("⚠️ Could not fetch balance.")


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    """Manually pause auto-trading (circuit breaker)."""
    await circuit_breaker.manual_pause(reason="Telegram /pause command")
    _state.monitoring_active = False
    await message.answer(
        "⏸️ *Auto-trading paused.*\n"
        "Signal monitoring also paused.\n"
        "Use /resume to restart.",
        parse_mode="Markdown",
    )


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    """Manually resume auto-trading."""
    await circuit_breaker.manual_resume()
    _state.monitoring_active = True
    await message.answer(
        "▶️ *Auto-trading resumed.*",
        parse_mode="Markdown",
    )


@router.message(Command("enable_autobuy"))
async def cmd_enable_autobuy(message: Message) -> None:
    """
    Step 1: Start the enable_autobuy flow.
    User must respond with exactly "CONFIRM BUY" to activate.
    """
    if circuit_breaker.is_active():
        cb = circuit_breaker.get_state()
        await message.answer(
            f"🚫 *Cannot enable auto-buy.*\n"
            f"Circuit breaker is active: `{cb.level.value}`\n"
            f"Reason: {cb.reason}",
            parse_mode="Markdown",
        )
        return

    _state.autobuy_confirm_pending = True
    await message.answer(
        "⚠️ *Enable Auto-Buy Confirmation Required*\n\n"
        "Auto-buy will execute real trades using your wallet.\n"
        "This is a LIVE trading system — losses are possible.\n\n"
        "Type exactly: `CONFIRM BUY`\n"
        "(Auto-buy will disable itself after 24 hours)",
        parse_mode="Markdown",
    )


@router.message(Command("disable_autobuy"))
async def cmd_disable_autobuy(message: Message) -> None:
    """Immediately disable auto-buy."""
    settings.ENABLE_AUTO_BUY        = False
    _state.autobuy_enabled_until    = 0.0
    _state.autobuy_confirm_pending  = False
    await message.answer("🔴 *Auto-buy disabled.*", parse_mode="Markdown")


@router.message()
async def handle_text_message(message: Message) -> None:
    """
    Catch-all handler — processes the CONFIRM BUY confirmation.
    Must be the LAST handler registered on the router.
    """
    if (
        _state.autobuy_confirm_pending
        and message.text
        and message.text.strip() == "CONFIRM BUY"
    ):
        _state.autobuy_confirm_pending = False
        settings.ENABLE_AUTO_BUY      = True
        settings.ENABLE_AUTO_SELL     = True   # enable sell side too
        _state.autobuy_enabled_until  = time.monotonic() + 86_400  # 24 h
        await message.answer(
            "🟢 *Auto-buy ENABLED*\n"
            "Auto-sell also enabled.\n"
            f"Will auto-disable in 24 hours.\n"
            f"Use /disable_autobuy to stop at any time.",
            parse_mode="Markdown",
        )
        logger.warning("Auto-buy ENABLED by operator Telegram confirmation.")


# ── Phase 4: Backtesting commands ──────────────────────────────────────────

@router.message(Command("backtest_status"))
async def cmd_backtest_status(message: Message) -> None:
    """Show data collection progress and backtest readiness."""
    try:
        from backtesting.backtest_report import BacktestReporter
        msg = BacktestReporter.format_status_message_static()
        # Try to append live collection progress
        try:
            from backtesting.data_collector import DataCollector
            dc = DataCollector()
            await dc.start()
            prog = await dc.get_progress()
            await dc.stop()
            msg += (
                f"\n\n📥 *Collection Progress*\n"
                f"Found: `{prog.get('total_found', 0):,}` | "
                f"Saved: `{prog.get('total_saved', 0):,}`\n"
                f"Complete: `{prog.get('total_complete', 0):,}`\n"
                f"Helius credits: `{prog.get('helius_credits_used', 0):,}` / `800,000`\n"
                f"Status: `{prog.get('status', 'idle')}`"
            )
        except Exception:
            pass  # DB may not exist yet
        await message.answer(msg, parse_mode="Markdown")
    except Exception as exc:
        logger.error(f"backtest_status error: {exc}")
        await message.answer("⚠️ Backtest module not available.")



@router.message(Command("backtest_report"))
async def cmd_backtest_report(message: Message) -> None:
    """Generate and display the full backtest performance report."""
    try:
        await message.answer("⏳ Generating report...", parse_mode="Markdown")
        from backtesting.backtest_report import BacktestReporter
        reporter = BacktestReporter()
        full_msg = await reporter.get_full_report_message()
        # Telegram has 4096 char limit — split if needed
        for chunk in _split_message(full_msg, 4000):
            await message.answer(chunk, parse_mode="Markdown")
    except Exception as exc:
        logger.error(f"backtest_report error: {exc}")
        await message.answer(
            "⚠️ Report not available.\n"
            "Run the backtest engine first:\n"
            "`python -m backtesting.backtest_engine`",
            parse_mode="Markdown",
        )


@router.message(Command("model_accuracy"))
async def cmd_model_accuracy(message: Message) -> None:
    """Show current ML model precision / recall / AUC."""
    try:
        from backtesting.ml_optimizer import ml_optimizer
        accuracy_msg = await ml_optimizer.get_accuracy_message()
        feat_msg     = await ml_optimizer.get_feature_importance_report()
        await message.answer(accuracy_msg, parse_mode="Markdown")
        if feat_msg and feat_msg != "No feature importance data yet.":
            await message.answer(feat_msg, parse_mode="Markdown")
    except Exception as exc:
        logger.error(f"model_accuracy error: {exc}")
        await message.answer(
            "📭 *ML models not trained yet.*\n"
            "Use `/optimize` to start training.",
            parse_mode="Markdown",
        )


@router.message(Command("optimize"))
async def cmd_optimize(message: Message) -> None:
    """Trigger a new ML model optimization run in the background."""
    await message.answer(
        "🔬 *Optimization started in background.*\n"
        "This will:\n"
        "1. Run backtest grid search\n"
        "2. Train XGBoost + RandomForest models\n"
        "3. Update signal weights\n\n"
        "Use /model_accuracy to check progress.\n"
        "You'll be notified when complete.",
        parse_mode="Markdown",
    )

    async def _run_optimize() -> None:
        try:
            from backtesting.backtest_engine import BacktestEngine
            from backtesting.ml_optimizer   import MLOptimizer
            from backtesting.backtest_report import BacktestReporter

            engine = BacktestEngine()
            best   = await engine.run()
            await _send_admin(
                f"✅ *Grid search complete*\n"
                f"Best threshold: `{best.params.signal_threshold}`\n"
                f"Sharpe: `{best.sharpe_ratio:.3f}` | WR: `{best.win_rate:.1%}`"
            )

            opt = MLOptimizer()
            opt.set_alert_fn(_send_admin)
            await opt.start()
            metrics = await opt.run()
            await opt.stop()

            if metrics:
                lines = ["🤖 *ML Training complete*\n"]
                for name, m in metrics.items():
                    lines.append(
                        f"*{name}*: F1=`{m.f1:.3f}` AUC=`{m.roc_auc:.3f}`"
                    )
                await _send_admin("\n".join(lines))

            reporter = BacktestReporter()
            report   = await reporter.run()
            BacktestReporter.apply_optimal_params_to_settings()
            await _send_admin(
                report.telegram_summary
                + "\n\n✅ _Signal weights updated automatically._"
            )

        except Exception as exc:
            logger.error(f"optimize task failed: {exc}")
            await _send_admin(f"❌ *Optimization failed*\n`{exc}`")

    asyncio.create_task(_run_optimize())


# ── Helpers ────────────────────────────────────────────────────────────────

def _split_message(text: str, max_len: int = 4000) -> list:
    """Split a long string into chunks ≤ max_len chars, breaking on newlines."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ---------------------------------------------------------------------------
# Signal sender
# ---------------------------------------------------------------------------

async def send_signal(signal_message: str) -> None:
    """Send *signal_message* to the configured admin Telegram chat."""
    await _send_admin(signal_message)


# ---------------------------------------------------------------------------
# Main token-handling pipeline
# ---------------------------------------------------------------------------

async def handle_new_token(
    token_info: TokenInfo,
    pool: Optional[DetectedPool] = None,
) -> None:
    """
    Callback invoked for every new pool-creation event.

    Accepts an optional ``DetectedPool`` object (provided by
    ``MultiSourceDetector``).  When called from the legacy
    ``monitor_new_listings``, ``pool`` will be ``None``.

    Pipeline
    --------
    1.  Dedup check — skip already-processed tokens.
    2.  Persist raw token + detected-pool metadata.
    3.  Start holder tracking in the background.
    4.  Run all enrichment tasks concurrently (SpeedOptimizer.parallel_scan).
    5.  Persist enrichment results.
    6.  Evaluate signal gates (evaluate_signal).
    7.  Send Telegram alert when all gates pass.
    """
    if not _state.monitoring_active:
        logger.info(f"Monitoring paused — skipping {token_info.address}")
        return

    pipeline_start = time.monotonic()
    _addr = token_info.address
    _sym  = token_info.symbol

    try:
        # ── 1. Dedup ───────────────────────────────────────────────────
        if await token_exists(_addr):
            logger.debug(f"Already processed {_addr} — skipping.")
            return

        _state.processed_tokens_count += 1

        await event_bus.emit("token_detected", {
            "address": _addr,
            "symbol": _sym,
            "name": token_info.name,
            "price": token_info.price,
            "liquidity_usd": token_info.liquidity_usd,
            "market_cap": token_info.market_cap,
            "dex_source": getattr(token_info, "dex_source", "unknown"),
        })

        # ── 2. Persist raw token + pool metadata ───────────────────────
        await save_token(token_info.to_dict())

        if pool:
            await save_detected_pool({
                "mint_address": pool.mint_address,
                "dex_name":     pool.dex_name,
                "signature":    pool.signature,
                "block_time":   pool.block_time,
                "latency_ms":   pool.latency_ms,
            })

        # ── 3. Start background holder tracking ────────────────────────
        _holder_tracker.start_tracking(_addr)

        # ── 4. Concurrent enrichment ───────────────────────────────────
        _ts = pool.block_time if pool else int(time.time())

        wallet_fn = (
            (lambda: _wallet_analyzer.analyze_wallet(_addr, _ts))
            if settings.ENABLE_WALLET_ANALYSIS else None
        )
        social_fn = (
            (lambda: _social_scanner.analyze_social(_sym, _addr))
            if settings.ENABLE_SOCIAL_SIGNALS else None
        )
        rug_fn = (
            (lambda: _rug_detector.analyze_rug_risk(_addr, token_info))
            if settings.ENABLE_RUG_DETECTION else None
        )
        holder_velocity_fn = (
            (lambda: _holder_tracker.quick_analyze(_addr))
            if settings.ENABLE_HOLDER_VELOCITY else None
        )
        smart_money_fn = (
            (lambda: _buyer_analyzer.analyze(_addr, _ts))
            if settings.ENABLE_FIRST_BUYER_ANALYSIS else None
        )

        tx_pattern_fn = (
            (lambda: _tx_scorer.analyze(_addr, _ts))
            if settings.ENABLE_TX_PATTERN_SCORING else None
        )
        liquidity_growth_fn = (
            (lambda: _liq_analyzer.analyze(_addr))
            if settings.ENABLE_LIQUIDITY_GROWTH_ANALYSIS else None
        )
        cross_dex_fn = (
            (lambda: _cross_dex.analyze(_addr))
            if settings.ENABLE_CROSS_DEX_MONITORING else None
        )

        results = await _speed_optimizer.parallel_scan(
            token_address=_addr,
            token_info=token_info,
            security_fn=lambda: full_security_scan(_addr),
            wallet_fn=wallet_fn,
            social_fn=social_fn,
            rug_fn=rug_fn,
            analysis_fn=lambda: analyze_token(token_info),
            holder_velocity_fn=holder_velocity_fn,
            smart_money_fn=smart_money_fn,
            tx_pattern_fn=tx_pattern_fn,
            liquidity_growth_fn=liquidity_growth_fn,
            cross_dex_fn=cross_dex_fn,
        )

        security_res    = results["security"]
        wallet_result   = results["wallet"]
        social_result   = results["social"]
        rug_result      = results["rug"]
        analysis_res    = results["analysis"]
        velocity_result = results["holder_velocity"]
        sm_result       = results["smart_money"]
        txp_result      = results["tx_pattern"]
        liq_result      = results["liquidity_growth"]
        cdx_result      = results["cross_dex"]

        # ── 5. Persist enrichment results ──────────────────────────────
        if security_res:
            await save_scan({
                "token_address": _addr,
                "passed":        security_res.passed,
                "fail_reason":   security_res.fail_reason,
            })
            token_data = token_info.to_dict()
            token_data["is_honeypot"]  = not security_res.passed
            token_data["goplus_score"] = security_res.score
            await save_token(token_data)

        if wallet_result:
            await save_wallet_analysis({
                "token_address":     _addr,
                "creator_wallet":    wallet_result.creator_wallet,
                "wallet_age_days":   wallet_result.wallet_age_days,
                "sniper_count":      wallet_result.sniper_count,
                "sniper_percentage": wallet_result.sniper_percentage,
                "wallet_score":      wallet_result.wallet_score,
                "risk_level":        wallet_result.risk_level,
            })

        if social_result:
            await save_social_signals({
                "token_address":    _addr,
                "mention_count_1h": social_result.mention_count_1h,
                "mention_velocity": social_result.mention_velocity,
                "sentiment_score":  social_result.sentiment_score,
                "has_viral_tweet":  social_result.has_viral_tweet,
                "social_score":     social_result.social_score,
            })

        if rug_result:
            await save_rug_analysis({
                "token_address":   _addr,
                "rug_probability": rug_result.rug_probability,
                "pattern_score":   rug_result.pattern_score,
                "risk_flags":      rug_result.risk_flags,
                "recommendation":  rug_result.recommendation,
            })

        if velocity_result:
            await save_holder_velocity_snapshot({
                "token_address":        _addr,
                "holder_count":         velocity_result.current_holders,
                "holders_per_minute":   velocity_result.holders_per_minute,
                "velocity_label":       velocity_result.velocity_label,
                "is_rug_warning":       velocity_result.is_rug_warning,
                "holder_velocity_score": velocity_result.holder_velocity_score,
            })

        if sm_result:
            await save_first_buyer_analysis({
                "token_address":    _addr,
                "first_buyers":     sm_result.first_buyers,
                "smart_money_count": sm_result.smart_money_count,
                "smart_money_pct":  sm_result.smart_money_pct,
                "smart_money_score": sm_result.smart_money_score,
                "analyzed_wallets": sm_result.analyzed_wallets,
                "data_source":      sm_result.data_source,
            })

        if txp_result:
            await save_tx_pattern({
                "token_address":      _addr,
                "buy_count":          txp_result.buy_count,
                "sell_count":         txp_result.sell_count,
                "total_txs":          txp_result.total_txs,
                "buy_ratio":          txp_result.buy_ratio,
                "wash_trade_count":   txp_result.wash_trade_count,
                "is_artificial_pump": txp_result.is_artificial_pump,
                "tx_pattern_score":   txp_result.tx_pattern_score,
            })

        if liq_result:
            await save_liquidity_growth({
                "token_address":          _addr,
                "candle_count":           liq_result.candle_count,
                "total_volume":           liq_result.total_volume,
                "first_candle_vol_pct":   liq_result.first_candle_vol_pct,
                "volume_cv":              liq_result.volume_cv,
                "growth_pattern":         liq_result.growth_pattern,
                "growth_rate_pct":        liq_result.growth_rate_pct,
                "liquidity_growth_score": liq_result.liquidity_growth_score,
            })

        if cdx_result:
            await save_cross_dex_result({
                "token_address":  _addr,
                "source_count":   cdx_result.source_count,
                "price_gap_pct":  cdx_result.price_gap_pct,
                "gap_label":      cdx_result.gap_label,
                "is_manipulated": cdx_result.is_manipulated,
                "cross_dex_score": cdx_result.cross_dex_score,
                "prices":         cdx_result.prices,
            })

        # ── 6. Gate evaluation ─────────────────────────────────────────
        if not security_res:
            logger.warning(f"Security scan timed out for {_addr} — skipping.")
            return

        if not security_res.passed:
            logger.info(
                f"Token {_addr} failed security: "
                f"status={security_res.scan_status}, "
                f"reason={security_res.fail_reason}"
            )
            return

        if not analysis_res:
            logger.warning(f"Technical analysis timed out for {_addr} — skipping.")
            return

        decision = evaluate_signal(
            token_info,
            security_res,
            analysis_res,
            wallet_analysis=wallet_result,
            rug_analysis=rug_result,
            social_signals=social_result,
            holder_velocity=velocity_result,
            smart_money=sm_result,
            tx_pattern=txp_result,
            liquidity_growth=liq_result,
            cross_dex=cdx_result,
        )

        await event_bus.emit("signal", {
            "address": _addr,
            "symbol": _sym,
            "composite_score": decision.composite_score,
            "confidence_score": analysis_res.confidence_score if analysis_res else 0,
            "decision": "BUY" if decision.send else "SKIP",
            "reason": decision.reason,
        })

        # ── 7. Send alert + auto-buy logic ────────────────────────────────
        if decision.send:
            msg = format_signal_message(
                token_info,
                security_res,
                analysis_res,
                wallet_analysis=wallet_result,
                rug_analysis=rug_result,
                social_signals=social_result,
                holder_velocity=velocity_result,
                smart_money=sm_result,
                tx_pattern=txp_result,
                liquidity_growth=liq_result,
                cross_dex=cdx_result,
                composite_score=decision.composite_score,
            )
            await send_signal(msg)
            dex_name = getattr(token_info, "dex_source", "Raydium_AMM")
            await save_signal({
                "token_address":    _addr,
                "signal_type":      f"{dex_name} Pool Launch",
                "price_at_signal":  token_info.price,
                "confidence_score": analysis_res.confidence_score,
                "composite_score":  decision.composite_score,
                "dex_source":       dex_name,
                "result":           "Alerted",
            })

            # ── 8. Auto-buy (───────────────────────────────────────────
            if decision.buy_recommended:
                if settings.ENABLE_CIRCUIT_BREAKER and circuit_breaker.is_active():
                    cb_state = circuit_breaker.get_state()
                    logger.info(
                        f"Auto-buy skipped for {_addr}: "
                        f"circuit breaker {cb_state.level.value}"
                    )
                    await send_signal(
                        f"⚠️ *SIGNAL (manual buy required)*\n"
                        f"Token: *{token_info.symbol}*\n"
                        f"Score: `{decision.composite_score:.0f}/100`\n"
                        f"Circuit breaker: `{cb_state.level.value}`\n"
                        f"Use /portfolio to check positions."
                    )
                elif _autobuy_active():
                    # Fire-and-forget buy; monitor loop is started inside execute_buy
                    buy_result = await swap_engine.execute_buy(
                        mint_address=_addr,
                        symbol=token_info.symbol,
                        signal_score=decision.composite_score,
                    )
                    if buy_result.success:
                        await event_bus.emit("trade_executed", {
                            "type": "buy",
                            "mint": _addr,
                            "symbol": token_info.symbol,
                            "sol_amount": buy_result.entry_sol,
                            "entry_price": buy_result.entry_price,
                            "tokens_received": buy_result.tokens_received,
                            "signal_score": buy_result.signal_score,
                            "trade_id": buy_result.trade_id,
                            "tx_signature": buy_result.tx_signature,
                        })
                        await send_signal(
                            SwapEngine.format_buy_alert(
                                symbol=token_info.symbol,
                                sol_amount=buy_result.entry_sol,
                                entry_price=buy_result.entry_price,
                                score=buy_result.signal_score,
                            )
                        )
                    else:
                        logger.warning(
                            f"Auto-buy failed for {_addr}: {buy_result.error}"
                        )
                else:
                    # Auto-buy not enabled — send manual alert
                    await send_signal(
                        f"🟡 *SIGNAL: manual buy recommended*\n"
                        f"Token: *{token_info.symbol}*\n"
                        f"Score: `{decision.composite_score:.0f}/100`\n"
                        f"Use /enable_autobuy to automate buys."
                    )

        else:
            logger.info(
                f"Signal rejected for {_addr}: "
                f"score={decision.composite_score:.1f} | {decision.reason}"
            )

    except Exception as exc:
        logger.error(
            f"Unhandled error in handle_new_token for {_addr}: {exc}",
            exc_info=True,
        )
    finally:
        _speed_optimizer.record_processing_time(pipeline_start)


# ---------------------------------------------------------------------------
# save_signal update — accept composite_score and dex_source
# ---------------------------------------------------------------------------
# (The existing save_signal in sqlite_client.py uses the dict keys directly;
# adding composite_score and dex_source columns to the INSERT requires the
# table to have those columns.  They were added in the Phase 1 models.py
# update above.)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def start_polling() -> None:
    """
    Initialise the database, start the pool listener(s), and
    begin Telegram bot polling.

    When ``ENABLE_MULTI_SOURCE_DETECTION`` is True (default), uses
    ``MultiSourceDetector`` which covers 5 DEX sources simultaneously.
    Falls back to the legacy single-source Raydium listener otherwise.
    """
    logger.info("Initializing database...")
    await init_db()   # also wires portfolio_tracker + circuit_breaker

    # Wire swap_engine dependencies
    swap_engine.inject(
        portfolio_tracker=portfolio_tracker,
        circuit_breaker=circuit_breaker,
        alert_fn=_send_admin,
    )
    await swap_engine.start()

    # Seed today's starting balance
    balance = await swap_engine.get_sol_balance()
    await portfolio_tracker.set_starting_balance(balance)

    # Choose detector
    if settings.ENABLE_MULTI_SOURCE_DETECTION:
        detector_coro = _multi_detector.start(handle_new_token)
        logger.info("Using MultiSourceDetector (5 DEX sources).")
    else:
        # Legacy single-source fallback
        async def _legacy(token_info: TokenInfo) -> None:  # type: ignore
            await handle_new_token(token_info, pool=None)
        detector_coro = monitor_new_listings(_legacy)
        logger.info("Using legacy single-source Raydium listener.")

    # Start background holder-velocity polling loop
    holder_loop = asyncio.create_task(
        _holder_tracker.run_background_loop(interval_s=15.0)
    )

    if not bot:
        logger.warning(
            "TELEGRAM_TOKEN is placeholder — "
            "Telegram features disabled. Running in standalone monitoring mode."
        )
        await asyncio.gather(detector_coro, holder_loop, return_exceptions=True)
    else:
        logger.info("Starting pool detection as background task...")
        asyncio.create_task(detector_coro)
        logger.info("Starting Telegram bot polling...")
        await dp.start_polling(bot)


async def start_dashboard_and_bot() -> None:
    await start_polling()


async def _stats_broadcast_loop() -> None:
    from trading.portfolio_tracker import portfolio_tracker
    from trading.swap_engine import swap_engine
    while True:
        await asyncio.sleep(5.0)
        try:
            balance = await swap_engine.get_sol_balance()
            daily_pnl = await portfolio_tracker.get_daily_pnl()
            trades = await portfolio_tracker.get_open_trades()
            await event_bus.emit("stats_update", {
                "sol_balance": balance,
                "daily_pnl": daily_pnl,
                "open_positions": len(trades),
                "processed_tokens": _state.processed_tokens_count,
            })
        except Exception:
            pass
