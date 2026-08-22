import io
import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pytesseract
from PIL import Image
from sqlalchemy import desc, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)

from app.bot.keyboards import (
    get_admin_account_verification_keyboard,
    get_admin_subscription_keyboard,
    get_av_transfer_keyboard,
    get_channel_post_button,
    get_confirm_registration_keyboard,
    get_creator_approval_keyboard,
    get_language_keyboard,
    get_payment_method_selection_keyboard,
    get_subscription_transfer_keyboard,
    get_tip_amount_keyboard,
    get_tip_note_prompt_keyboard,
    get_transfer_keyboard,
)
from app.bot.notifications import notify_tip_success
from app.config import settings
from app.db.models import Creator, Subscription, Tip
from app.db.session import AsyncSessionLocal
from app.export import build_tips_csv
from app.i18n import t
from app.payment_methods import (
    get_method,
    method_name,
    ussd_code_for,
)
from app.receipts import build_tips_pdf
from app.subscriptions import (
    SUB_STATUS_PENDING,
    SUB_STATUS_PENDING_VERIFICATION,
    SUB_STATUS_REJECTED,
    activate_subscription,
    auto_verify_subscription,
    get_active_subscription,
    is_pro,
    log_subscription_verification,
)
from app.verify.base import VerificationError, VerifyResult
from app.verify.registry import verify_registry
from app.verify.service import (
    ACCOUNT_NUMBER_METHODS,
    _amount_matches,
    auto_verify_tip,
    log_verification_attempt,
)

logger = logging.getLogger(__name__)

# Conversation states for registration
METHOD_CHOICE, ACCOUNT_NUM, ACCOUNT_NAME, CHANNEL_LINK, CONFIRMATION = range(5)

# Amount bounds shared by the bot tip flow (mirrors the Mini App API limits).
MIN_TIP_BIRR = Decimal(5)
MAX_TIP_BIRR = Decimal(50000)


def _lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Active UI language ('en' | 'am') from user_data (persisted for creators)."""
    return context.user_data.get("lang") or "en"


def get_telegram_application_lazy():
    """Lazy import to dodge the handlers <-> bot module circular import."""
    from app.bot.bot import get_telegram_application

    return get_telegram_application()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and deep linking tip_<creator_id>."""
    if not update.effective_message or not update.effective_user:
        return

    logger.info(f"Received /start from Telegram user: {update.effective_user.id} ({update.effective_user.first_name})")
    args = context.args or []

    # Check for deep-link tip flow: /start tip_<creator_id>
    if args and args[0].startswith("tip_"):
        raw_arg = args[0].replace("tip_", "").strip()
        creator_id_str = raw_arg
        post_id = None

        if "_post_" in raw_arg:
            parts = raw_arg.split("_post_")
            creator_id_str = parts[0]
            post_id = parts[1]

        try:
            creator_uuid = uuid.UUID(creator_id_str)
            async with AsyncSessionLocal() as session:
                stmt = select(Creator).where(Creator.id == creator_uuid)
                result = await session.execute(stmt)
                creator = result.scalar_one_or_none()

            if creator:
                if creator.is_frozen:
                    await update.effective_message.reply_text(
                        "⚠️ This creator is temporarily unable to receive tips. Please try again later."
                    )
                    return
                if post_id:
                    context.user_data["active_post_id"] = post_id
                keyboard = get_tip_amount_keyboard(str(creator.id))
                method_str = method_name(creator.payment_method)
                lang = creator.language or "en"
                context.user_data["lang"] = lang
                post_text = f" for post **#{post_id}**" if post_id else ""
                intro = t(
                    lang,
                    "tip_intro",
                    creator_name=creator.display_name,
                    post_text=post_text,
                    method=method_str,
                )
                await update.effective_message.reply_text(intro, reply_markup=keyboard, parse_mode="Markdown")
                return
            else:
                await update.effective_message.reply_text("❌ Creator not found or tipping link is invalid.")
                return
        except ValueError:
            await update.effective_message.reply_text("❌ Invalid tipping link format.")
            return

    # Normal /start intro message
    telegram_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Creator"

    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == telegram_id)
        res = await session.execute(stmt)
        existing_creator = res.scalar_one_or_none()

    bot_name = context.bot.username or settings.bot_username
    if existing_creator:
        lang = existing_creator.language or "en"
        context.user_data["lang"] = lang
        deep_link = f"https://t.me/{bot_name}?start=tip_{existing_creator.id}"
        method_str = method_name(existing_creator.payment_method)
        await update.effective_message.reply_text(
            t(
                lang,
                "start_back",
                display_name=existing_creator.display_name,
                method=method_str,
                account_number=existing_creator.account_number,
                deep_link=deep_link,
            ),
            parse_mode="Markdown",
        )
    else:
        lang = _lang(context)
        await update.effective_message.reply_text(
            t(lang, "start_new", user_name=user_name, bot_name=bot_name) + t(lang, "lang_prompt"),
            reply_markup=get_language_keyboard(),
            parse_mode="Markdown",
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detailed help guide explaining every bot command and feature."""
    if not update.effective_message:
        return

    bot_name = context.bot.username or settings.bot_username
    await update.effective_message.reply_text(
        t(_lang(context), "help_text", bot_name=bot_name),
        parse_mode="Markdown",
    )


async def pro_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tipa Pro upsell & direct-payment upgrade flow."""
    if not update.effective_message or not update.effective_user:
        return

    if not settings.tipa_receiving_account:
        await update.effective_message.reply_text(
            "⭐ **Tipa Pro** is coming soon! Payments are not configured yet — check back shortly.",
            parse_mode="Markdown",
        )
        return

    user_id = update.effective_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()

        active_sub = None
        if creator:
            active_sub = await get_active_subscription(session, creator.id)

    price = settings.pro_price_birr
    duration = settings.pro_duration_days

    status_line = ""
    if active_sub and active_sub.expires_at:
        expiry_str = active_sub.expires_at.strftime("%b %d, %Y")
        status_line = (
            f"✅ Your Pro is active until **{expiry_str}**.\n"
            f"Renewing now adds **{duration} more days** on top.\n\n"
        )

    method = get_method(settings.tipa_receiving_method) or get_method("telebirr")
    tx_ref = f"pro_{uuid.uuid4().hex[:12]}"
    emoji = "📱" if method.kind == "mobile" else "🏦"
    account_label = method.account_label or ("Phone" if method.kind == "mobile" else "Account Number")

    lang = _lang(context)
    instructions = t(
        lang,
        "pro_pitch",
        price=f"{price:g}",
        duration=duration,
        status_line=status_line,
        emoji=emoji,
        method_name=method.name,
        tipa_account=settings.tipa_receiving_account,
        account_label=account_label.lower(),
        tx_ref=tx_ref,
    )

    if not creator:
        instructions += "\n\n⚠️ You must `/register` first so we know where to enable Pro!"
    else:
        async with AsyncSessionLocal() as session:
            session.add(
                Subscription(
                    creator_id=creator.id,
                    plan="pro",
                    amount=price,
                    tx_ref=tx_ref,
                    status="pending",
                )
            )
            await session.commit()

    await update.effective_message.reply_text(
        instructions,
        reply_markup=get_subscription_transfer_keyboard(),
        parse_mode="Markdown",
    )


async def subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Pro/AV payment claims, cancels, and admin approval callbacks.

    Approval callbacks answer themselves inside their handlers (so the first
    answer can carry an alert); the simple ack/cancel branches answer here.
    """
    query = update.callback_query
    if not query or not query.data or not query.from_user:
        return

    data = query.data
    if data == "pro_sent":
        await query.answer()
        context.user_data["pending_pro_ref"] = True
        await query.edit_message_text(
            "📝 **Pro Payment Confirmation**\n\n"
            "Please send the transaction **Reference Number** / **SMS Code** from your payment "
            "(e.g., `TLB12345678` or `FT12345678`):",
            parse_mode="Markdown",
        )
    elif data == "pro_cancel":
        await query.answer()
        context.user_data.pop("pending_pro_ref", None)
        await query.edit_message_text("❌ Pro upgrade cancelled. Run `/pro` anytime!")
    elif data == "av_sent":
        await query.answer()
        context.user_data["pending_av_ref"] = True
        await query.edit_message_text(
            "📝 **Deposit Confirmation**\n\n"
            "Please send the transaction **Reference Number** / **SMS Code** from your deposit "
            "(e.g., `TLB12345678` or `FT12345678`):",
            parse_mode="Markdown",
        )
    elif data == "av_cancel":
        await query.answer()
        context.user_data.pop("pending_av_ref", None)
        await query.edit_message_text("❌ Verification cancelled. Run `/verifyaccount` anytime!")
    elif data in ("lang_en", "lang_am"):
        await query.answer()
        lang = "am" if data == "lang_am" else "en"
        context.user_data["lang"] = lang
        bot_name = context.bot.username or settings.bot_username
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(Creator).where(Creator.telegram_id == query.from_user.id)
            )
            creator = res.scalar_one_or_none()
            if creator:
                creator.language = lang
                await session.commit()
                deep_link = f"https://t.me/{bot_name}?start=tip_{creator.id}"
                text = t(
                    lang,
                    "start_back",
                    display_name=creator.display_name,
                    method=method_name(creator.payment_method),
                    account_number=creator.account_number,
                    deep_link=deep_link,
                )
            else:
                first = query.from_user.first_name or "Creator"
                text = t(lang, "start_new", user_name=first, bot_name=bot_name)
        try:
            await query.edit_message_text(text, parse_mode="Markdown")
        except TelegramError:
            pass  # identical text (re-tap same language)
    elif data.startswith(("approve_sub:", "reject_sub:")):
        sub_id_str = data.split(":", 1)[1]
        await handle_admin_subscription_approval(
            update, context, sub_id_str, is_approve=data.startswith("approve_sub:")
        )
    elif data.startswith(("approve_av:", "reject_av:")):
        creator_id_str = data.split(":", 1)[1]
        await handle_admin_account_verification(
            update, context, creator_id_str, is_approve=data.startswith("approve_av:")
        )


async def process_subscription_claim(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ref_code: str,
) -> None:
    """Verify a claimed Pro payment; auto-activate or route to admin approval."""
    if not update.effective_user or not update.effective_message:
        return
    user_id = update.effective_user.id

    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()
        if not creator:
            await update.effective_message.reply_text(
                "❌ Please `/register` first, then run `/pro` again.",
                parse_mode="Markdown",
            )
            return

        sub_stmt = (
            select(Subscription)
            .where(
                Subscription.creator_id == creator.id,
                Subscription.status.in_([SUB_STATUS_PENDING, SUB_STATUS_PENDING_VERIFICATION]),
            )
            .order_by(desc(Subscription.created_at))
            .limit(1)
        )
        sub_res = await session.execute(sub_stmt)
        sub = sub_res.scalar_one_or_none()
        if not sub:
            await update.effective_message.reply_text(
                "❌ No pending Pro payment found. Run `/pro` to start one first.",
                parse_mode="Markdown",
            )
            return

        dup_stmt = (
            select(Subscription.id)
            .where(
                Subscription.ref_id == ref_code,
                Subscription.id != sub.id,
                Subscription.status.in_([SUB_STATUS_PENDING, SUB_STATUS_PENDING_VERIFICATION, "active"]),
            )
            .limit(1)
        )
        dup_res = await session.execute(dup_stmt)
        if dup_res.first() is not None:
            await update.effective_message.reply_text(
                f"❌ **Duplicate Reference Code**\n\nThe code `{ref_code}` was already used for another Pro payment.",
                parse_mode="Markdown",
            )
            return

        sub.ref_id = ref_code
        sub.claimed_at = datetime.now(timezone.utc)
        sub.status = SUB_STATUS_PENDING_VERIFICATION
        try:
            await session.commit()
        except IntegrityError:
            # Concurrent claim won the unique race on Subscription.ref_id.
            await session.rollback()
            await update.effective_message.reply_text(
                f"❌ **Duplicate Reference Code**\n\nThe code `{ref_code}` was already used for another Pro payment.",
                parse_mode="Markdown",
            )
            return

        verify_result = await auto_verify_subscription(session, sub, ref_code)

        if verify_result is not None and verify_result.verified:
            await session.refresh(sub)
            expiry_str = sub.expires_at.strftime("%b %d, %Y") if sub.expires_at else "now"
            await update.effective_message.reply_text(
                f"🎉 **Tipa Pro Activated!**\n\n"
                f"Payment of **{float(sub.amount):g} ETB** verified (Ref: `{ref_code}`).\n"
                f"Your Pro features are unlocked until **{expiry_str}**. Thank you for supporting Tipa! ❤️",
                parse_mode="Markdown",
            )
            return

    admins = settings.admin_ids
    if admins:
        try:
            from app.bot.bot import get_telegram_application

            bot_app = get_telegram_application()
            for admin_id in admins:
                try:
                    await bot_app.bot.send_message(
                        chat_id=admin_id,
                        text=(
                            f"⭐ **New Pro Payment Claim**\n\n"
                            f"Creator: **{creator.display_name}** (`{creator.telegram_id}`)\n"
                            f"Amount: **{float(sub.amount):g} ETB**\n"
                            f"Receipt Ref: `{ref_code}`\n\n"
                            f"Auto-verification did not confirm it. Verify manually and approve below:"
                        ),
                        reply_markup=get_admin_subscription_keyboard(str(sub.id)),
                        parse_mode="Markdown",
                    )
                except TelegramError as e:
                    logger.error("Failed to notify admin %s about Pro claim: %s", admin_id, e)
        except Exception:
            logger.exception("Failed to notify admins about Pro claim")

    await update.effective_message.reply_text(
        f"✅ **Pro Payment Submitted!**\n\n"
        f"Ref/SMS Code: `{ref_code}`\n\n"
        f"We couldn't auto-verify it yet — our team will review it shortly. "
        f"You'll get a message as soon as Pro is activated! 🙏",
        parse_mode="Markdown",
    )


async def handle_admin_subscription_approval(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    sub_id_str: str,
    is_approve: bool,
) -> None:
    """Admin-only manual Approve/Reject for a claimed Pro payment."""
    query = update.callback_query
    if not query:
        return

    admin_id = query.from_user.id
    if admin_id not in settings.admin_ids:
        await query.answer("⛔ Only Tipa admins can do this.", show_alert=True)
        return
    await query.answer()

    try:
        sub_uuid = uuid.UUID(sub_id_str)
    except ValueError:
        await query.edit_message_text("❌ Invalid subscription ID.")
        return

    async with AsyncSessionLocal() as session:
        sub = await session.get(Subscription, sub_uuid)
        if not sub:
            await query.edit_message_text("❌ Subscription not found.")
            return

        if sub.status not in (SUB_STATUS_PENDING, SUB_STATUS_PENDING_VERIFICATION):
            await query.answer(f"This payment was already processed ({sub.status}).", show_alert=True)
            return

        c_stmt = select(Creator).where(Creator.id == sub.creator_id)
        c_res = await session.execute(c_stmt)
        creator = c_res.scalar_one_or_none()

        if is_approve:
            await activate_subscription(session, sub, method="admin_approval")
            await log_subscription_verification(
                session,
                subscription_id=sub.id,
                provider="admin_approval",
                status="success",
                verified=True,
                amount=sub.amount,
                message="Approved manually by a Tipa admin",
            )
            expiry_str = sub.expires_at.strftime("%b %d, %Y") if sub.expires_at else "now"
            await query.edit_message_text(
                f"🎉 **Pro Approved**\n\n{creator.display_name if creator else 'Creator'} is Pro until **{expiry_str}**."
            )
            if creator:
                try:
                    await context.bot.send_message(
                        chat_id=creator.telegram_id,
                        text=(
                            f"🎉 **Tipa Pro Activated!**\n\n"
                            f"Your payment was confirmed. Pro features are unlocked until **{expiry_str}**. "
                            f"Thank you for supporting Tipa! ❤️"
                        ),
                        parse_mode="Markdown",
                    )
                except TelegramError as e:
                    logger.error("Failed to notify creator about Pro activation: %s", e)
        else:
            sub.status = SUB_STATUS_REJECTED
            await session.commit()
            await log_subscription_verification(
                session,
                subscription_id=sub.id,
                provider="admin_approval",
                status="rejected",
                verified=False,
                amount=sub.amount,
                message="Rejected by a Tipa admin",
            )
            await query.edit_message_text(
                f"❌ **Pro Payment Rejected**\n\nClaim for `{sub.ref_id}` marked as rejected."
            )
            if creator:
                try:
                    await context.bot.send_message(
                        chat_id=creator.telegram_id,
                        text=(
                            f"❌ **Pro Payment Not Confirmed**\n\n"
                            f"Your Pro payment (Ref: `{sub.ref_id}`) could not be verified. "
                            f"If you believe this is a mistake, contact support with your receipt."
                        ),
                        parse_mode="Markdown",
                    )
                except TelegramError as e:
                    logger.error("Failed to notify creator about Pro rejection: %s", e)


async def _verify_account_deposit(ref_code: str) -> VerifyResult | None:
    """Verify the ownership-proof micro-deposit against Tipa's receiving account."""
    if not verify_registry.enabled_providers:
        return None
    bank = settings.tipa_receiving_method
    account_number = settings.tipa_receiving_account if bank in ACCOUNT_NUMBER_METHODS else None
    try:
        return await verify_registry.verify(
            bank=bank,
            reference=ref_code,
            account_number=account_number,
            idempotency_key=f"av-{ref_code}",
        )
    except VerificationError as e:
        logger.exception("verify registry failed for account verification ref %s", ref_code)
        return VerifyResult(request_success=False, message=str(e))


async def verifyaccount_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prove ownership of the registered receiving account via a coded micro-deposit.

    Without this, anyone could register someone else's phone/account number and
    collect their tips, or a typo would silently route money into the void.
    """
    if not update.effective_message or not update.effective_user:
        return

    if not settings.tipa_receiving_account:
        await update.effective_message.reply_text(
            "🔐 Account verification isn't configured yet — please try again later.",
            parse_mode="Markdown",
        )
        return

    user_id = update.effective_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()

        if not creator:
            await update.effective_message.reply_text(
                "❌ You must `/register` first, then run `/verifyaccount`.",
                parse_mode="Markdown",
            )
            return

        if creator.account_verified:
            await update.effective_message.reply_text(
                "✅ Your account is already verified. You're all set!",
                parse_mode="Markdown",
            )
            return

        code = f"av_{uuid.uuid4().hex[:8]}"
        creator.account_verification_code = code
        creator.account_verification_ref = None
        await session.commit()
        method_str = method_name(creator.payment_method)
        lang = creator.language or "en"

    amount = settings.account_verification_amount_birr
    instructions = t(
        lang,
        "verify_instructions",
        account_number=creator.account_number,
        method=method_str,
        amount=f"{amount:g}",
        tipa_account=settings.tipa_receiving_account,
        code=code,
    )

    await update.effective_message.reply_text(
        instructions,
        reply_markup=get_av_transfer_keyboard(),
        parse_mode="Markdown",
    )


async def process_account_verification_claim(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ref_code: str,
) -> None:
    """Verify the ownership deposit; auto-activate or route to admin approval."""
    if not update.effective_user or not update.effective_message:
        return
    user_id = update.effective_user.id

    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()

        if not creator or not creator.account_verification_code:
            await update.effective_message.reply_text(
                "❌ No verification in progress. Run `/verifyaccount` first.",
                parse_mode="Markdown",
            )
            return

        dup_stmt = (
            select(Creator.id)
            .where(
                Creator.account_verification_ref == ref_code,
                Creator.id != creator.id,
            )
            .limit(1)
        )
        dup_res = await session.execute(dup_stmt)
        if dup_res.first() is not None:
            await update.effective_message.reply_text(
                f"❌ The code `{ref_code}` was already used for another verification.",
                parse_mode="Markdown",
            )
            return

        creator.account_verification_ref = ref_code
        try:
            await session.commit()
        except IntegrityError:
            # Concurrent claim won the unique race on account_verification_ref.
            await session.rollback()
            await update.effective_message.reply_text(
                f"❌ The code `{ref_code}` was already used for another verification.",
                parse_mode="Markdown",
            )
            return

        result = await _verify_account_deposit(ref_code)
        verified = (
            result is not None
            and result.verified
            and _amount_matches(result.amount, settings.account_verification_amount_birr)
        )

        await log_verification_attempt(
            session,
            tip_id=None,
            provider=result.provider if result else "none",
            status=result.status if result else "unavailable",
            verified=verified,
            amount=result.amount if result else None,
            message="Account ownership verification deposit",
        )

        if verified:
            creator.account_verified = True
            creator.account_verification_code = None
            creator.account_verification_ref = None
            await session.commit()
            await update.effective_message.reply_text(
                "🎉 **Account Verified!**\n\n"
                "Your ownership of this receiving account is confirmed. Tippers can now trust "
                "that their tips reach you. Thank you for keeping Tipa safe! 🙏",
                parse_mode="Markdown",
            )
            return

    admins = settings.admin_ids
    if admins:
        try:
            from app.bot.bot import get_telegram_application

            bot_app = get_telegram_application()
            for admin_id in admins:
                try:
                    await bot_app.bot.send_message(
                        chat_id=admin_id,
                        text=(
                            f"🔐 **New Account Ownership Claim**\n\n"
                            f"Creator: **{creator.display_name}** (`{creator.telegram_id}`)\n"
                            f"Account: `{creator.account_number}` ({method_name(creator.payment_method)})\n"
                            f"Deposit Ref: `{ref_code}`\n\n"
                            f"Auto-verification did not confirm it. Review manually:"
                        ),
                        reply_markup=get_admin_account_verification_keyboard(str(creator.id)),
                        parse_mode="Markdown",
                    )
                except TelegramError as e:
                    logger.error("Failed to notify admin %s about ownership claim: %s", admin_id, e)
        except Exception:
            logger.exception("Failed to notify admins about ownership claim")

    await update.effective_message.reply_text(
        "✅ **Deposit Submitted!**\n\n"
        "We couldn't auto-verify it yet — our team will review it shortly. "
        "You'll get a message once your account is verified! 🙏",
        parse_mode="Markdown",
    )


async def handle_admin_account_verification(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    creator_id_str: str,
    is_approve: bool,
) -> None:
    """Admin-only manual Approve/Reject for an account-ownership deposit."""
    query = update.callback_query
    if not query:
        return

    if query.from_user.id not in settings.admin_ids:
        await query.answer("⛔ Only Tipa admins can do this.", show_alert=True)
        return
    await query.answer()

    try:
        creator_uuid = uuid.UUID(creator_id_str)
    except ValueError:
        await query.edit_message_text("❌ Invalid creator ID.")
        return

    async with AsyncSessionLocal() as session:
        creator = await session.get(Creator, creator_uuid)
        if not creator:
            await query.edit_message_text("❌ Creator not found.")
            return

        if creator.account_verified or not creator.account_verification_ref:
            await query.answer("This verification was already processed.", show_alert=True)
            return

        if is_approve:
            creator.account_verified = True
            creator.account_verification_code = None
            creator.account_verification_ref = None
            await session.commit()
            await log_verification_attempt(
                session,
                tip_id=None,
                provider="admin_approval",
                status="success",
                verified=True,
                amount=settings.account_verification_amount_birr,
                message="Ownership approved manually by a Tipa admin",
            )
            await query.edit_message_text(f"✅ **Ownership Approved** for {creator.display_name}.")
        else:
            # Keep the verification code so the creator can retry with a fresh deposit.
            creator.account_verification_ref = None
            await session.commit()
            await log_verification_attempt(
                session,
                tip_id=None,
                provider="admin_approval",
                status="rejected",
                verified=False,
                amount=settings.account_verification_amount_birr,
                message="Ownership rejected by a Tipa admin",
            )
            await query.edit_message_text(f"❌ **Ownership Claim Rejected** for {creator.display_name}.")

        try:
            await context.bot.send_message(
                chat_id=creator.telegram_id,
                text=(
                    "🎉 **Account Verified!** Your receiving account ownership is confirmed."
                    if is_approve
                    else "❌ **Verification Not Confirmed**\n\nYour ownership deposit couldn't be verified. "
                    "Run `/verifyaccount` to retry with a fresh deposit, or contact support."
                ),
                parse_mode="Markdown",
            )
        except TelegramError as e:
            logger.error("Failed to notify creator about ownership decision: %s", e)


async def _find_creator_flexible(session, identifier: str) -> Creator | None:
    """Look up a creator by UUID or Telegram id (admin tooling helper)."""
    try:
        creator_uuid = uuid.UUID(identifier)
        creator = await session.get(Creator, creator_uuid)
        if creator:
            return creator
    except ValueError:
        pass
    if identifier.isdigit():
        res = await session.execute(select(Creator).where(Creator.telegram_id == int(identifier)))
        return res.scalar_one_or_none()
    return None


async def freeze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin only: /freeze <creator_id_or_telegram_id> — block new tips to a creator."""
    if not update.effective_message or not update.effective_user:
        return
    if update.effective_user.id not in settings.admin_ids:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /freeze <creator_id_or_telegram_id>")
        return

    async with AsyncSessionLocal() as session:
        creator = await _find_creator_flexible(session, context.args[0])
        if not creator:
            await update.effective_message.reply_text("❌ Creator not found.")
            return
        creator.is_frozen = True
        await session.commit()
        await update.effective_message.reply_text(
            f"🧊 Frozen: **{creator.display_name}** (`{creator.telegram_id}`) can no longer receive tips.",
            parse_mode="Markdown",
        )


async def unfreeze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin only: /unfreeze <creator_id_or_telegram_id> — re-enable tips."""
    if not update.effective_message or not update.effective_user:
        return
    if update.effective_user.id not in settings.admin_ids:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /unfreeze <creator_id_or_telegram_id>")
        return

    async with AsyncSessionLocal() as session:
        creator = await _find_creator_flexible(session, context.args[0])
        if not creator:
            await update.effective_message.reply_text("❌ Creator not found.")
            return
        creator.is_frozen = False
        await session.commit()
        await update.effective_message.reply_text(
            f"🔥 Unfrozen: **{creator.display_name}** (`{creator.telegram_id}`) can receive tips again.",
            parse_mode="Markdown",
        )


async def dispute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin only: /dispute <tip_id> — flag a tip for review and notify both sides."""
    if not update.effective_message or not update.effective_user:
        return
    if update.effective_user.id not in settings.admin_ids:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /dispute <tip_id>")
        return

    try:
        tip_uuid = uuid.UUID(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid tip id.")
        return

    async with AsyncSessionLocal() as session:
        tip = await session.get(Tip, tip_uuid)
        if not tip:
            await update.effective_message.reply_text("❌ Tip not found.")
            return
        if tip.status == "disputed":
            await update.effective_message.reply_text("⚠️ This tip is already disputed.")
            return
        if tip.status not in ("success", "pending_verification"):
            await update.effective_message.reply_text(
                f"❌ Only successful or pending-verification tips can be disputed (current: {tip.status})."
            )
            return

        previous_status = tip.status
        tip.status = "disputed"
        await session.commit()
        creator = await session.get(Creator, tip.creator_id)

    await update.effective_message.reply_text(
        f"⚖️ Tip `{tip.id}` marked **disputed** (was: {previous_status}). "
        f"Evidence on file: {tip.receipt_file_path or 'none'}",
        parse_mode="Markdown",
    )

    bot_app = get_telegram_application_lazy()
    for chat_id, text in (
        (creator.telegram_id if creator else None, "⚖️ A tip you received is under dispute review. Tipa support may contact you."),
        (tip.tipper_telegram_id, "⚖️ Your recent tip is under dispute review. Tipa support may contact you."),
    ):
        if not chat_id:
            continue
        try:
            await bot_app.bot.send_message(chat_id=chat_id, text=text)
        except TelegramError as e:
            logger.error("Failed to notify chat %s about dispute: %s", chat_id, e)


async def resolvedispute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin only: /resolvedispute <tip_id> <keep|refund> — close a dispute.

    ``keep`` restores the tip as successful; ``refund`` marks it failed so the
    record shows the money must be returned outside Tipa (no custody).
    """
    if not update.effective_message or not update.effective_user:
        return
    if update.effective_user.id not in settings.admin_ids:
        return
    if len(context.args) < 2 or context.args[1] not in ("keep", "refund"):
        await update.effective_message.reply_text("Usage: /resolvedispute <tip_id> <keep|refund>")
        return

    try:
        tip_uuid = uuid.UUID(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid tip id.")
        return

    outcome = context.args[1]
    async with AsyncSessionLocal() as session:
        tip = await session.get(Tip, tip_uuid)
        if not tip:
            await update.effective_message.reply_text("❌ Tip not found.")
            return
        if tip.status != "disputed":
            await update.effective_message.reply_text("❌ This tip is not under dispute.")
            return

        tip.status = "success" if outcome == "keep" else "failed"
        if outcome == "keep" and tip.verified_at is None:
            tip.verified_at = datetime.now(timezone.utc)
        await session.commit()
        creator = await session.get(Creator, tip.creator_id)

    resolution_text = (
        "kept as successful" if outcome == "keep" else "marked failed (refund handled outside Tipa)"
    )
    await update.effective_message.reply_text(
        f"✅ Dispute on `{tip.id}` resolved: {resolution_text}.", parse_mode="Markdown"
    )

    bot_app = get_telegram_application_lazy()
    for chat_id, text in (
        (
            creator.telegram_id if creator else None,
            f"⚖️ The disputed tip was {resolution_text}. Thank you for your patience!",
        ),
        (
            tip.tipper_telegram_id,
            f"⚖️ The dispute over your tip was resolved: {resolution_text}.",
        ),
    ):
        if not chat_id:
            continue
        try:
            await bot_app.bot.send_message(chat_id=chat_id, text=text)
        except TelegramError as e:
            logger.error("Failed to notify chat %s about dispute resolution: %s", chat_id, e)


async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start registration conversation flow: select payment method."""
    if not update.effective_message:
        return ConversationHandler.END

    context.user_data.pop("payout_update", None)
    keyboard = get_payment_method_selection_keyboard()
    await update.effective_message.reply_text(
        "💳 **Step 1/3: Choose your Receiving Payment Method**\n"
        "How would you like to receive tips from followers?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return METHOD_CHOICE


async def payout_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Change an existing creator's payout bank/wallet via the registration flow.

    Any of the nine supported banks / mobile wallets can be switched to here;
    switching resets account ownership proof until /verifyaccount is redone.
    """
    if not update.effective_message:
        return ConversationHandler.END

    user_id = update.effective_user.id if update.effective_user else None
    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()

    if not creator:
        await update.effective_message.reply_text(
            "❌ You are not registered as a creator yet.\nRun `/register` first to set up payouts!",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    lang = _lang(context)
    context.user_data["payout_update"] = True
    current = method_name(creator.payment_method)
    await update.effective_message.reply_text(
        t(lang, "payout_intro", current=current) + "\n\n"
        "💳 **Step 1/2: Choose your new Payout Method**",
        reply_markup=get_payment_method_selection_keyboard(),
        parse_mode="Markdown",
    )
    return METHOD_CHOICE


async def payment_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle payment method selection and back navigation."""
    query = update.callback_query
    if not query or not query.data:
        return METHOD_CHOICE
    await query.answer()

    data = query.data
    if data == "back_to_methods":
        keyboard = get_payment_method_selection_keyboard()
        await query.edit_message_text(
            "💳 **Step 1/3: Choose your Receiving Payment Method**\n"
            "How would you like to receive tips from followers?",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return METHOD_CHOICE

    if data.startswith("method_select:"):
        method_code = data.split(":")[1]
        method = get_method(method_code)
        if not method:
            return METHOD_CHOICE

        context.user_data["selected_method"] = method.code
        context.user_data["selected_bank_code"] = method.bank_code
        context.user_data["selected_bank_name"] = method.name

        emoji = "📱" if method.kind == "mobile" else "🏦"
        kind_text = "Phone Number" if method.kind == "mobile" else "Account Number"
        example = "e.g., `0911223344`" if method.kind == "mobile" else "e.g., `1000123456789`"
        await query.edit_message_text(
            f"{emoji} **Selected Method: {method.name}**\n\n"
            f"🔢 **Step 2/3: Enter your {kind_text}**\n"
            f"Please type and send your {method.name} {kind_text.lower()} ({example}):",
            parse_mode="Markdown",
        )
        return ACCOUNT_NUM

    return METHOD_CHOICE


async def account_number_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive bank/telebirr account number and prompt for holder name."""
    if not update.effective_message or not update.effective_message.text:
        return ACCOUNT_NUM

    account_num = update.effective_message.text.strip()
    if len(account_num) < 5:
        await update.effective_message.reply_text("⚠️ Please enter a valid account or phone number.")
        return ACCOUNT_NUM

    context.user_data["account_number"] = account_num
    method = get_method(context.user_data.get("selected_method", "cbe"))
    label = method.account_label if method else "Account"

    await update.effective_message.reply_text(
        f"👤 **Step 3/3: {label} Holder Name**\n"
        f"Please send the exact account holder name registered:",
        parse_mode="Markdown",
    )
    return ACCOUNT_NAME


async def account_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive account holder name and prompt for optional channel link (Step 4/4)."""
    if not update.effective_message or not update.effective_message.text:
        return ACCOUNT_NAME

    account_name = update.effective_message.text.strip()
    context.user_data["account_name"] = account_name

    # Payout updates don't touch the channel link — go straight to confirmation.
    if context.user_data.get("payout_update"):
        return await show_registration_confirmation(update, context)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏩ Skip Channel Link For Now", callback_data="skip_channel_link")]
    ])

    await update.effective_message.reply_text(
        "📢 **Step 4/4: Link Your Telegram Channel (Optional)**\n\n"
        "Forward **ANY message** from your channel into this chat, or send your channel handle (e.g. `@glitchcrafts`).\n\n"
        "*(Or tap Skip below to complete registration and link later using `/addchannel`)*",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return CHANNEL_LINK


async def channel_link_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive channel link/forward or skip callback and show registration summary."""
    query = update.callback_query
    msg = update.effective_message

    if query:
        await query.answer()
        if query.data == "skip_channel_link":
            return await show_registration_confirmation(update, context)

    if msg:
        forward_chat = None
        if hasattr(msg, "forward_origin") and msg.forward_origin and hasattr(msg.forward_origin, "chat"):
            forward_chat = msg.forward_origin.chat
        elif getattr(msg, "forward_from_chat", None):
            forward_chat = msg.forward_from_chat

        raw_input = (msg.text or "").strip()
        if not forward_chat and raw_input:
            clean_handle = raw_input.replace("https://t.me/", "").replace("http://t.me/", "").strip("@/ ")
            if clean_handle:
                try:
                    forward_chat = await context.bot.get_chat(f"@{clean_handle}")
                except TelegramError as e:
                    logger.warning(f"Could not resolve channel @{clean_handle}: {e}")

        if forward_chat and getattr(forward_chat, "type", None) == "channel":
            context.user_data["selected_channel_id"] = str(forward_chat.id)
            context.user_data["selected_channel_title"] = forward_chat.title or forward_chat.username or "Channel"
            await msg.reply_text(f"✅ Linked Channel: **{context.user_data['selected_channel_title']}**", parse_mode="Markdown")

    return await show_registration_confirmation(update, context)


async def show_registration_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Present confirmation summary for creator registration."""
    method = get_method(context.user_data.get("selected_method", "cbe"))
    method_code = method.code if method else "cbe"
    bank_name = context.user_data.get("selected_bank_name", method.name if method else "Bank")
    account_num = context.user_data.get("account_number", "")
    account_name = context.user_data.get("account_name", "")
    channel_title = context.user_data.get("selected_channel_title", "None")

    keyboard = get_confirm_registration_keyboard()
    summary = (
        f"📋 **Registration Confirmation**\n\n"
        f"💳 Payment Method: **{method_code.upper()}** ({bank_name})\n"
        f"🔢 Account Number: `{account_num}`\n"
        f"👤 Account Holder: **{account_name}**\n"
        f"📢 Linked Channel: **{channel_title}**\n\n"
        f"Please verify your details and tap **Confirm & Register** below:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(summary, reply_markup=keyboard, parse_mode="Markdown")
    elif update.effective_message:
        await update.effective_message.reply_text(summary, reply_markup=keyboard, parse_mode="Markdown")
    return CONFIRMATION


async def confirm_registration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirm registration: save Creator record in DB."""
    query = update.callback_query
    if not query or not query.from_user:
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    if data == "reg_cancel":
        context.user_data.pop("payout_update", None)
        await query.edit_message_text("❌ Registration cancelled. Run `/register` whenever you're ready!")
        return ConversationHandler.END

    if data == "back_to_methods":
        keyboard = get_payment_method_selection_keyboard()
        await query.edit_message_text(
            "💳 **Step 1/3: Choose your Receiving Payment Method**\n"
            "How would you like to receive tips from followers?",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return METHOD_CHOICE

    if data == "reg_confirm":
        await query.edit_message_text("⏳ Saving registration details... Please wait.")

        user = query.from_user
        method_code = context.user_data.get("selected_method", "cbe")
        method = get_method(method_code)
        bank_code = method.bank_code if method else 861
        account_num = context.user_data.get("account_number")
        account_name = context.user_data.get("account_name")

        display_name = user.first_name or "Creator"
        if user.last_name:
            display_name += f" {user.last_name}"

        channel_id = context.user_data.get("selected_channel_id")
        async with AsyncSessionLocal() as session:
            stmt = select(Creator).where(Creator.telegram_id == user.id)
            res = await session.execute(stmt)
            creator = res.scalar_one_or_none()

            if creator:
                creator.payment_method = method_code
                creator.bank_code = bank_code
                creator.account_number = account_num
                creator.account_name = account_name
                creator.display_name = display_name
                creator.telegram_username = user.username
                if channel_id:
                    creator.channel_id = channel_id
                if context.user_data.get("payout_update"):
                    # New account, unproven: drop ownership proof until /verifyaccount.
                    creator.account_verified = False
                    creator.account_verification_code = None
                    creator.account_verification_ref = None
            else:
                creator = Creator(
                    telegram_id=user.id,
                    telegram_username=user.username,
                    display_name=display_name,
                    bank_code=bank_code,
                    payment_method=method_code,
                    account_number=account_num,
                    account_name=account_name,
                    channel_id=channel_id,
                )
                session.add(creator)

            await session.commit()
            await session.refresh(creator)

        bot_name = context.bot.username or settings.bot_username
        deep_link = f"https://t.me/{bot_name}?start=tip_{creator.id}"

        if context.user_data.pop("payout_update", None):
            await query.edit_message_text(
                f"✅ **Payout Details Updated!**\n\n"
                f"New Payout Method: **{method_code.upper()}** (`{account_num}`)\n\n"
                f"🔐 **Action required:** run `/verifyaccount` to prove the new account is yours — "
                f"tips stay protected while ownership is unproven.\n\n"
                f"🔗 Your tipping link is unchanged:\n`{deep_link}`",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

        await query.edit_message_text(
            f"🎉 **Registration Successful!**\n\n"
            f"Configured Payment Method: **{method_code.upper()}**\n\n"
            f"🔗 **Your Personal Tipping Deep Link:**\n`{deep_link}`\n\n"
            f"🔐 **Recommended:** Run `/verifyaccount` to prove you own this account — "
            f"it protects your tips from being sent to the wrong place.\n\n"
            f"💡 **Pro Tip:** Run `/post` to get an inline tip button for your Telegram channel posts!",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    return ConversationHandler.END


async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel registration flow."""
    if update.effective_message:
        await update.effective_message.reply_text("❌ Registration cancelled. Run `/register` whenever you're ready!")
    return ConversationHandler.END


async def tip_amount_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle preset tip amount selection, custom amount, note prompts, or back navigation."""
    query = update.callback_query
    if not query or not query.data or not query.from_user:
        return
    await query.answer()

    data = query.data
    if data == "tip_cancel":
        await query.edit_message_text("❌ Tip cancelled.")
        return

    if data.startswith("back_to_amounts:"):
        creator_id_str = data.split(":")[1]
        keyboard = get_tip_amount_keyboard(creator_id_str)
        await query.edit_message_text(
            "🎁 Choose an amount below to tip directly in Birr (ETB):",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return

    if data.startswith("tip_amt:"):
        parts = data.split(":")
        creator_id_str = parts[1]
        amount = Decimal(parts[2])

        keyboard = get_tip_note_prompt_keyboard(creator_id_str, amount)
        await query.edit_message_text(
            f"💰 Selected Tip Amount: **{amount:g} ETB**\n\n"
            f"Would you like to add an optional note/message for the creator?",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

    elif data.startswith("tip_custom:"):
        parts = data.split(":")
        creator_id_str = parts[1]
        context.user_data["pending_tip_creator_id"] = creator_id_str

        await query.edit_message_text(
            "✏️ Please reply to this message with your custom tip amount in Birr (e.g. 75 or 250):",
            parse_mode="Markdown",
        )

    elif data.startswith("tip_add_note:"):
        parts = data.split(":")
        creator_id_str = parts[1]
        amount = Decimal(parts[2])
        context.user_data["pending_note_data"] = (creator_id_str, amount)

        await query.edit_message_text(
            "💬 Please send your message/note for the creator below:",
            parse_mode="Markdown",
        )

    elif data.startswith("tip_pay_now:"):
        parts = data.split(":")
        creator_id_str = parts[1]
        amount = float(parts[2])

        await process_tip_initialization(
            update=update,
            context=context,
            creator_id_str=creator_id_str,
            amount=amount,
            note=None,
            is_edit=True,
        )

    elif data.startswith("tip_sent:"):
        parts = data.split(":")
        tip_id_str = parts[1]
        context.user_data["pending_verify_tip_id"] = tip_id_str

        await query.edit_message_text(
            "📝 **Payment Sent Confirmation**\n\n"
            "Please send the transaction **Reference Number** / **SMS Code** (e.g., `TLB12345678` or `FT12345678`), **OR upload / paste a screenshot photo of your payment receipt**! 📸",
            parse_mode="Markdown",
        )

    elif data.startswith("approve_tip:"):
        tip_id_str = data.split(":")[1]
        await handle_creator_approval(update, context, tip_id_str, is_approve=True)

    elif data.startswith("reject_tip:"):
        tip_id_str = data.split(":")[1]
        await handle_creator_approval(update, context, tip_id_str, is_approve=False)


def _mask_digits(text: str) -> str:
    """Mask runs of 4+ digits (account/phone numbers in OCR dumps)."""
    return re.sub(
        r"\d{4,}",
        lambda m: m.group()[:2] + "*" * (len(m.group()) - 3) + m.group()[-1],
        text,
    )


def extract_ref_code_from_image(image_bytes: bytes) -> str | None:
    """Extract a transaction reference number from a receipt screenshot image via OCR & regex."""
    from app.storage import MAX_IMAGE_PIXELS

    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.width * img.height > MAX_IMAGE_PIXELS:
            logger.warning("Skipping OCR on oversized image (%dx%d)", img.width, img.height)
            return None
        ocr_text = pytesseract.image_to_string(img)
        logger.info(f"OCR Extracted text snippet: {_mask_digits(ocr_text[:200])}")

        patterns = [
            r"\b(TLB[A-Za-z0-9]{7,16})\b",
            r"\b(FT[0-9]{8,16})\b",
            r"\b(TX[A-Za-z0-9]{6,16})\b",
            r"\b([A-Za-z0-9]{10,16})\b",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, ocr_text)
            for m in matches:
                clean_m = m.upper()
                if len(clean_m) >= 6 and re.match(r"^[A-Z0-9\-_]{6,30}$", clean_m):
                    return clean_m
    except (pytesseract.TesseractError, OSError, ValueError) as e:
        logger.warning(f"OCR processing fallback: {e}")
    return None


async def receipt_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle receipt screenshot upload by tipper during verification step."""
    if not update.effective_message or not update.effective_message.photo:
        return

    if "pending_verify_tip_id" not in context.user_data:
        await update.effective_message.reply_text("📸 Please start a tip session first or run /help!")
        return

    tip_id_str = context.user_data.get("pending_verify_tip_id")
    photo = update.effective_message.photo[-1]
    photo_file = await photo.get_file()
    photo_bytes = await photo_file.download_as_bytearray()

    # Persist the screenshot as dispute evidence before anything else.
    from app.storage import save_receipt_photo

    receipt_path = save_receipt_photo(tip_id_str, bytes(photo_bytes))
    if receipt_path:
        # Keyed to the tip so an abandoned upload never lands on another claim.
        context.user_data["pending_receipt_path"] = (tip_id_str, receipt_path)

    caption = (update.effective_message.caption or "").strip()
    extracted_ref = None

    if caption:
        match = re.search(r"\b([A-Za-z0-9\-_]{6,30})\b", caption)
        if match:
            extracted_ref = match.group(1).upper()

    if not extracted_ref:
        # Preferred: vision LLM reads structured data from the screenshot (#8).
        try:
            from app.receipt_vision import parse_receipt_image

            vision = await parse_receipt_image(bytes(photo_bytes))
            if vision and vision.get("reference"):
                extracted_ref = str(vision["reference"]).upper()
        except Exception:
            logger.warning("Vision receipt parsing fallback failed", exc_info=True)

    if not extracted_ref:
        extracted_ref = extract_ref_code_from_image(bytes(photo_bytes))

    if extracted_ref:
        context.user_data.pop("pending_verify_tip_id")
        await update.effective_message.reply_text(
            t(_lang(context), "ocr_processed", ref=extracted_ref),
            parse_mode="Markdown",
        )
        await process_tip_verification_claim(update, context, tip_id_str, ref_code=extracted_ref)
    else:
        await update.effective_message.reply_text(
            t(_lang(context), "ref_prompt"),
            parse_mode="Markdown",
        )


def _resolve_claim_reference(text: str) -> tuple[str, bool]:
    """Accept either a bare reference code or a pasted/forwarded payment SMS.

    Returns (reference, from_sms). Falls back to the raw text when nothing
    SMS-like is detected so existing bare-code behaviour is unchanged.
    """
    from app.sms_parse import parse_payment_sms

    parsed = parse_payment_sms(text)
    if parsed:
        return parsed.reference, True
    return text.strip(), False


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle custom tip amount, note entry, or payment reference code."""
    if not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text.strip()

    # Case 0: Pending Tipa Pro payment reference code
    if context.user_data.pop("pending_pro_ref", None):
        clean_ref, _from_sms = _resolve_claim_reference(text)
        if not re.match(r"^[A-Za-z0-9\-_]{6,30}$", clean_ref):
            context.user_data["pending_pro_ref"] = True
            await update.effective_message.reply_text(
                "⚠️ **Invalid Reference / SMS Code Format**\n\n"
                "Please enter a valid transaction reference code (e.g. Telebirr `TLB12345678` or CBE `FT12345678`) "
                "with at least 6 characters, no spaces or special symbols:",
                parse_mode="Markdown",
            )
            return
        await process_subscription_claim(update, context, ref_code=clean_ref)
        return

    # Case 0.5: Pending account-ownership deposit reference code
    if context.user_data.pop("pending_av_ref", None):
        clean_ref, _from_sms = _resolve_claim_reference(text)
        if not re.match(r"^[A-Za-z0-9\-_]{6,30}$", clean_ref):
            context.user_data["pending_av_ref"] = True
            await update.effective_message.reply_text(
                "⚠️ **Invalid Reference / SMS Code Format**\n\n"
                "Please enter a valid transaction reference code (e.g. Telebirr `TLB12345678` or CBE `FT12345678`) "
                "with at least 6 characters, no spaces or special symbols:",
                parse_mode="Markdown",
            )
            return
        await process_account_verification_claim(update, context, ref_code=clean_ref)
        return

    # Case A: Pending transaction reference verification code
    if "pending_verify_tip_id" in context.user_data:
        tip_id_str = context.user_data.get("pending_verify_tip_id")
        clean_ref, from_sms = _resolve_claim_reference(text)
        if from_sms:
            # Whole SMS pasted/forwarded — acknowledge the magic briefly.
            await update.effective_message.reply_text(
                f"🔎 Detected transaction reference `{clean_ref}` from your SMS…",
                parse_mode="Markdown",
            )

        # Validate reference code format (must be 6-30 alphanumeric characters)
        if not re.match(r"^[A-Za-z0-9\-_]{6,30}$", clean_ref):
            await update.effective_message.reply_text(
                t(_lang(context), "invalid_ref"),
                parse_mode="Markdown",
            )
            return

        context.user_data.pop("pending_verify_tip_id")
        await process_tip_verification_claim(update, context, tip_id_str, ref_code=clean_ref)
        return

    # Case B: Pending custom note entry
    if "pending_note_data" in context.user_data:
        creator_id_str, amount = context.user_data.pop("pending_note_data")
        note = text[:280]
        await process_tip_initialization(
            update=update,
            context=context,
            creator_id_str=creator_id_str,
            amount=amount,
            note=note,
            is_edit=False,
        )
        return

    # Case C: Pending custom tip amount entry
    if "pending_tip_creator_id" in context.user_data:
        creator_id_str = context.user_data.pop("pending_tip_creator_id")
        try:
            amount = Decimal(text)
            if amount != amount.quantize(Decimal("0.01")):
                await update.effective_message.reply_text(
                    "⚠️ Amounts support at most two decimal places (e.g. 75 or 75.50). Please try again:"
                )
                context.user_data["pending_tip_creator_id"] = creator_id_str
                return
        except InvalidOperation:
            await update.effective_message.reply_text("⚠️ Invalid number. Please enter a numerical amount in Birr (e.g. 75):")
            context.user_data["pending_tip_creator_id"] = creator_id_str
            return
        if amount < MIN_TIP_BIRR:
            await update.effective_message.reply_text(f"⚠️ Minimum tip amount is {MIN_TIP_BIRR:g} Birr. Please try again:")
            context.user_data["pending_tip_creator_id"] = creator_id_str
            return
        if amount > MAX_TIP_BIRR:
            await update.effective_message.reply_text(f"⚠️ Maximum single tip amount is {MAX_TIP_BIRR:g} Birr. Please try again:")
            context.user_data["pending_tip_creator_id"] = creator_id_str
            return

        keyboard = get_tip_note_prompt_keyboard(creator_id_str, amount)
        await update.effective_message.reply_text(
            f"💰 Selected Tip Amount: **{amount:g} ETB**\n\n"
            f"Would you like to add an optional note/message for the creator?",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return


async def process_tip_initialization(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    creator_id_str: str,
    amount: float,
    note: str | None = None,
    is_edit: bool = False,
) -> None:
    """Initialize tip record and present payment instructions (Telebirr / CBE)."""
    try:
        creator_uuid = uuid.UUID(creator_id_str)
    except ValueError:
        msg = "❌ Invalid creator ID."
        if is_edit and update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.effective_message:
            await update.effective_message.reply_text(msg)
        return

    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.id == creator_uuid)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()

        if not creator:
            msg = "❌ Creator not found."
            if is_edit and update.callback_query:
                await update.callback_query.edit_message_text(msg)
            elif update.effective_message:
                await update.effective_message.reply_text(msg)
            return

        if creator.is_frozen:
            msg = "⚠️ This creator is temporarily unable to receive tips. Please try again later."
            if is_edit and update.callback_query:
                await update.callback_query.edit_message_text(msg)
            elif update.effective_message:
                await update.effective_message.reply_text(msg)
            return

        tx_ref = f"tipa_{uuid.uuid4().hex[:12]}"
        user = update.effective_user
        tipper_id = user.id if user else None
        tipper_name = user.first_name if user else "Anonymous"
        if user and user.last_name:
            tipper_name += f" {user.last_name}"

        post_id = context.user_data.get("active_post_id")
        tip_record = Tip(
            creator_id=creator.id,
            tipper_telegram_id=tipper_id,
            tipper_display_name=tipper_name,
            amount=amount,
            platform_fee=settings.platform_fee_birr,
            tx_ref=tx_ref,
            status="pending",
            note=note,
            post_id=post_id,
        )
        session.add(tip_record)
        await session.commit()
        await session.refresh(tip_record)

        method_code = creator.payment_method
        method = get_method(method_code) or get_method("telebirr")
        note_display = f"\n💬 Note: *\"{note}\"*" if note else ""
        ussd = ussd_code_for(method_code)
        emoji = "📱" if method.kind == "mobile" else "🏦"
        account_label = method.account_label or ("Phone" if method.kind == "mobile" else "Account Number")

        keyboard = get_transfer_keyboard(method_code, str(tip_record.id))

        ussd_line = (
            t(
                _lang(context),
                "pay_ussd_line",
                ussd=ussd,
                account_number=creator.account_number,
            )
            if ussd
            else ""
        )
        instructions = t(
            _lang(context),
            "pay_instructions",
            emoji=emoji,
            method_name=method.name,
            recipient=creator.account_name,
            creator_name=creator.display_name,
            account_label=account_label,
            account_number=creator.account_number,
            amount=f"{amount:g}",
            note_display=note_display,
            tx_ref=tx_ref,
            ussd_line=ussd_line,
        )

        if is_edit and update.callback_query:
            await update.callback_query.edit_message_text(instructions, reply_markup=keyboard, parse_mode="Markdown")
        elif update.effective_message:
            await update.effective_message.reply_text(instructions, reply_markup=keyboard, parse_mode="Markdown")


async def _is_duplicate_ref(session, tip_id: uuid.UUID, ref_code: str) -> bool:
    """True when another active tip already claimed the same receipt reference.

    One SMS receipt code maps to exactly one real transfer, so reusing it on a
    second tip is either a mistake or fraud — the DB unique index on ``ref_id``
    is the backstop, this check gives the tipper a clear message.
    """
    stmt = (
        select(Tip.id)
        .where(
            Tip.ref_id == ref_code,
            Tip.id != tip_id,
            Tip.status.in_(["pending", "pending_verification", "success"]),
        )
        .limit(1)
    )
    res = await session.execute(stmt)
    return res.first() is not None


async def process_tip_verification_claim(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tip_id_str: str,
    ref_code: str,
) -> None:
    """Process claimed payment ref code: reject duplicates, auto-verify via providers, else creator approval."""
    try:
        tip_uuid = uuid.UUID(tip_id_str)
    except ValueError:
        if update.effective_message:
            await update.effective_message.reply_text("❌ Invalid Tip session.")
        return

    async with AsyncSessionLocal() as session:
        stmt = select(Tip).where(Tip.id == tip_uuid)
        res = await session.execute(stmt)
        tip = res.scalar_one_or_none()

        if not tip:
            if update.effective_message:
                await update.effective_message.reply_text("❌ Tip transaction not found.")
            return

        c_stmt = select(Creator).where(Creator.id == tip.creator_id)
        c_res = await session.execute(c_stmt)
        creator = c_res.scalar_one_or_none()

        if not creator:
            if update.effective_message:
                await update.effective_message.reply_text("❌ Creator not found.")
            return

        if await _is_duplicate_ref(session, tip.id, ref_code):
            if update.effective_message:
                await update.effective_message.reply_text(
                    f"❌ **Duplicate Reference Code**\n\n"
                    f"The code `{ref_code}` was already used for another tip. "
                    f"Please double-check your SMS receipt code and try again.",
                    parse_mode="Markdown",
                )
            return

        tip.ref_id = ref_code
        tip.claimed_at = datetime.now(timezone.utc)
        pending_receipt = context.user_data.get("pending_receipt_path")
        if pending_receipt and pending_receipt[0] == tip_id_str:
            tip.receipt_file_path = pending_receipt[1]
            context.user_data.pop("pending_receipt_path", None)
        try:
            await session.commit()
        except IntegrityError:
            # Concurrent claim won the unique race on Tip.ref_id.
            await session.rollback()
            if update.effective_message:
                await update.effective_message.reply_text(
                    f"❌ **Duplicate Reference Code**\n\n"
                    f"The code `{ref_code}` was already used for another tip. "
                    f"Please double-check your SMS receipt code and try again.",
                    parse_mode="Markdown",
                )
            return

        verify_result = await auto_verify_tip(session, tip, creator, ref_code)

        if verify_result is not None and verify_result.verified:
            if update.effective_message:
                await update.effective_message.reply_text(
                    t(
                        _lang(context),
                        "tip_verified",
                        ref=ref_code,
                        amount=f"{float(tip.amount):g}",
                        creator_name=creator.display_name,
                    ),
                    parse_mode="Markdown",
                )
            await notify_tip_success(str(tip.id))
            return

        tip.status = "pending_verification"
        await session.commit()

        tipper_name = tip.tipper_display_name or "A follower"
        note_str = f"\n💬 **Note:** *\"{tip.note}\"*\n" if tip.note else ""
        post_str = f"\n📌 **Channel Post:** #{tip.post_id}\n" if tip.post_id else ""
        method_str = method_name(creator.payment_method)

        creator_approval_msg = (
            f"💸 **New Tip Received via {method_str}!**\n\n"
            f"**{tipper_name}** claims they sent **{float(tip.amount):g} ETB** to your `{creator.account_number}`.\n"
            f"Receipt / Ref Code: `{ref_code}`\n"
            f"{post_str}"
            f"{note_str}\n"
            f"Please check your {method_str} app and tap **Approve Tip** below to confirm:"
        )

        try:
            approval_kb = get_creator_approval_keyboard(str(tip.id))
            await context.bot.send_message(
                chat_id=creator.telegram_id,
                text=creator_approval_msg,
                reply_markup=approval_kb,
                parse_mode="Markdown",
            )
        except TelegramError as e:
            logger.error(f"Failed to send creator approval notification: {e}")

    if update.effective_message:
        await update.effective_message.reply_text(
            t(
                _lang(context),
                "claim_submitted",
                ref=ref_code,
                amount=f"{float(tip.amount):g}",
                creator_name=creator.display_name,
            ),
            parse_mode="Markdown",
        )


async def handle_creator_approval(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tip_id_str: str,
    is_approve: bool,
) -> None:
    """Handle Creator's 1-tap Approve or Reject inline callback.

    Authorization: only the receiving creator may process a tip claim —
    callback data is client-controlled, so anyone who obtains the approval
    message (forwarded, screenshotted into another chat) must be rejected.
    State guard: already-processed tips (approved, rejected, expired) are
    final; double-taps never overwrite state.
    """
    query = update.callback_query
    if not query:
        return

    try:
        tip_uuid = uuid.UUID(tip_id_str)
    except ValueError:
        await query.answer("Invalid tip ID.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        stmt = select(Tip).where(Tip.id == tip_uuid)
        res = await session.execute(stmt)
        tip = res.scalar_one_or_none()

        if not tip:
            await query.answer("Tip not found.", show_alert=True)
            return

        c_stmt = select(Creator).where(Creator.id == tip.creator_id)
        c_res = await session.execute(c_stmt)
        creator = c_res.scalar_one_or_none()

        if creator is None:
            await query.answer("Creator not found.", show_alert=True)
            return

        if query.from_user.id != creator.telegram_id:
            logger.warning(
                "Blocked tip approval by non-owner: user %s tried to process tip %s owned by %s",
                query.from_user.id,
                tip.id,
                creator.telegram_id,
            )
            await query.answer("⛔ Only the creator can process this tip.", show_alert=True)
            return

        if tip.status != "pending_verification":
            await query.answer(f"This tip was already processed ({tip.status}).", show_alert=True)
            return

        await query.answer()

        if is_approve:
            # Atomic transition — two callbacks can pass the status pre-check
            # concurrently, but only one flips pending_verification -> success;
            # the loser never double-fires side effects (webhooks, invites).
            claimed = await session.execute(
                sa_update(Tip)
                .where(Tip.id == tip.id, Tip.status == "pending_verification")
                .values(
                    status="success",
                    verified_at=datetime.now(timezone.utc),
                    verification_method="creator_approval",
                )
            )
            await session.commit()
            if claimed.rowcount != 1:
                # Lost a concurrent approval/reject race — winner already
                # answered this callback query, so stay silent.
                logger.info("Tip %s approval race lost by another callback", tip.id)
                return

            await log_verification_attempt(
                session,
                tip_id=tip.id,
                provider="creator_approval",
                status="success",
                verified=True,
                amount=tip.amount,
                message="Approved manually by the creator",
            )

            # Refresh any bound tip-goal post / celebrate a reached goal.
            try:
                from app.goals import on_tip_verified

                await on_tip_verified(tip.id, bot=context.bot)
            except Exception:
                logger.exception("Goal refresh failed after approving tip %s", tip.id)

            # Pay-to-unlock: DM the tipper a one-time VIP channel invite.
            if tip.tipper_telegram_id:
                try:
                    from app.unlock import send_unlock_invite

                    await send_unlock_invite(str(tip.id), bot=context.bot)
                except Exception:
                    logger.exception("VIP unlock failed after approving tip %s", tip.id)

            # Live overlay alert for OBS viewers.
            try:
                from app.overlay import publish_tip

                publish_tip(
                    str(creator.id),
                    {
                        "amount": float(tip.amount),
                        "tipper": tip.tipper_display_name,
                        "note": tip.note,
                    },
                )
            except Exception:
                logger.exception("Overlay publish failed after approving tip %s", tip.id)

            # Outbound signed webhook (fire-and-forget).
            try:
                import asyncio as _asyncio

                from app.webhooks import deliver_tip_verified

                _asyncio.create_task(deliver_tip_verified(str(tip.id)))
            except Exception:
                logger.exception("Webhook scheduling failed after approving tip %s", tip.id)

            note_str = f" (*\"{tip.note}\"*)" if tip.note else ""
            await query.edit_message_text(
                f"🎉 **Tip Approved & Verified!**\n\n"
                f"Confirmed **{float(tip.amount):g} ETB** tip from {tip.tipper_display_name or 'A follower'}{note_str}.\n"
                f"Run `/mytips` to view your updated dashboard.",
                parse_mode="Markdown",
            )

            if tip.tipper_telegram_id:
                try:
                    await context.bot.send_message(
                        chat_id=tip.tipper_telegram_id,
                        text=f"🎉 **Tip Verified!**\n\nYour **{float(tip.amount):g} ETB** tip to **{creator.display_name}** has been confirmed by the creator! Thank you for your support! 🙏",
                        parse_mode="Markdown",
                    )
                except TelegramError as e:
                    logger.error(f"Failed to notify tipper: {e}")
        else:
            claimed = await session.execute(
                sa_update(Tip)
                .where(Tip.id == tip.id, Tip.status == "pending_verification")
                .values(status="failed")
            )
            await session.commit()
            if claimed.rowcount != 1:
                logger.info("Tip %s rejection race lost by another callback", tip.id)
                return
            await log_verification_attempt(
                session,
                tip_id=tip.id,
                provider="creator_approval",
                status="failed",
                verified=False,
                amount=tip.amount,
                message="Rejected by the creator",
            )
            await query.edit_message_text(f"❌ **Tip Claim Rejected**\n\nMarked claim for `{tip.ref_id}` as unverified/rejected.")
            if tip.tipper_telegram_id:
                try:
                    await context.bot.send_message(
                        chat_id=tip.tipper_telegram_id,
                        text=f"❌ **Tip Claim Unverified**\n\nYour tip claim for **{float(tip.amount):g} ETB** (Ref: `{tip.ref_id}`) could not be verified by **{creator.display_name}**.",
                        parse_mode="Markdown",
                    )
                except TelegramError as e:
                    logger.error(f"Failed to notify tipper of rejection: {e}")


async def channel_post_generator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate ready-to-use channel post snippet and tip button."""
    if not update.effective_message or not update.effective_user:
        return

    user_id = update.effective_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()

        if not creator:
            await update.effective_message.reply_text(
                "❌ You must register first with `/register` to generate your channel post button!",
                parse_mode="Markdown",
            )
            return

    bot_name = context.bot.username or settings.bot_username
    keyboard = get_channel_post_button(bot_name, str(creator.id))

    from app.goals import get_active_goal, goal_progress_line, goal_raised_amount

    goal_line = ""
    async with AsyncSessionLocal() as session:
        active_goal = await get_active_goal(session, creator.id)
        if active_goal:
            current = await goal_raised_amount(session, active_goal)
            goal_line = "\n\n" + goal_progress_line(active_goal, current)

    await update.effective_message.reply_text(
        "📢 **Channel Post & Tip Button Generator**\n\n"
        "Forward or copy the message below to your channel so followers can tip you with one tap!\n\n"
        "---",
        parse_mode="Markdown",
    )

    await update.effective_message.reply_text(
        "✨ **Enjoying the content?** Support this channel directly in Birr (ETB) via Tipa!"
        f"{goal_line}",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def poster_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a printable A4 QR poster linking to the creator's tipping page."""
    if not update.effective_message or not update.effective_user:
        return

    user_id = update.effective_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        creator = (await session.execute(stmt)).scalar_one_or_none()

    if not creator:
        await update.effective_message.reply_text(
            "❌ You must register first with `/register` to generate a poster!",
            parse_mode="Markdown",
        )
        return

    bot_name = context.bot.username or settings.bot_username
    from app.posters import build_poster_pdf, tip_deep_link

    url = tip_deep_link(bot_name, creator.id)
    pdf = build_poster_pdf(creator, url)
    await update.effective_message.reply_document(
        document=io.BytesIO(pdf),
        filename=f"tipa_poster_{str(creator.id)[:8]}.pdf",
        caption=(
            "🖼️ **Your Tip Poster is ready!**\n\n"
            "Print it for your café, shop, studio, or event table — "
            "one scan opens your tipping page. A4 size."
        ),
        parse_mode="Markdown",
    )


async def setvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enable pay-to-unlock: verified tippers get a one-time VIP channel invite."""
    if not update.effective_message or not update.effective_user:
        return

    from app.unlock import set_vip_channel

    # Preferred input: forward any post from the private channel.
    forwarded_chat = getattr(update.effective_message, "forward_origin", None)
    source_chat = None
    if forwarded_chat is not None and hasattr(forwarded_chat, "chat"):
        source_chat = forwarded_chat.chat
    elif getattr(update.effective_message, "forward_from_chat", None):
        source_chat = update.effective_message.forward_from_chat  # legacy field

    if source_chat is not None:
        raw = str(source_chat.id)
    else:
        args = (context.args or []) if context.args else []
        raw = " ".join(args)

    _ok, message = await set_vip_channel(update.effective_user.id, raw)
    await update.effective_message.reply_text(message, parse_mode="Markdown")


async def unsetvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disable VIP unlock invites."""
    if not update.effective_message or not update.effective_user:
        return

    from app.unlock import unset_vip_channel

    removed = await unset_vip_channel(update.effective_user.id)
    await update.effective_message.reply_text(
        "🔓 **VIP Unlock disabled.** Future tips won't include channel invites."
        if removed
        else "You don't have VIP unlock enabled.",
        parse_mode="Markdown",
    )


async def webhook_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Register an HMAC-signed webhook endpoint (`/webhook https://...`, `/webhook off`)."""
    if not update.effective_message or not update.effective_user:
        return

    from app.webhooks import disable_webhook, set_webhook

    args = (context.args or []) if context.args else []
    raw = " ".join(args).strip()

    if not raw or raw.lower() == "help":
        await update.effective_message.reply_text(
            "🔗 **Webhooks** — get a POST for every verified tip.\n\n"
            "`/webhook https://yourapp.com/tipa` — register & receive your signing secret\n"
            "`/webhook off` — disable delivery\n\n"
            "Events carry headers `X-Tipa-Event: tip.verified` and "
            "`X-Tipa-Signature` (HMAC-SHA256 hex of the raw body keyed by your secret).",
            parse_mode="Markdown",
        )
        return

    if raw.lower() in ("off", "disable", "remove"):
        removed = await disable_webhook(update.effective_user.id)
        await update.effective_message.reply_text(
            "🔕 Webhook disabled." if removed else "You don't have an active webhook.",
            parse_mode="Markdown",
        )
        return

    _ok, message = await set_webhook(update.effective_user.id, raw)
    await update.effective_message.reply_text(message, parse_mode="Markdown")


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Telegram Inline Mode queries (@TipaPayBot) in any chat/channel."""
    inline_query = update.inline_query
    if not inline_query or not inline_query.from_user:
        return

    user_id = inline_query.from_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()

    bot_name = context.bot.username or settings.bot_username
    if not creator:
        results = [
            InlineQueryResultArticle(
                id="not_registered",
                title="⚠️ Register with Tipa First",
                description="Run /register in private chat with @TipaPayBot to get your tipping button",
                input_message_content=InputTextMessageContent(
                    f"Please register your payment details with @{bot_name} first using /register!"
                ),
            )
        ]
        await inline_query.answer(results, cache_time=1)
        return

    keyboard = get_channel_post_button(bot_name, str(creator.id))
    results = [
        InlineQueryResultArticle(
            id=f"tip_button_{creator.id}",
            title=f"🎁 Tip {creator.display_name} Button",
            description="Attach a 1-tap Birr tipping button to your channel post",
            reply_markup=keyboard,
            input_message_content=InputTextMessageContent(
                f"✨ **Support {creator.display_name}**\n\n"
                f"If you enjoyed this post, tap below to tip in Ethiopian Birr (ETB)!",
                parse_mode="Markdown",
            ),
        )
    ]
    await inline_query.answer(results, cache_time=5)


async def mytips_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Creator dashboard: sum of successful tips, count, and 5 most recent tips."""
    if not update.effective_message or not update.effective_user:
        return

    user_id = update.effective_user.id

    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()

        if not creator:
            await update.effective_message.reply_text(
                "❌ You are not registered as a creator yet.\nRun `/register` to link your bank or Telebirr account!",
                parse_mode="Markdown",
            )
            return

        tot_stmt = (
            select(
                func.coalesce(func.sum(Tip.amount), 0),
                func.count(Tip.id),
            )
            .where(Tip.creator_id == creator.id)
            .where(Tip.status == "success")
        )
        tot_res = await session.execute(tot_stmt)
        total_amount, total_count = tot_res.first() or (0, 0)

        rec_stmt = (
            select(Tip)
            .where(Tip.creator_id == creator.id)
            .where(Tip.status == "success")
            .order_by(desc(Tip.verified_at), desc(Tip.created_at))
            .limit(5)
        )
        rec_res = await session.execute(rec_stmt)
        recent_tips = rec_res.scalars().all()

        active_sub = await get_active_subscription(session, creator.id)

        from app.goals import get_active_goal, goal_progress_line, goal_raised_amount

        goal = await get_active_goal(session, creator.id)
        goal_dash_line = ""
        if goal:
            current = await goal_raised_amount(session, goal)
            goal_dash_line = "\n" + goal_progress_line(goal, current).replace("*", "") + "\n"

    deep_link = f"https://t.me/{context.bot.username}?start=tip_{creator.id}"
    method_str = method_name(creator.payment_method)
    lang = creator.language or _lang(context)
    if active_sub and active_sub.expires_at:
        pro_line = t(lang, "pro_line_active", date=active_sub.expires_at.strftime("%b %d, %Y"))
    else:
        pro_line = t(lang, "pro_line_inactive")
    av_line = "" if creator.account_verified else t(lang, "av_line_unverified")
    text = (
        t(
            lang,
            "mytips_dashboard",
            display_name=creator.display_name,
            method=method_str,
            account_number=creator.account_number,
            total=f"{float(total_amount):,.2f}",
            count=total_count,
            pro_line=pro_line,
            av_line=av_line,
            deep_link=deep_link,
        )
        + goal_dash_line
    )

    if recent_tips:
        text += t(lang, "recent_tips_header")
        for tip_row in recent_tips:
            tipper = tip_row.tipper_display_name or "Anonymous"
            note_str = f" (*\"{tip_row.note}\"*)" if tip_row.note else ""
            date_str = (
                tip_row.verified_at.strftime("%Y-%m-%d %H:%M")
                if tip_row.verified_at
                else tip_row.created_at.strftime("%Y-%m-%d %H:%M")
            )
            text += f"• **{float(tip_row.amount):g} ETB** from {tipper}{note_str} ({date_str})\n"
    else:
        text += t(lang, "no_tips_yet")

    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set or replace the creator's public fundraising goal (live progress bar)."""
    if not update.effective_message or not update.effective_user:
        return

    args = (context.args or []) if context.args else []
    if len(args) < 2:
        lang = _lang(context)
        await update.effective_message.reply_text(
            t(lang, "goal_usage"),
            parse_mode="Markdown",
        )
        return

    try:
        target = Decimal(args[0])
        if target <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await update.effective_message.reply_text("⚠️ Target must be a positive number, e.g. `/goal 10000 New camera`.", parse_mode="Markdown")
        return

    title = " ".join(args[1:]).strip()[:120]

    user_id = update.effective_user.id
    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        creator = (await session.execute(stmt)).scalar_one_or_none()
        if not creator:
            await update.effective_message.reply_text(
                "❌ You are not registered as a creator yet.\nRun `/register` first!",
                parse_mode="Markdown",
            )
            return

        from app.goals import create_goal, goal_progress_line, goal_raised_amount

        goal = await create_goal(session, creator.id, title, target)
        current = await goal_raised_amount(session, goal)
        line = goal_progress_line(goal, current)

    lang = creator.language or _lang(context)
    await update.effective_message.reply_text(
        t(lang, "goal_set", line=line),
        parse_mode="Markdown",
    )


async def endgoal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove the creator's active goal."""
    if not update.effective_message or not update.effective_user:
        return

    user_id = update.effective_user.id
    lang = _lang(context)
    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        creator = (await session.execute(stmt)).scalar_one_or_none()
        if not creator:
            await update.effective_message.reply_text(
                "❌ You are not registered as a creator yet.\nRun `/register` first!",
                parse_mode="Markdown",
            )
            return

        from app.goals import cancel_goal

        removed = await cancel_goal(session, creator.id)

    await update.effective_message.reply_text(
        t(creator.language or lang, "goal_cancelled" if removed else "goal_none"),
        parse_mode="Markdown",
    )


async def topfans_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Monthly top-tipper leaderboard with all-time tier badges."""

    from app.fans import fan_tier, top_tippers

    if not update.effective_message or not update.effective_user:
        return

    user_id = update.effective_user.id
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        creator = (await session.execute(stmt)).scalar_one_or_none()
        if not creator:
            await update.effective_message.reply_text(
                "❌ You are not registered as a creator yet.\nRun `/register` first!",
                parse_mode="Markdown",
            )
            return

        rows = await top_tippers(session, creator.id, since=month_start, limit=10)

    lang = creator.language or _lang(context)
    if not rows:
        await update.effective_message.reply_text(t(lang, "topfans_empty"), parse_mode="Markdown")
        return

    # One windowless pass for every supporter's all-time total (drives tier badges).
    async with AsyncSessionLocal() as session:
        all_rows = await top_tippers(session, creator.id, limit=100)
    totals = {r["telegram_id"]: r["total"] for r in all_rows}

    lines = [t(lang, "topfans_header")]
    for i, row in enumerate(rows, start=1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        badge = fan_tier(totals.get(row["telegram_id"], 0))
        lines.append(f"{medal} **{row['name']}** — {float(row['total']):,.0f} ETB ({badge})")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Export the creator's verified tips as CSV or PDF (via `/export pdf`)."""
    if not update.effective_message or not update.effective_user:
        return

    user_id = update.effective_user.id
    want_pdf = bool(context.args) and context.args[0].lower() in ("pdf", "receipt")

    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.telegram_id == user_id)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()

        if not creator:
            await update.effective_message.reply_text(
                "❌ You are not registered as a creator yet.\nRun `/register` to link your bank or Telebirr account!",
                parse_mode="Markdown",
            )
            return

        if not await is_pro(session, creator.id):
            await update.effective_message.reply_text(
                "⭐ **Export is a Tipa Pro feature!**\n\n"
                "Unlock it (plus more Pro perks) by upgrading with `/pro` — "
                f"just **{settings.pro_price_birr:g} ETB / {settings.pro_duration_days} days**.",
                parse_mode="Markdown",
            )
            return

        t_stmt = (
            select(Tip)
            .where(Tip.creator_id == creator.id, Tip.status == "success")
            .order_by(desc(Tip.verified_at), desc(Tip.created_at))
        )
        t_res = await session.execute(t_stmt)
        tips = t_res.scalars().all()

    if not tips:
        await update.effective_message.reply_text(
            "📄 **No successful tips to export yet.**\n"
            "Share your tip link to start receiving tips — they will appear here once verified!",
            parse_mode="Markdown",
        )
        return

    if want_pdf:
        filename = f"tipa_{creator.telegram_id}_tips.pdf"
        document = io.BytesIO(build_tips_pdf(tips, creator))
    else:
        filename = f"tipa_{creator.telegram_id}_tips.csv"
        document = io.BytesIO(build_tips_csv(tips).encode("utf-8"))
    await update.effective_message.reply_document(
        document=document,
        filename=filename,
        caption=f"📄 **{len(tips)} tips exported** ({filename})",
        parse_mode="Markdown",
    )


async def addchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Link a creator's channel by message forwarding, @username, or t.me link."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return

    # Extract forwarded chat object safely (PTB v21/v22 compatibility)
    forward_chat = None
    if hasattr(msg, "forward_origin") and msg.forward_origin and hasattr(msg.forward_origin, "chat"):
        forward_chat = msg.forward_origin.chat
    elif getattr(msg, "forward_from_chat", None):
        forward_chat = msg.forward_from_chat

    # Parse text arguments e.g. /addchannel https://t.me/glitchcrafts or @glitchcrafts or glitchcrafts
    raw_input = (context.args[0] if context.args else msg.text or "").strip()
    if not forward_chat and raw_input and not raw_input.startswith("/addchannel"):
        clean_handle = raw_input.replace("https://t.me/", "").replace("http://t.me/", "").strip("@/ ")
        if clean_handle:
            try:
                forward_chat = await context.bot.get_chat(f"@{clean_handle}")
            except TelegramError as e:
                logger.warning(f"Could not resolve channel @{clean_handle}: {e}")
                await msg.reply_text(
                    f"❌ Could not find channel **@{clean_handle}**.\n"
                    "Please make sure the channel is public and `@TipaPayBot` is added as a member or admin.",
                    parse_mode="Markdown",
                )
                return

    if forward_chat and getattr(forward_chat, "type", None) == "channel":
        chat_id = str(forward_chat.id)
        chat_title = forward_chat.title or forward_chat.username or "Channel"

        async with AsyncSessionLocal() as session:
            stmt = select(Creator).where(Creator.telegram_id == user.id)
            res = await session.execute(stmt)
            creator = res.scalar_one_or_none()

            if not creator:
                # Auto-register user as Creator if not yet registered
                user_name = user.first_name or "Creator"
                if user.last_name:
                    user_name += f" {user.last_name}"
                creator = Creator(
                    telegram_id=user.id,
                    telegram_username=user.username,
                    display_name=user_name,
                    bank_code=869,
                    payment_method="telebirr",
                    account_number="Pending",
                    account_name=user_name,
                    channel_id=chat_id,
                )
                session.add(creator)
            else:
                creator.channel_id = chat_id
            await session.commit()

        bot_username = context.bot.username or settings.bot_username
        add_admin_url = f"https://t.me/{bot_username}?startchannel=true&admin=edit_messages+post_messages"
        admin_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add @TipaPayBot as Channel Admin", url=add_admin_url)]
        ])

        await msg.reply_text(
            f"✅ **Channel Linked Successfully!**\n\n"
            f"📢 Channel: **{chat_title}** (`{chat_id}`)\n"
            f"👤 Creator: **{creator.display_name}**\n\n"
            f"👇 Tap the button below to add `@TipaPayBot` as an Admin to **{chat_title}** so it can attach tipping buttons to your posts automatically!",
            reply_markup=admin_keyboard,
            parse_mode="Markdown",
        )
        return

    await msg.reply_text(
        "📢 **Link Your Telegram Channel:**\n\n"
        "Option 1️⃣: Send `/addchannel @your_channel_name` or `/addchannel https://t.me/your_channel`\n"
        "Option 2️⃣: Forward **ANY message** from your channel into this chat.\n\n"
        "Once linked, all your new channel posts will automatically get a **`[ 🎁 Tip Creator in Birr ]`** button!",
        parse_mode="Markdown",
    )


async def auto_channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Automatically attach a tipping inline button when a creator posts in their channel."""
    channel_post = update.channel_post
    if not channel_post or not channel_post.chat:
        return

    channel_id_str = str(channel_post.chat.id)

    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.channel_id == channel_id_str)
        result = await session.execute(stmt)
        creator = result.scalar_one_or_none()

    if not creator:
        logger.warning(
            f"No creator linked to channel {channel_id_str}; skipping auto tip button."
        )
        return

    post_id = str(channel_post.message_id)
    bot_username = context.bot.username or settings.bot_username
    keyboard = get_channel_post_button(bot_username, str(creator.id), post_id)
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=channel_post.chat.id,
            message_id=channel_post.message_id,
            reply_markup=keyboard,
        )
        logger.info(f"Auto-attached tip button for creator {creator.display_name} on channel post {post_id}")
    except TelegramError as e:
        logger.error(f"Could not auto-attach tip button to channel post: {e}")

    # Attach the active goal's live progress bar to this post, if any.
    from app.goals import (
        bind_goal_to_post,
        get_active_goal,
        goal_progress_line,
        goal_raised_amount,
    )

    async with AsyncSessionLocal() as session:
        goal = await get_active_goal(session, creator.id)
        if not goal or goal.status != "active":
            return
        base_text = (channel_post.text or channel_post.caption or "").strip()
        if not base_text:
            return  # sticker/photo-only posts without caption can't carry the bar
        full_text = f"{base_text}\n\n{goal_progress_line(goal, await goal_raised_amount(session, goal))}"
        bound_caption = False
        try:
            try:
                await context.bot.edit_message_text(
                    chat_id=channel_post.chat.id,
                    message_id=channel_post.message_id,
                    text=full_text,
                    parse_mode="Markdown",
                )
            except TelegramError as te:
                if "caption" not in str(te).lower() and "text" not in str(te).lower():
                    raise
                await context.bot.edit_message_caption(
                    chat_id=channel_post.chat.id,
                    message_id=channel_post.message_id,
                    caption=full_text,
                    parse_mode="Markdown",
                )
                bound_caption = True
        except TelegramError as e:
            logger.warning("Could not attach goal bar to post %s: %s", post_id, e)
            return
        await bind_goal_to_post(
            session,
            goal,
            str(channel_post.chat.id),
            post_id,
            base_text,
            bound_caption,
        )

