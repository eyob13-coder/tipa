import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.db.session import init_db
from app.chapa.client import chapa_client
from app.bot.bot import get_telegram_application
from app.bot.reminders import run_tip_reminder_loop
from app.webhooks.chapa_webhook import router as webhook_router
from app.api.routes import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

bot_task: asyncio.Task = None
BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task
    logger.info("Initializing database...")
    await init_db()

    logger.info("Pre-caching bank list from Chapa...")
    try:
        banks = await asyncio.wait_for(chapa_client.list_banks(), timeout=3.0)
        logger.info(f"Loaded {len(banks)} banks.")
    except Exception as e:
        logger.info(f"Using default offline bank list ({e})")

    # Initialize Telegram Bot
    if settings.bot_token and settings.bot_token != "sandbox_bot_token":
        try:
            logger.info("Starting Telegram Bot Application...")
            telegram_app = get_telegram_application()
            await telegram_app.initialize()
            await telegram_app.bot.delete_webhook(drop_pending_updates=False)
            await telegram_app.start()
            if telegram_app.updater:
                await telegram_app.updater.start_polling(
                    allowed_updates=["message", "edited_message", "channel_post", "edited_channel_post", "callback_query", "inline_query"],
                    drop_pending_updates=False,
                )
            logger.info("Telegram Bot started successfully & listening for updates.")

            # Start background tip reminder / expiry loop
            bot_task = asyncio.create_task(run_tip_reminder_loop())
            logger.info("Background tip reminder loop started.")
        except Exception as e:
            logger.error(f"Failed to start Telegram Bot: {e}")

    yield

    # Shutdown bot
    if settings.bot_token and settings.bot_token != "sandbox_bot_token":
        try:
            logger.info("Stopping Telegram Bot...")
            if bot_task is not None:
                bot_task.cancel()
                try:
                    await bot_task
                except asyncio.CancelledError:
                    pass
            telegram_app = get_telegram_application()
            if telegram_app.updater:
                await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception as e:
            logger.error(f"Error stopping Telegram bot: {e}")

    logger.info("Application shutdown complete.")


app = FastAPI(
    title="Tipa API & Telegram Mini App",
    description="Telegram Tipping for Ethiopian Creators via Telebirr & CBE",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static files
static_path = BASE_DIR / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

app.include_router(api_router)
app.include_router(webhook_router)


@app.get("/")
async def root():
    return {
        "app": "Tipa",
        "status": "running",
        "bot": settings.bot_username,
        "miniapp": "/miniapp",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/miniapp", response_class=FileResponse)
async def serve_miniapp():
    """Serve Telegram Mini App single-page interface."""
    index_file = BASE_DIR / "static" / "index.html"
    if not index_file.exists():
        return FileResponse(str(BASE_DIR / "static" / "index.html"))
    return FileResponse(str(index_file))
