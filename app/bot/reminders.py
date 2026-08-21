import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from telegram.error import TelegramError

from app.bot.bot import get_telegram_application
from app.bot.keyboards import get_creator_approval_keyboard
from app.config import settings
from app.db.models import Creator, Tip
from app.db.session import AsyncSessionLocal
from app.subscriptions import expire_due_subscriptions

logger = logging.getLogger(__name__)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize a datetime to UTC-aware, assuming UTC for naive values (SQLite)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hours_between(now: datetime, earlier: datetime) -> float:
    return (now - earlier).total_seconds() / 3600.0


def _safe(value: str | None, max_len: int = 100) -> str:
    """Strip markup-breaking characters before putting user input in a message."""
    if not value:
        return ""
    cleaned = value.replace("`", "").replace("_", " ").replace("*", "")
    return cleaned[:max_len]


async def _send_reminder(bot, tip: Tip, creator: Creator) -> None:
    """Nudge the creator to approve a claimed tip that is still waiting."""
    tipper = _safe(tip.tipper_display_name) or "A supporter"
    ref = _safe(tip.ref_id)
    try:
        await bot.send_message(
            chat_id=creator.telegram_id,
            text=(
                f"⏰ **Reminder: Tip Waiting for Confirmation**\n\n"
                f"**{tipper}** claims they sent **{float(tip.amount):g} ETB** to your "
                f"`{_safe(creator.account_number)}` more than {settings.tip_reminder_hours:g} hours ago.\n"
                f"Reference: `{ref}`\n\n"
                f"Tap **Approve** if you received it. Tips not confirmed within "
                f"{settings.tip_expiry_hours:g} hours are cancelled automatically."
            ),
            reply_markup=get_creator_approval_keyboard(str(tip.id)),
            parse_mode="Markdown",
        )
        logger.info("Sent approval reminder to creator %s for tip %s", creator.telegram_id, tip.id)
    except TelegramError as e:
        logger.error("Failed to send approval reminder to creator %s: %s", creator.telegram_id, e)


async def _send_expired(bot, tip: Tip, creator: Creator) -> None:
    """Tell both sides a claimed tip expired without confirmation."""
    amount = float(tip.amount)
    tipper = _safe(tip.tipper_display_name) or "a supporter"
    try:
        await bot.send_message(
            chat_id=creator.telegram_id,
            text=(
                f"⏰ **Tip Auto-Cancelled**\n\n"
                f"A claimed tip of **{amount:g} ETB** from {tipper} was cancelled because it was "
                f"not approved within {settings.tip_expiry_hours:g} hours.\n"
                f"If you actually received the money, ask the supporter to send the tip again."
            ),
            parse_mode="Markdown",
        )
        logger.info("Notified creator %s that tip %s expired", creator.telegram_id, tip.id)
    except TelegramError as e:
        logger.error("Failed to notify creator %s about expired tip: %s", creator.telegram_id, e)

    if tip.tipper_telegram_id:
        try:
            await bot.send_message(
                chat_id=tip.tipper_telegram_id,
                text=(
                    f"⏰ **Tip Update**\n\n"
                    f"Your **{amount:g} ETB** tip to **{_safe(creator.display_name)}** was not "
                    f"confirmed by the creator within {settings.tip_expiry_hours:g} hours, so it was cancelled.\n"
                    f"If you already sent the money, please contact the creator directly."
                ),
                parse_mode="Markdown",
            )
            logger.info("Notified tipper %s that tip %s expired", tip.tipper_telegram_id, tip.id)
        except TelegramError as e:
            logger.error("Failed to notify tipper %s about expired tip: %s", tip.tipper_telegram_id, e)


async def remind_and_expire_pending_tips(
    bot=None,
    now: datetime | None = None,
    reminder_hours: float | None = None,
    expiry_hours: float | None = None,
) -> dict:
    """One pass over claimed tips: send approval reminders and expire old ones.

    Reminders are only sent once per reminder window (tracked by last_reminder_at).
    Returns the ids of tips that were reminded and expired.
    """
    if bot is None:
        bot = get_telegram_application().bot
    if reminder_hours is None:
        reminder_hours = settings.tip_reminder_hours
    if expiry_hours is None:
        expiry_hours = settings.tip_expiry_hours
    now = _as_utc(now) or datetime.now(timezone.utc)

    reminded_ids = []
    expired_ids = []

    async with AsyncSessionLocal() as session:
        # FOR UPDATE SKIP LOCKED: with multiple app instances each pass claims
        # disjoint tip sets, so creators never get duplicate reminders. SQLite
        # (dev) ignores the locking hints, which is fine for a single process.
        stmt = select(Tip).where(Tip.status == "pending_verification").with_for_update(skip_locked=True)
        res = await session.execute(stmt)
        tips = res.scalars().all()

        creators: dict = {}
        for tip in tips:
            creator = creators.get(tip.creator_id)
            if creator is None:
                creator = await session.get(Creator, tip.creator_id)
                if creator is None:
                    continue
                creators[tip.creator_id] = creator

            anchor = _as_utc(tip.claimed_at) or _as_utc(tip.created_at)
            if anchor is None:
                continue

            age = _hours_between(now, anchor)

            if age >= expiry_hours:
                tip.status = "failed"
                await session.commit()
                expired_ids.append(str(tip.id))
                await _send_expired(bot, tip, creator)
            elif age >= reminder_hours:
                last = _as_utc(tip.last_reminder_at)
                if last is None or _hours_between(now, last) >= reminder_hours:
                    tip.last_reminder_at = now
                    await session.commit()
                    reminded_ids.append(str(tip.id))
                    await _send_reminder(bot, tip, creator)

    return {"reminded": reminded_ids, "expired": expired_ids}


async def run_tip_reminder_loop() -> None:
    """Background loop that periodically reminds and expires pending tips."""
    logger.info("Starting tip reminder loop (every %s minutes)", settings.tip_reminder_loop_minutes)
    while True:
        try:
            await remind_and_expire_pending_tips()
        except asyncio.CancelledError:
            logger.info("Tip reminder loop stopped.")
            raise
        except Exception:
            logger.exception("Tip reminder loop error")
        try:
            await expire_due_subscriptions()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Subscription expiry loop error")
        await asyncio.sleep(settings.tip_reminder_loop_minutes * 60)
