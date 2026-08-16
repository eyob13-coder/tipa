import logging

from sqlalchemy import select

from app.db.models import Creator, Tip
from app.db.session import AsyncSessionLocal
from app.payment_methods import method_name

logger = logging.getLogger(__name__)


async def notify_tip_success(tip_id: str) -> None:
    """Notify creator and tipper that a tip has been verified as received."""
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Tip).where(Tip.id == tip_id)
            res = await session.execute(stmt)
            tip = res.scalar_one_or_none()

            if not tip or tip.status != "success":
                return

            c_stmt = select(Creator).where(Creator.id == tip.creator_id)
            c_res = await session.execute(c_stmt)
            creator = c_res.scalar_one_or_none()

        if not creator:
            return

        method_str = method_name(creator.payment_method)
        from app.bot.bot import get_telegram_application

        bot_app = get_telegram_application()
        tipper_name = tip.tipper_display_name or "A follower"

        note_text = f"\n💬 **Message:** *\"{tip.note}\"*\n" if tip.note else ""
        creator_msg = (
            f"🎉 **Tip Received!**\n\n"
            f"**{tipper_name}** just tipped you **{float(tip.amount):g} ETB**!\n"
            f"{note_text}"
            f"The money goes straight to your **{method_str}** account "
            f"(`{creator.account_number}`).\n\n"
            f"Run `/mytips` to view your updated dashboard."
        )
        try:
            await bot_app.bot.send_message(
                chat_id=creator.telegram_id,
                text=creator_msg,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram notification to creator {creator.telegram_id}: {e}")

        if tip.tipper_telegram_id:
            tipper_msg = (
                f"✅ **Tip Verified!**\n\n"
                f"Your tip of **{float(tip.amount):g} ETB** to **{creator.display_name}** has been confirmed.\n"
                f"Thank you for supporting creators on Tipa! 🙏"
            )
            try:
                await bot_app.bot.send_message(
                    chat_id=tip.tipper_telegram_id,
                    text=tipper_msg,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Failed to send Telegram notification to tipper {tip.tipper_telegram_id}: {e}")

    except Exception as e:
        logger.exception(f"Error in notify_tip_success background task: {e}")
