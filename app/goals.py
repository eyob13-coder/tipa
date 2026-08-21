"""Tip goals: public fundraising targets with a live-updating progress bar.

Progress is always recomputed from verified tips (status='success' verified
since the goal was created), so disputes and refunds self-correct without any
bookkeeping. A goal can be bound to a channel post; after each verified tip the
bound post's progress line is edited in place — that is the "live" part.

All Telegram interaction is best-effort: failures are logged, never raised, so
verification flows can't break because a channel post went away.
"""
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from telegram.error import TelegramError

from app.db.models import Creator, Tip, TipGoal
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

BAR_WIDTH = 12


def render_progress_bar(current: Decimal, target: Decimal, width: int = BAR_WIDTH) -> str:
    """Unicode progress bar, e.g. '▓▓▓▓▓░░░░░░░' for 42%."""
    try:
        if target <= 0:
            ratio = 0.0
        else:
            ratio = min(1.0, max(0.0, float(current) / float(target)))
    except (TypeError, ValueError):
        ratio = 0.0
    filled = round(ratio * width)
    return "▓" * filled + "░" * (width - filled)


def format_etb(value: Decimal | float) -> str:
    return f"{float(value):,.0f}"


def goal_progress_line(goal: TipGoal, current: Decimal) -> str:
    """The single line rendered on posts/dashboards."""
    bar = render_progress_bar(current, goal.target_amount)
    pct = int(min(100.0, (float(current) / float(goal.target_amount)) * 100)) if goal.target_amount > 0 else 0
    if current >= goal.target_amount:
        return (
            f"🎯 *{goal.title}* — GOAL REACHED! 🎉\n"
            f"`{bar}` {format_etb(current)} / {format_etb(goal.target_amount)} ETB"
        )
    return (
        f"🎯 *{goal.title}*\n"
        f"`{bar}` {format_etb(current)} / {format_etb(goal.target_amount)} ETB ({pct}%)"
    )


async def get_active_goal(session, creator_id) -> TipGoal | None:
    stmt = (
        select(TipGoal)
        .where(TipGoal.creator_id == creator_id, TipGoal.status == "active")
        .order_by(TipGoal.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def goal_raised_amount(session, goal: TipGoal) -> Decimal:
    """Sum of tips verified since this goal started (refunds self-correct)."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Tip.amount), 0)).where(
                Tip.creator_id == goal.creator_id,
                Tip.status == "success",
                Tip.verified_at >= goal.created_at,
            )
        )
    ).scalar_one()
    return Decimal(str(total))


async def create_goal(session, creator_id, title: str, target: Decimal) -> TipGoal:
    """Start a new goal, cancelling any previous active one (one at a time)."""
    old = await get_active_goal(session, creator_id)
    if old:
        old.status = "cancelled"
    goal = TipGoal(creator_id=creator_id, title=title[:120], target_amount=target)
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


async def cancel_goal(session, creator_id) -> bool:
    goal = await get_active_goal(session, creator_id)
    if not goal:
        return False
    goal.status = "cancelled"
    await session.commit()
    return True


async def _edit_bound_post(bot, goal: TipGoal, current: Decimal) -> None:
    """Rewrite the goal line on a previously-bound channel post."""
    if not (goal.bound_channel_id and goal.bound_message_id and goal.bound_text):
        return
    new_text = f"{goal.bound_text}\n\n{goal_progress_line(goal, current)}"
    chat_id = int(goal.bound_channel_id)
    message_id = int(goal.bound_message_id)
    try:
        if goal.bound_is_caption:
            await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=new_text, parse_mode="Markdown")
        else:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=new_text, parse_mode="Markdown")
    except TelegramError as e:
        msg = str(e).lower()
        # Media posts reject text edits; retry as caption once.
        if not goal.bound_is_caption and ("caption" in msg or "text" in msg):
            try:
                await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=new_text, parse_mode="Markdown")
                goal.bound_is_caption = True
                return
            except TelegramError:
                pass
        logger.warning("Could not update goal post %s/%s: %s", chat_id, message_id, e)


async def _celebrate(bot, creator: Creator, goal: TipGoal, current: Decimal) -> None:
    """One-time fanfare when a goal crosses its target."""
    line = goal_progress_line(goal, current)
    try:
        await bot.send_message(
            chat_id=creator.telegram_id,
            text=(
                f"🎉 **GOAL REACHED!** 🎉\n\n{line}\n\n"
                f"Your community came through for **{goal.title}**! "
                f"Set a new one anytime with `/goal <target> <title>`."
            ),
            parse_mode="Markdown",
        )
    except TelegramError as e:
        logger.warning("Could not send goal-reached DM to %s: %s", creator.telegram_id, e)


def _get_bot():
    # Lazy import avoids an app.bot.handlers <-> app.goals import cycle.
    from app.bot.handlers import get_telegram_application_lazy

    return get_telegram_application_lazy().bot


async def on_tip_verified(tip_id, bot=None) -> None:
    """Call after a tip transitions to success: refresh the bound post / celebrate.

    Opens its own session so callers can invoke it fire-and-forget after their
    own commit. Safe to call even when no goal exists.
    """
    if bot is None:
        bot = _get_bot()
    async with AsyncSessionLocal() as session:
        tip = await session.get(Tip, uuid.UUID(str(tip_id)))
        if tip is None or tip.status != "success":
            return
        goal = await get_active_goal(session, tip.creator_id)
        if goal is None:
            return

        was_reached = goal.reached_at is not None
        current = await goal_raised_amount(session, goal)

        await _edit_bound_post(bot, goal, current)

        crossed = current >= goal.target_amount and goal.reached_at is None
        if crossed:
            goal.reached_at = datetime.now(timezone.utc)
            goal.status = "reached"
        await session.commit()

        if crossed and not was_reached:
            creator = await session.get(Creator, tip.creator_id)
            if creator:
                await _celebrate(bot, creator, goal, current)


async def bind_goal_to_post(session, goal: TipGoal, channel_id: str, message_id: str, base_text: str, is_caption: bool) -> None:
    """Remember which post carries this goal's bar so it can be edited live."""
    goal.bound_channel_id = channel_id
    goal.bound_message_id = message_id
    goal.bound_text = base_text
    goal.bound_is_caption = is_caption
    await session.commit()
