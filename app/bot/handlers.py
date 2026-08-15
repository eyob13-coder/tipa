import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)
from sqlalchemy import select, func, desc

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models import Creator, Tip
from app.chapa.client import chapa_client
from app.bot.keyboards import (
    get_payment_method_selection_keyboard,
    get_bank_selection_keyboard,
    get_tip_amount_keyboard,
    get_tip_note_prompt_keyboard,
    get_telebirr_transfer_keyboard,
    get_cbe_transfer_keyboard,
    get_creator_approval_keyboard,
    get_payment_link_keyboard,
    get_confirm_registration_keyboard,
    get_channel_post_button,
)

logger = logging.getLogger(__name__)

# Conversation states for registration
METHOD_CHOICE, BANK_CHOICE, ACCOUNT_NUM, ACCOUNT_NAME, CONFIRMATION = range(5)


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
                if post_id:
                    context.user_data["active_post_id"] = post_id
                keyboard = get_tip_amount_keyboard(str(creator.id))
                method_name = "Telebirr" if creator.payment_method == "telebirr" else ("CBE" if creator.payment_method == "cbe" else "Chapa")
                post_text = f" for post **#{post_id}**" if post_id else ""
                await update.effective_message.reply_text(
                    f"🎁 **Tip {creator.display_name}**{post_text}\n"
                    f"Payment Method: **{method_name}**\n\n"
                    f"Choose an amount below to tip directly in Birr (ETB):",
                    reply_markup=keyboard,
                    parse_mode="Markdown",
                )
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
        deep_link = f"https://t.me/{bot_name}?start=tip_{existing_creator.id}"
        method_str = existing_creator.payment_method.upper()
        await update.effective_message.reply_text(
            f"👋 Welcome back, **{existing_creator.display_name}**!\n"
            f"Active Payment Method: **{method_str}** (`{existing_creator.account_number}`)\n\n"
            f"🔗 **Your Personal Channel Tip Link:**\n`{deep_link}`\n\n"
            f"📌 **Quick Actions:**\n"
            f"• `/post` — Generate channel post & 1-tap tip button\n"
            f"• `/mytips` — View your total earnings & supporter notes\n"
            f"• `/register` — Update your Telebirr or CBE details\n"
            f"• `/help` — Detailed command guide",
            parse_mode="Markdown",
        )
    else:
        await update.effective_message.reply_text(
            f"🎁 **Welcome {user_name} to Tipa (@{bot_name})!**\n"
            f"Telegram Tipping for Ethiopian Creators via Telebirr & CBE.\n\n"
            f"Tipa enables followers to tip channel creators directly in Ethiopian Birr (ETB). "
            f"Funds flow directly to your Telebirr phone number or CBE bank account — 100% direct and transparent!\n\n"
            f"🚀 **How to Get Started (Takes 1 Minute):**\n"
            f"1️⃣ Run `/register` to link your Telebirr or CBE account.\n"
            f"2️⃣ Get your custom tipping deep link (`t.me/{bot_name}?start=tip_<your_id>`).\n"
            f"3️⃣ Run `/post` or type `@{bot_name}` to attach a tipping button to your channel posts!\n\n"
            f"👇 **Tap `/register` below to get started!**",
            parse_mode="Markdown",
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detailed help guide explaining every bot command and feature."""
    if not update.effective_message:
        return

    bot_name = context.bot.username or settings.bot_username
    help_text = (
        f"📖 **Tipa Bot Command Guide & Help (@{bot_name})**\n\n"
        f"**Commands Overview:**\n\n"
        f"🚀 **/start** — Welcome screen & deep link handler. Tapping a creator's tip link starts the tipping flow.\n\n"
        f"🏦 **/register** — Register or update your receiving payment method (**Telebirr**, **CBE**, or **Chapa**). Takes less than 1 minute!\n\n"
        f"📢 **/post** — Generates a copy-paste post with a 1-tap `[ 🎁 Tip Creator in Birr ]` button for your channel.\n\n"
        f"📊 **/mytips** — Creator dashboard. Shows your total Birr earned, tip count, and recent tips with supporter messages.\n\n"
        f"💬 **Supporter Notes** — Tippers can leave an optional encouraging message/note with their tip.\n\n"
        f"⚡ **Inline Mode** — Type `@{bot_name}` while composing a post in any Telegram channel to attach a tip button instantly!\n\n"
        f"❌ **/cancel** — Cancel any active registration step or tipping session."
    )
    await update.effective_message.reply_text(help_text, parse_mode="Markdown")


async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start registration conversation flow: select payment method."""
    if not update.effective_message:
        return ConversationHandler.END

    keyboard = get_payment_method_selection_keyboard()
    await update.effective_message.reply_text(
        "💳 **Step 1/3: Choose your Receiving Payment Method**\n"
        "How would you like to receive tips from followers?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return METHOD_CHOICE


async def payment_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle payment method selection (telebirr, cbe, chapa) and back navigation."""
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
        method = data.split(":")[1]
        context.user_data["selected_method"] = method

        if method == "telebirr":
            context.user_data["selected_bank_code"] = 869
            context.user_data["selected_bank_name"] = "Telebirr"
            await query.edit_message_text(
                "📱 **Selected Method: Telebirr**\n\n"
                "🔢 **Step 2/3: Enter your Telebirr Phone Number**\n"
                "Please type and send your Telebirr registered phone number (e.g., `0911223344`):",
                parse_mode="Markdown",
            )
            return ACCOUNT_NUM

        elif method == "cbe":
            context.user_data["selected_bank_code"] = 861
            context.user_data["selected_bank_name"] = "Commercial Bank of Ethiopia (CBE)"
            await query.edit_message_text(
                "🏦 **Selected Method: CBE (Commercial Bank of Ethiopia)**\n\n"
                "🔢 **Step 2/3: Enter your CBE Account Number**\n"
                "Please type and send your 13-digit CBE account number (e.g., `1000123456789`):",
                parse_mode="Markdown",
            )
            return ACCOUNT_NUM

        elif method == "chapa":
            banks = await chapa_client.list_banks()
            keyboard = get_bank_selection_keyboard(banks, page=0)
            context.user_data["reg_banks"] = banks
            await query.edit_message_text(
                "🏦 **Selected Method: Chapa Subaccount**\n\n"
                "Select your bank for automated split payments:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            return BANK_CHOICE

    return METHOD_CHOICE


async def bank_pagination_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle bank selection pagination callback for Chapa method."""
    query = update.callback_query
    if not query:
        return BANK_CHOICE
    await query.answer()

    data = query.data or ""
    if data == "bank_noop":
        return BANK_CHOICE

    if data == "back_to_methods":
        keyboard = get_payment_method_selection_keyboard()
        await query.edit_message_text(
            "💳 **Step 1/3: Choose your Receiving Payment Method**\n"
            "How would you like to receive tips from followers?",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return METHOD_CHOICE

    if data.startswith("bank_page:"):
        page = int(data.split(":")[1])
        banks = context.user_data.get("reg_banks") or await chapa_client.list_banks()
        keyboard = get_bank_selection_keyboard(banks, page=page)
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return BANK_CHOICE

    if data.startswith("bank_select:"):
        parts = data.split(":", 2)
        bank_code = int(parts[1])
        bank_name = parts[2]
        context.user_data["selected_bank_code"] = bank_code
        context.user_data["selected_bank_name"] = bank_name

        await query.edit_message_text(
            f"✅ Selected Bank: **{bank_name}**\n\n"
            f"🔢 **Step 2/3: Enter your Account Number**\n"
            f"Please type and send your bank account number:",
            parse_mode="Markdown",
        )
        return ACCOUNT_NUM

    return BANK_CHOICE


async def account_number_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive bank/telebirr account number and prompt for holder name."""
    if not update.effective_message or not update.effective_message.text:
        return ACCOUNT_NUM

    account_num = update.effective_message.text.strip()
    if len(account_num) < 5:
        await update.effective_message.reply_text("⚠️ Please enter a valid account or phone number.")
        return ACCOUNT_NUM

    context.user_data["account_number"] = account_num
    method = context.user_data.get("selected_method", "cbe")
    label = "Telebirr Account Holder Name" if method == "telebirr" else "Bank Account Holder Name"

    await update.effective_message.reply_text(
        f"👤 **Step 3/3: {label}**\n"
        f"Please send the exact account holder name registered:",
        parse_mode="Markdown",
    )
    return ACCOUNT_NAME


async def account_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive account holder name and present confirmation summary."""
    if not update.effective_message or not update.effective_message.text:
        return ACCOUNT_NAME

    account_name = update.effective_message.text.strip()
    context.user_data["account_name"] = account_name

    method = context.user_data.get("selected_method", "cbe").upper()
    bank_name = context.user_data.get("selected_bank_name", "Bank")
    account_num = context.user_data.get("account_number", "")

    keyboard = get_confirm_registration_keyboard()
    await update.effective_message.reply_text(
        f"📋 **Please Confirm Registration Details:**\n\n"
        f"💳 **Payment Method:** {method}\n"
        f"🏦 **Bank / Service:** {bank_name}\n"
        f"🔢 **Phone / Account Number:** `{account_num}`\n"
        f"👤 **Account Holder:** {account_name}\n\n"
        f"Click **Confirm** below to save your details and generate your tip link.",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return CONFIRMATION


async def confirm_registration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirm registration: save Creator record in DB."""
    query = update.callback_query
    if not query or not query.from_user:
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    if data == "reg_cancel":
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
        method = context.user_data.get("selected_method", "cbe")
        bank_code = context.user_data.get("selected_bank_code", 861)
        account_num = context.user_data.get("account_number")
        account_name = context.user_data.get("account_name")

        display_name = user.first_name or "Creator"
        if user.last_name:
            display_name += f" {user.last_name}"

        chapa_sub_id = f"manual_{user.id}"
        if method == "chapa":
            try:
                chapa_sub_id = await chapa_client.create_subaccount(
                    account_name=account_name,
                    bank_code=bank_code,
                    account_number=account_num,
                    split_value=settings.platform_fee_birr,
                )
            except Exception as ce:
                logger.warning(f"Chapa subaccount creation fallback to manual: {ce}")

        async with AsyncSessionLocal() as session:
            stmt = select(Creator).where(Creator.telegram_id == user.id)
            res = await session.execute(stmt)
            creator = res.scalar_one_or_none()

            if creator:
                creator.payment_method = method
                creator.bank_code = bank_code
                creator.account_number = account_num
                creator.account_name = account_name
                creator.chapa_subaccount_id = chapa_sub_id
                creator.display_name = display_name
                creator.telegram_username = user.username
            else:
                creator = Creator(
                    telegram_id=user.id,
                    telegram_username=user.username,
                    display_name=display_name,
                    bank_code=bank_code,
                    payment_method=method,
                    account_number=account_num,
                    account_name=account_name,
                    chapa_subaccount_id=chapa_sub_id,
                )
                session.add(creator)

            await session.commit()
            await session.refresh(creator)

        bot_name = context.bot.username or settings.bot_username
        deep_link = f"https://t.me/{bot_name}?start=tip_{creator.id}"

        await query.edit_message_text(
            f"🎉 **Registration Successful!**\n\n"
            f"Configured Payment Method: **{method.upper()}**\n\n"
            f"🔗 **Your Personal Tipping Deep Link:**\n`{deep_link}`\n\n"
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
        amount = float(parts[2])

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
        amount = float(parts[2])
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
            "Please send the transaction **Reference Number** or **SMS Receipt Code** from Telebirr/CBE (e.g., `TX987654`):",
            parse_mode="Markdown",
        )

    elif data.startswith("approve_tip:"):
        tip_id_str = data.split(":")[1]
        await handle_creator_approval(update, context, tip_id_str, is_approve=True)

    elif data.startswith("reject_tip:"):
        tip_id_str = data.split(":")[1]
        await handle_creator_approval(update, context, tip_id_str, is_approve=False)


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle custom tip amount, note entry, or payment reference code."""
    if not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text.strip()

    # Case A: Pending transaction reference verification code
    if "pending_verify_tip_id" in context.user_data:
        tip_id_str = context.user_data.pop("pending_verify_tip_id")
        await process_tip_verification_claim(update, context, tip_id_str, ref_code=text)
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
            amount = float(text)
            if amount < 5:
                await update.effective_message.reply_text("⚠️ Minimum tip amount is 5 Birr. Please try again:")
                context.user_data["pending_tip_creator_id"] = creator_id_str
                return
            if amount > 50000:
                await update.effective_message.reply_text("⚠️ Maximum single tip amount is 50,000 Birr. Please try again:")
                context.user_data["pending_tip_creator_id"] = creator_id_str
                return
        except ValueError:
            await update.effective_message.reply_text("⚠️ Invalid number. Please enter a numerical amount in Birr (e.g. 75):")
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
    note: Optional[str] = None,
    is_edit: bool = False,
) -> None:
    """Initialize tip record and present payment instructions (Telebirr / CBE / Chapa)."""
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
            chapa_tx_ref=tx_ref,
            status="pending",
            note=note,
            post_id=post_id,
        )
        session.add(tip_record)
        await session.commit()
        await session.refresh(tip_record)

        method = creator.payment_method
        note_display = f"\n💬 Note: *\"{note}\"*" if note else ""

        if method == "chapa" and not creator.chapa_subaccount_id.startswith("manual_"):
            try:
                bot_username = context.bot.username or settings.bot_username
                checkout_url = await chapa_client.initialize_transaction(
                    amount=amount,
                    creator_name=creator.display_name,
                    subaccount_id=creator.chapa_subaccount_id,
                    tx_ref=tx_ref,
                    tipper_telegram_id=tipper_id,
                    tipper_first_name=user.first_name if user else "Tipa",
                    tipper_last_name=user.last_name if user else "User",
                    return_url=f"https://t.me/{bot_username}",
                )

                keyboard = get_payment_link_keyboard(checkout_url, amount, creator.display_name)
                msg_text = (
                    f"🎁 **Tip for {creator.display_name}**\n\n"
                    f"Amount: **{amount:g} ETB**{note_display}\n"
                    f"Ref: `{tx_ref}`\n\n"
                    f"Click below to complete secure Chapa payment:"
                )

                if is_edit and update.callback_query:
                    await update.callback_query.edit_message_text(msg_text, reply_markup=keyboard, parse_mode="Markdown")
                elif update.effective_message:
                    await update.effective_message.reply_text(msg_text, reply_markup=keyboard, parse_mode="Markdown")
                return
            except Exception as e:
                logger.warning(f"Chapa init failed, falling back to direct transfer instructions: {e}")

        if method == "telebirr":
            keyboard = get_telebirr_transfer_keyboard(str(tip_record.id))
            instructions = (
                f"📱 **Telebirr Direct Tip Payment**\n\n"
                f"👤 Recipient: **{creator.account_name}** ({creator.display_name})\n"
                f"📱 Telebirr Phone: `{creator.account_number}` *(Tap number to copy)*\n"
                f"💰 Amount to Send: **{amount:g} ETB**{note_display}\n"
                f"🔖 Reference Code: `{tx_ref}`\n\n"
                f"**How to Pay:**\n"
                f"• **Option 1 (App):** Tap **Open Telebirr Web / App** below or open Telebirr app → Send **{amount:g} ETB** to `{creator.account_number}`.\n"
                f"• **Option 2 (USSD - No app needed):** Dial `*127#` on your phone → Send Money → Enter `{creator.account_number}`.\n\n"
                f"After sending, tap **I Have Sent the Payment** below to enter your SMS receipt code:"
            )
        else:  # CBE / CBE Birr
            keyboard = get_cbe_transfer_keyboard(str(tip_record.id))
            instructions = (
                f"🏦 **CBE / CBE Birr Tip Payment**\n\n"
                f"👤 Recipient: **{creator.account_name}** ({creator.display_name})\n"
                f"🏦 CBE Account Number: `{creator.account_number}` *(Tap number to copy)*\n"
                f"💰 Amount to Send: **{amount:g} ETB**{note_display}\n"
                f"🔖 Reference Code: `{tx_ref}`\n\n"
                f"**How to Pay:**\n"
                f"• **Option 1 (CBE Mobile App):** Open CBE Mobile app → Transfer **{amount:g} ETB** to `{creator.account_number}`.\n"
                f"• **Option 2 (CBE Birr USSD - No app needed):** Dial `*847#` on your phone → Transfer Money → Enter `{creator.account_number}`.\n\n"
                f"After sending, tap **I Have Sent the Payment** below to enter your transaction/SMS receipt code:"
            )

        if is_edit and update.callback_query:
            await update.callback_query.edit_message_text(instructions, reply_markup=keyboard, parse_mode="Markdown")
        elif update.effective_message:
            await update.effective_message.reply_text(instructions, reply_markup=keyboard, parse_mode="Markdown")


async def process_tip_verification_claim(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tip_id_str: str,
    ref_code: str,
) -> None:
    """Process claimed payment ref code by tipper and send 1-tap approval notification to Creator."""
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

        tip.status = "pending_verification"
        tip.chapa_ref_id = ref_code
        tip.claimed_at = datetime.now(timezone.utc)
        await session.commit()

    if update.effective_message:
        await update.effective_message.reply_text(
            f"✅ **Payment Claim Submitted!**\n\n"
            f"Ref/SMS Code: `{ref_code}`\n"
            f"Amount: **{float(tip.amount):g} ETB**\n\n"
            f"We have notified **{creator.display_name}**. Once they verify receipt, your tip will be confirmed! 🙏",
            parse_mode="Markdown",
        )

    tipper_name = tip.tipper_display_name or "A follower"
    note_str = f"\n💬 **Note:** *\"{tip.note}\"*\n" if tip.note else ""
    post_str = f"\n📌 **Channel Post:** #{tip.post_id}\n" if tip.post_id else ""
    method_name = creator.payment_method.upper()

    creator_approval_msg = (
        f"💸 **New Tip Received via {method_name}!**\n\n"
        f"**{tipper_name}** claims they sent **{float(tip.amount):g} ETB** to your `{creator.account_number}`.\n"
        f"Receipt / Ref Code: `{ref_code}`\n"
        f"{post_str}"
        f"{note_str}\n"
        f"Please check your {method_name} app and tap **Approve Tip** below to confirm:"
    )

    try:
        approval_kb = get_creator_approval_keyboard(str(tip.id))
        await context.bot.send_message(
            chat_id=creator.telegram_id,
            text=creator_approval_msg,
            reply_markup=approval_kb,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Failed to send creator approval notification: {e}")


async def handle_creator_approval(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tip_id_str: str,
    is_approve: bool,
) -> None:
    """Handle Creator's 1-tap Approve or Reject inline callback."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    try:
        tip_uuid = uuid.UUID(tip_id_str)
    except ValueError:
        await query.edit_message_text("❌ Invalid tip ID.")
        return

    async with AsyncSessionLocal() as session:
        stmt = select(Tip).where(Tip.id == tip_uuid)
        res = await session.execute(stmt)
        tip = res.scalar_one_or_none()

        if not tip:
            await query.edit_message_text("❌ Tip not found.")
            return

        c_stmt = select(Creator).where(Creator.id == tip.creator_id)
        c_res = await session.execute(c_stmt)
        creator = c_res.scalar_one_or_none()

        if is_approve:
            tip.status = "success"
            tip.verified_at = datetime.now(timezone.utc)
            await session.commit()

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
                except Exception as e:
                    logger.error(f"Failed to notify tipper: {e}")
        else:
            tip.status = "failed"
            await session.commit()
            await query.edit_message_text("❌ Tip rejected.")


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

    await update.effective_message.reply_text(
        "📢 **Channel Post & Tip Button Generator**\n\n"
        "Forward or copy the message below to your channel so followers can tip you with one tap!\n\n"
        "---",
        parse_mode="Markdown",
    )

    await update.effective_message.reply_text(
        "✨ **Enjoying the content?** Support this channel directly in Birr (ETB) via Tipa!",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


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
                    f"Please register your Telebirr/CBE details with @{bot_name} first using /register!"
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

    deep_link = f"https://t.me/{context.bot.username}?start=tip_{creator.id}"
    method_str = creator.payment_method.upper()
    text = (
        f"📊 **Creator Dashboard — {creator.display_name}**\n"
        f"Payment Method: **{method_str}** (`{creator.account_number}`)\n\n"
        f"💰 **Total Tips Earned:** `{float(total_amount):,.2f} ETB`\n"
        f"🎉 **Total Tips Received:** `{total_count}`\n\n"
        f"🔗 **Your Tip Link:**\n`{deep_link}`\n\n"
    )

    if recent_tips:
        text += "📜 **Recent Tips:**\n"
        for t in recent_tips:
            tipper = t.tipper_display_name or "Anonymous"
            note_str = f" (*\"{t.note}\"*)" if t.note else ""
            date_str = t.verified_at.strftime("%Y-%m-%d %H:%M") if t.verified_at else t.created_at.strftime("%Y-%m-%d %H:%M")
            text += f"• **{float(t.amount):g} ETB** from {tipper}{note_str} ({date_str})\n"
    else:
        text += "💡 *No successful tips yet. Share your tip link in your Telegram channel to start receiving tips!*"

    await update.effective_message.reply_text(text, parse_mode="Markdown")


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
            except Exception as e:
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
                    bank_code=861,
                    payment_method="telebirr",
                    account_number="Pending",
                    account_name=user_name,
                    chapa_subaccount_id=f"manual_{user.id}",
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
    except Exception as e:
        logger.error(f"Could not auto-attach tip button to channel post: {e}")
