"""Pay-to-unlock content (#4): invite verified tippers into a private channel.

Creators set a VIP channel with /setvip. Every successfully verified tip
then generates a single-use Telegram invite link that is DM'd to the tipper.
"""
import logging

from sqlalchemy import select

from app.db.models import Creator, Tip
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _chat_id(raw: str):
    """Telegram wants numeric chat ids; we store them as strings."""
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return raw

logger = logging.getLogger(__name__)


async def send_unlock_invite(tip_id: str, bot=None) -> None:
    """After a verified tip, DM the tipper a one-time invite to the creator's VIP channel.

    Never raises — unlock must not break the tipping flow.
    """
    try:
        async with AsyncSessionLocal() as session:
            tip = (
                await session.execute(select(Tip).where(Tip.id == tip_id))
            ).scalar_one_or_none()
            if not tip or tip.status != "success" or not tip.tipper_telegram_id:
                return
            creator = (
                await session.execute(select(Creator).where(Creator.id == tip.creator_id))
            ).scalar_one_or_none()

        if not creator or not creator.vip_channel_id:
            return

        if bot is None:
            from app.bot.handlers import get_telegram_application_lazy

            application = get_telegram_application_lazy()
            if application is None:
                return
            bot = application.bot

        chat_id = _chat_id(creator.vip_channel_id)
        invite = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1,
            name=f"Tipa tip {str(tip.id)[:8]}",
        )

        amount = float(tip.amount)
        await bot.send_message(
            chat_id=tip.tipper_telegram_id,
            text=(
                f"🎁 **VIP Access Unlocked!**\n\n"
                f"Your **{amount:g} ETB** tip to **{creator.display_name}** came with "
                f"exclusive channel access.\n\n"
                f"👉 Join here (one-time link): {invite.invite_link}\n\n"
                f"_This private link works for you only._"
            ),
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Failed to send VIP unlock invite for tip %s", tip_id)


async def set_vip_channel(telegram_id: int, raw_channel: str) -> tuple[bool, str]:
    """Validate and store a creator's VIP channel. Returns (ok, message).

    Accepts @username, a numeric chat id (e.g. -100123...), or the word
    "forward" flow handled by the caller. The bot must already be an admin
    in the channel (it needs that to mint single-use invite links).
    """
    from telegram.error import TelegramError

    from app.bot.handlers import get_telegram_application_lazy

    application = get_telegram_application_lazy()
    if application is None:
        return False, "⏳ Bot is starting up — try again in a moment."

    raw_channel = (raw_channel or "").strip()
    if not raw_channel:
        return False, "⚠️ Send me the private channel: forward any post from it, or send its `@username` or `-100…` id."

    try:
        chat = await application.bot.get_chat(raw_channel)
    except TelegramError as exc:
        return (
            False,
            (
                f"❌ I couldn't access that channel ({exc.message}).\n\n"
                "Make sure you **forwarded a post from it** and that I'm an **admin** there "
                "with 'Invite users via link' permission."
            ),
        )

    if chat.type not in ("channel", "supergroup"):
        return False, "⚠️ That's not a channel. Forward a post from your **private channel** instead."

    try:
        member = await application.bot.get_chat_member(chat.id, application.bot.id)
        admin_ok = getattr(member, "status", "") in ("administrator", "creator")
    except TelegramError:
        admin_ok = False
    if not admin_ok:
        return (
            False,
            (
                f"⚠️ I need to be an **admin** of *{chat.title}* with 'Invite users via link' "
                "permission so I can mint one-time invites after each verified tip."
            ),
        )

    async with AsyncSessionLocal() as session:
        creator = (
            await session.execute(select(Creator).where(Creator.telegram_id == telegram_id))
        ).scalar_one_or_none()
        if not creator:
            return False, "❌ Register first with /register."
        creator.vip_channel_id = str(chat.id)
        await session.commit()

    return (
        True,
        (
            f"🔓 **VIP Unlock enabled!**\n\nEvery verified tipper now gets a one-time invite to "
            f"*{chat.title}*.\n\nUse /unsetvip to turn it off."
        ),
    )


async def unset_vip_channel(telegram_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        creator = (
            await session.execute(select(Creator).where(Creator.telegram_id == telegram_id))
        ).scalar_one_or_none()
        if not creator or not creator.vip_channel_id:
            return False
        creator.vip_channel_id = None
        await session.commit()
        return True
