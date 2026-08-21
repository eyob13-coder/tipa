import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from telegram import Update as TelegramUpdate

from app.api.routes import router as api_router
from app.bot.bot import get_telegram_application
from app.bot.digest import run_weekly_digest_loop
from app.bot.reminders import run_tip_reminder_loop
from app.config import settings
from app.db.session import AsyncSessionLocal, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

bot_task: asyncio.Task = None
digest_task: asyncio.Task = None
BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task, digest_task

    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
            logger.info("Sentry error tracking initialized.")
        except ImportError:
            logger.warning("SENTRY_DSN set but sentry-sdk is not installed — skipping.")

    # Schema is managed by Alembic in production; create_all only for dev/SQLite.
    if settings.auto_create_tables or settings.database_url.startswith("sqlite"):
        logger.info("Initializing database (create_all)...")
        await init_db()
    else:
        logger.info("Skipping create_all — schema managed by Alembic migrations.")

    # Fail-closed guards: make misconfiguration loud instead of silently open.
    if settings.is_production:
        if not settings.bot_token:
            logger.error(
                "APP_ENV=production without BOT_TOKEN — the Mini App API rejects "
                "all requests (fail closed) until a token is configured."
            )
        if settings.telegram_webhook_url and not settings.telegram_webhook_secret:
            logger.warning(
                "TELEGRAM_WEBHOOK_URL is set but TELEGRAM_WEBHOOK_SECRET is empty — "
                "the webhook endpoint will reject every update."
            )

    # Initialize Telegram Bot
    if settings.bot_token and settings.bot_token != "sandbox_bot_token":
        try:
            logger.info("Starting Telegram Bot Application...")
            telegram_app = get_telegram_application()
            await telegram_app.initialize()
            await telegram_app.start()
            if settings.telegram_webhook_url:
                webhook_url = settings.telegram_webhook_url.rstrip("/")
                secret_token = settings.telegram_webhook_secret or None
                await telegram_app.bot.set_webhook(
                    url=f"{webhook_url}/telegram/webhook",
                    secret_token=secret_token,
                    allowed_updates=[
                        "message",
                        "edited_message",
                        "channel_post",
                        "edited_channel_post",
                        "callback_query",
                        "inline_query",
                    ],
                    drop_pending_updates=False,
                )
                logger.info("Telegram Bot started in webhook mode: %s", webhook_url)
            else:
                await telegram_app.bot.delete_webhook(drop_pending_updates=False)
                if telegram_app.updater:
                    await telegram_app.updater.start_polling(
                        allowed_updates=[
                            "message",
                            "edited_message",
                            "channel_post",
                            "edited_channel_post",
                            "callback_query",
                            "inline_query",
                        ],
                        drop_pending_updates=False,
                    )
                logger.info("Telegram Bot started successfully & listening for updates.")

            # Start background tip reminder / expiry loop
            bot_task = asyncio.create_task(run_tip_reminder_loop())
            logger.info("Background tip reminder loop started.")
            digest_task = asyncio.create_task(run_weekly_digest_loop())
            logger.info("Weekly digest loop started.")
        except Exception as e:  # noqa: BLE001 - app must still boot even if the bot fails
            logger.error(f"Failed to start Telegram Bot: {e}")

    yield

    # Shutdown bot
    if settings.bot_token and settings.bot_token != "sandbox_bot_token":
        try:
            logger.info("Stopping Telegram Bot...")
            for task in (bot_task, digest_task):
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            telegram_app = get_telegram_application()
            if telegram_app.updater:
                await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception as e:  # noqa: BLE001 - shutdown must never crash the process
            logger.error(f"Error stopping Telegram bot: {e}")

    logger.info("Application shutdown complete.")


app = FastAPI(
    title="Tipa API & Telegram Mini App",
    description="Telegram Tipping for Ethiopian Creators via Telebirr & CBE",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s -> %s (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Baseline security headers for the Mini App and API.

    The Mini App runs inside Telegram's iframe, so frame-ancestors must allow
    web.telegram.org (X-Frame-Options would break it entirely).
    """
    response = await call_next(request)
    response.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'self' https://web.telegram.org https://telegram-web-app.ssl.do;",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-XSS-Protection", "0")
    return response

# Mount static files
static_path = BASE_DIR / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

app.include_router(api_router)


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


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram updates in webhook mode.

    Only active when TELEGRAM_WEBHOOK_URL is configured; every request must
    carry the shared secret Telegram echoes back in X-Telegram-Bot-Api-Secret-Token.
    """
    if not settings.telegram_webhook_url:
        raise HTTPException(status_code=404, detail="Webhook mode is disabled")
    secret = settings.telegram_webhook_secret
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or not hmac.compare_digest(secret.encode(), provided.encode()):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    data = await request.json()
    telegram_app = get_telegram_application()
    update = TelegramUpdate.de_json(data, telegram_app.bot)
    if update is None:
        raise HTTPException(status_code=400, detail="Invalid update payload")
    await telegram_app.update_queue.put(update)
    return {"ok": True}


@app.get("/ready")
async def ready():
    """Readiness probe: verifies the database connection is alive."""
    try:
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ready", "database": "ok"}
    except SQLAlchemyError as e:
        logger.error("readiness check failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "error"},
        )


@app.get("/metrics")
async def metrics():
    """Lightweight SLO metrics: verification outcomes per provider (last 24h)."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from app.db.models import VerificationLog

    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(
                    VerificationLog.provider,
                    VerificationLog.status,
                    func.count(VerificationLog.id),
                )
                .where(VerificationLog.created_at >= day_ago)
                .group_by(VerificationLog.provider, VerificationLog.status)
            )
            rows = (await session.execute(stmt)).all()
    except Exception as e:  # noqa: BLE001 - metrics must never 500 (DB down, DNS, etc.)
        logger.error("metrics query failed: %s", e)
        return JSONResponse(status_code=503, content={"error": "database unavailable"})

    providers: dict[str, dict[str, int]] = {}
    for provider, status, count in rows:
        bucket = providers.setdefault(provider, {})
        bucket[status] = bucket.get(status, 0) + count
    return {"window_hours": 24, "providers": providers}


@app.get("/overlay/{creator_id}")
async def overlay_page(creator_id):
    """OBS browser-source page showing live tip alerts for one creator."""
    from app.overlay import render_overlay_page

    return HTMLResponse(render_overlay_page(creator_id))


@app.get("/overlay/{creator_id}/stream")
async def overlay_stream(creator_id: str):
    """SSE stream of verified-tip events for the overlay page."""
    import asyncio

    from app.overlay import _KEEPALIVE_SECONDS, format_sse, subscribe, unsubscribe

    queue = subscribe(creator_id)

    async def event_stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_KEEPALIVE_SECONDS
                    )
                    yield format_sse(event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            unsubscribe(creator_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/miniapp", response_class=FileResponse)
async def serve_miniapp():
    """Serve Telegram Mini App single-page interface."""
    index_file = BASE_DIR / "static" / "index.html"
    if not index_file.exists():
        return FileResponse(str(BASE_DIR / "static" / "index.html"))
    return FileResponse(str(index_file))
