import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from api.event_bus import event_bus
from config.settings import settings, runtime_state
from database.sqlite_client import _get_db, get_recent_signals

# ---------------------------------------------------------------------------
# Startup timestamp
# ---------------------------------------------------------------------------
_start_time: float = time.time()

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
_request_counts: dict[str, list[float]] = {}
_RATE_LIMIT = 100
_RATE_WINDOW = 60.0

def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    window = _RATE_WINDOW
    counts = _request_counts.get(ip, [])
    counts = [t for t in counts if now - t < window]
    if len(counts) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    counts.append(now)
    _request_counts[ip] = counts

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
async def verify_api_key(request: Request) -> None:
    key = request.headers.get("X-API-Key", "")
    if key != settings.DASHBOARD_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Dashboard API server started.")
    yield
    logger.info("Dashboard API server stopped.")

app = FastAPI(title="Solana Bot Dashboard API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status(_=Depends(verify_api_key)):
    from trading.swap_engine import swap_engine
    from trading.circuit_breaker import circuit_breaker

    balance = await swap_engine.get_sol_balance()
    cb = circuit_breaker.get_state()

    return {
        "bot_running": True,
        "auto_buy_enabled": shared_state.autobuy_enabled,
        "auto_sell_enabled": shared_state.autosell_enabled,
        "circuit_breaker_active": cb.is_active(),
        "circuit_breaker_level": cb.level.value,
        "uptime_seconds": int(time.time() - _start_time),
        "sol_balance": balance,
        "version": "1.0.0",
    }


@app.get("/api/portfolio")
async def get_portfolio(_=Depends(verify_api_key)):
    from trading.portfolio_tracker import portfolio_tracker
    from trading.swap_engine import swap_engine

    trades = await portfolio_tracker.get_open_trades()

    balance = await swap_engine.get_sol_balance()
    daily_pnl = await portfolio_tracker.get_daily_pnl()
    today_stats = await portfolio_tracker.get_today_stats()

    total_position_value = sum(
        t.entry_sol * (t.current_price / t.entry_price) if t.entry_price > 0 else 0
        for t in trades
    )
    total_sol = balance + total_position_value

    open_positions = []
    for t in trades:
        pnl_pct = ((t.current_price - t.entry_price) / t.entry_price * 100) if t.entry_price > 0 else 0
        open_positions.append({
            "mint": t.mint_address,
            "symbol": t.symbol,
            "entry_price": t.entry_price,
            "current_price": t.current_price,
            "pnl_percent": round(pnl_pct, 2),
            "pnl_sol": round(t.unrealized_pnl_sol, 6),
            "amount_sol": t.entry_sol,
            "entry_time": t.entry_time,
            "take_profit_targets": ["2x", "5x", "10x"],
            "stop_loss_price": round(t.entry_price * 0.6, 12) if t.entry_price > 0 else 0,
            "signal_score": t.signal_score,
        })

    return {
        "total_sol": round(total_sol, 6),
        "daily_pnl_sol": round(daily_pnl, 6),
        "daily_pnl_percent": round(
            (daily_pnl / today_stats.starting_balance_sol * 100)
            if today_stats and today_stats.starting_balance_sol > 0 else 0, 2
        ),
        "open_positions": open_positions,
        "closed_today": today_stats.trades_total if today_stats else 0,
        "won_today": today_stats.trades_won if today_stats else 0,
        "lost_today": today_stats.trades_lost if today_stats else 0,
    }


@app.get("/api/signals")
async def get_signals(limit: int = 50, _=Depends(verify_api_key)):
    signals = await get_recent_signals(limit=limit)
    return {"signals": signals, "count": len(signals)}


@app.get("/api/trades")
async def get_trades(limit: int = 100, status: str = "all", _=Depends(verify_api_key)):
    from trading.portfolio_tracker import portfolio_tracker

    if status == "open":
        trades = await portfolio_tracker.get_open_trades()
    elif status == "closed":
        trades = await portfolio_tracker.get_closed_trades(limit=limit)
    else:
        trades = await portfolio_tracker.get_open_trades()
        trades.extend(await portfolio_tracker.get_closed_trades(limit=limit))

    trade_list = []
    for t in trades:
        trade_list.append({
            "id": t.id,
            "mint": t.mint_address,
            "symbol": t.symbol,
            "entry_price": t.entry_price,
            "current_price": t.current_price,
            "entry_sol": t.entry_sol,
            "tokens_received": t.tokens_received,
            "realized_pnl_sol": t.realized_pnl_sol,
            "unrealized_pnl_sol": t.unrealized_pnl_sol,
            "status": t.status,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "exit_reason": t.exit_reason,
            "signal_score": t.signal_score,
        })

    return {"trades": trade_list, "count": len(trade_list)}


@app.get("/api/stats")
async def get_stats(_=Depends(verify_api_key)):
    from trading.portfolio_tracker import portfolio_tracker

    today_stats = await portfolio_tracker.get_today_stats()
    closed = await portfolio_tracker.get_closed_trades(limit=500)
    open_trades = await portfolio_tracker.get_open_trades()

    total_trades = len(closed) + len(open_trades)
    won = sum(1 for t in closed if t.realized_pnl_sol > 0)
    lost = sum(1 for t in closed if t.realized_pnl_sol <= 0)
    win_rate = (won / (won + lost) * 100) if (won + lost) > 0 else 0

    profits = [t.realized_pnl_sol for t in closed if t.realized_pnl_sol > 0]
    losses = [t.realized_pnl_sol for t in closed if t.realized_pnl_sol <= 0]
    avg_profit = sum(profits) / len(profits) if profits else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    gross_profit = sum(profits)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    best = max(closed, key=lambda t: t.realized_pnl_sol) if closed else None
    worst = min(closed, key=lambda t: t.realized_pnl_sol) if closed else None

    return {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "avg_profit": round(avg_profit, 6),
        "avg_loss": round(avg_loss, 6),
        "profit_factor": round(profit_factor, 2),
        "best_trade": round(best.realized_pnl_sol, 6) if best else 0,
        "worst_trade": round(worst.realized_pnl_sol, 6) if worst else 0,
        "open_positions": len(open_trades),
        "total_pnl_today": round(today_stats.total_pnl_sol, 6) if today_stats else 0,
    }


@app.get("/api/backtest")
async def get_backtest(_=Depends(verify_api_key)):
    try:
        from backtesting.backtest_report import BacktestReporter
        reporter = BacktestReporter()
        report = await reporter.run()
        return {
            "tokens_analyzed": report.tokens_analyzed,
            "trades_simulated": report.trades_simulated,
            "win_rate": report.win_rate,
            "profit_factor": report.profit_factor,
            "max_drawdown": report.max_drawdown,
            "sharpe_ratio": report.sharpe_ratio,
            "optimal_threshold": report.optimal_threshold,
            "telegram_summary": report.telegram_summary,
        }
    except Exception as exc:
        return {"error": str(exc), "message": "Backtest report not yet available"}


@app.get("/api/ml/accuracy")
async def get_ml_accuracy(_=Depends(verify_api_key)):
    try:
        from backtesting.ml_optimizer import ml_optimizer
        metrics = {}
        for name, model_data in ml_optimizer._models.items() if hasattr(ml_optimizer, '_models') else {}.items():
            metrics[name] = {
                "f1": model_data.f1 if hasattr(model_data, 'f1') else 0,
                "roc_auc": model_data.roc_auc if hasattr(model_data, 'roc_auc') else 0,
                "precision": model_data.precision if hasattr(model_data, 'precision') else 0,
                "recall": model_data.recall if hasattr(model_data, 'recall') else 0,
            } if model_data else {}
        return {
            "models": metrics,
            "last_trained": getattr(ml_optimizer, '_last_trained', None),
            "training_data_size": getattr(ml_optimizer, '_training_size', 0),
        }
    except Exception as exc:
        return {"error": str(exc), "models": {}}


@app.post("/api/control")
async def control_bot(body: dict[str, Any], _=Depends(verify_api_key)):
    action = body.get("action", "")
    value = body.get("value")

    from trading.circuit_breaker import circuit_breaker
    from trading.swap_engine import swap_engine
    from core.shared_state import shared_state

    if action == "enable_autobuy":
        shared_state.autobuy_enabled = True
        return {"success": True, "message": "Auto-buy enabled"}
    elif action == "disable_autobuy":
        shared_state.autobuy_enabled = False
        return {"success": True, "message": "Auto-buy disabled"}
    elif action == "enable_autosell":
        shared_state.autosell_enabled = True
        return {"success": True, "message": "Auto-sell enabled"}
    elif action == "disable_autosell":
        shared_state.autosell_enabled = False
        return {"success": True, "message": "Auto-sell disabled"}
    elif action == "set_position_size":
        val = float(value or 0.1)
        runtime_state.max_position_size_sol = max(0.01, min(1.0, val))
        return {"success": True, "message": f"Position size set to {runtime_state.max_position_size_sol} SOL"}
    elif action == "set_min_score":
        val = float(value or 0.5)
        runtime_state.min_signal_score_for_buy = max(10.0, min(100.0, val * 100.0))
        return {"success": True, "message": f"Min score set to {runtime_state.min_signal_score_for_buy}"}
    elif action == "emergency_stop_all":
        shared_state.autobuy_enabled = False
        shared_state.autosell_enabled = False
        await circuit_breaker.manual_pause(reason="Emergency stop from dashboard")
        return {"success": True, "message": "Emergency stop activated"}
    elif action == "resume":
        await circuit_breaker.manual_resume()
        return {"success": True, "message": "Bot resumed"}
    elif action == "pause_minutes":
        mins = int(value or 5)
        import time
        class _TempLevel:
            OK = "OK"
            MANUAL_PAUSE = "MANUAL_PAUSE"
        from trading.circuit_breaker import BreakerLevel
        await circuit_breaker._trigger(BreakerLevel.MANUAL_PAUSE, f"Dashboard pause {mins}min", pause_seconds=mins * 60)
        return {"success": True, "message": f"Paused for {mins} minutes"}

    return {"success": False, "message": f"Unknown action: {action}"}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    api_key = ws.headers.get("x-api-key", ws.query_params.get("api_key", ""))
    if api_key != settings.DASHBOARD_API_KEY:
        await ws.close(code=4001)
        return

    await ws.accept()
    q = await event_bus.subscribe()

    try:
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=15.0)
                await ws.send_text(payload)
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "ping", "data": {}, "timestamp": time.time()}))
    except WebSocketDisconnect:
        pass
    finally:
        await event_bus.unsubscribe(q)


# ---------------------------------------------------------------------------
# Health check (unauthenticated)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "uptime": int(time.time() - _start_time)}


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def run_dashboard_server() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.DASHBOARD_PORT, log_level="info")


async def start_dashboard_server() -> None:
    import uvicorn
    config = uvicorn.Config(app, host="0.0.0.0", port=settings.DASHBOARD_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
