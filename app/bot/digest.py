"""Weekly digest DM: earned this week, top fan with tier badge, goal progress.

Cadence is per-creator anchored (every 7 days since their last digest) rather
than calendar-Monday so restarts and multiple app instances never double-send:
``creators.last_weekly_digest_at`` is the dedup marker.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from telegram.error import TelegramError

from app.config import settings
from app.db.models import Creator, Tip
from app.db.session import AsyncSessionLocal
from app.fans import fan_tier, top_fan_of_week
from app.i18n import t

logger = logging.getLogger(__name__)


def _fmt(value) -> str:
    return f"{float(value):,.2f}"


async def build_weekly_digest(session, creator: Creator, now: datetime | None = None) -> str | None:
    """Digest text for a creator, or None when they had no tips this week."""
    now = now or datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    lang = creator.language or "en"

    earned, count = (
        await session.execute(
            select(func.coalesce(func.sum(Tip.amount), 0), func.count(Tip.id))
            .where(Tip.creator_id == creator.id)
            .where(Tip.status == "success")
            .where(Tip.verified_at >= week_ago)
        )
    ).first() or (0, 0)

    if not count:
        return None

    top = await top_fan_of_week(session, creator.id, now=now)

    from app.goals import get_active_goal, goal_progress_line, goal_raised_amount

    goal_line = ""
    goal = await get_active_goal(session, creator.id)
    if goal:
        current = await goal_raised_amount(session, goal)
        goal_line = "\n" + goal_progress_line(goal, current).replace("*", "") + "\n"

    top_line = ""
    if top:
        tier_total = await _all_time_total(session, creator.id, top["telegram_id"])
        top_line = (
            f"🏆 {t(lang, 'digest_top_fan')}: {top['name']} — {_fmt(top['total'])} ETB "
            f"({fan_tier(tier_total)})\n"
        )

    return t(
        lang,
        "digest_text",
        earned=_fmt(earned),
        count=count,
        top_line=top_line,
        goal_line=goal_line,
        method=creator.payment_method.upper(),
    )


async def _all_time_total(session, creator_id, tipper_telegram_id):
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Tip.amount), 0)).where(
                Tip.creator_id == creator_id,
                Tip.tipper_telegram_id == tipper_telegram_id,
                Tip.status == "success",
            )
        )
    ).scalar_one()
    return total


async def send_due_digests(bot, now: datetime | None = None) -> int:
    """DM digests to creators whose weekly window elapsed. Returns send count."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.weekly_digest_days)
    sent = 0

    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(
            (Creator.last_weekly_digest_at.is_(None)) | (Creator.last_weekly_digest_at < cutoff)
        )
        creators = (await session.execute(stmt)).scalars().all()

        for creator in creators:
            try:
                digest = await build_weekly_digest(session, creator, now=now)
            except Exception:
                logger.exception("Failed to build digest for creator %s", creator.telegram_id)
                continue
            # Mark even when there was nothing to send, so quiet creators are
            # not re-scanned on every pass; their next digest is a week away.
            creator.last_weekly_digest_at = now
            await session.commit()

            if not digest:
                continue
            try:
                await bot.send_message(chat_id=creator.telegram_id, text=digest, parse_mode="Markdown")
                sent += 1
            except TelegramError as e:
                logger.warning("Could not deliver digest to %s: %s", creator.telegram_id, e)

    return sent


async def run_weekly_digest_loop() -> None:
    """Background companion to the reminder loop; checks hourly."""
    from app.bot.bot import get_telegram_application

    logger.info("Starting weekly digest loop (every %s days per creator)", settings.weekly_digest_days)
    while True:
        try:
            count = await send_due_digests(get_telegram_application().bot)
            if count:
                logger.info("Sent %d weekly digests", count)
        except asyncio.CancelledError:
            logger.info("Weekly digest loop stopped.")
            raise
        except Exception:
            logger.exception("Weekly digest loop error")
        await asyncio.sleep(settings.digest_loop_minutes * 60)
