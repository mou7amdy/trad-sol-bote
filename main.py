import asyncio
import sys
from loguru import logger
from bot.tg_bot import start_polling, _stats_broadcast_loop
from config.settings import settings

async def _run_all():
    tasks = [asyncio.create_task(start_polling())]

    if settings.ENABLE_DASHBOARD:
        try:
            from api.event_bus import event_bus
            _ = event_bus  # ensure import works
            tasks.append(asyncio.create_task(_stats_broadcast_loop()))
            tasks.append(asyncio.create_task(_start_api_server()))
            logger.info(f"Dashboard API starting on port {settings.DASHBOARD_PORT}")
        except ImportError as exc:
            logger.warning(f"Dashboard API not available: {exc}")

    await asyncio.gather(*tasks, return_exceptions=True)


async def _start_api_server():
    import uvicorn
    from api.main import app
    config = uvicorn.Config(app, host="0.0.0.0", port=settings.DASHBOARD_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


def main():
    logger.info("Starting Multi-Chain Signal Bot + Dashboard API...")
    placeholders = settings.check_placeholders()
    if placeholders:
        logger.warning(
            f"Placeholder config keys: {', '.join(placeholders)}. "
            "Set real values in .env for live operations."
        )
    try:
        asyncio.run(_run_all())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
