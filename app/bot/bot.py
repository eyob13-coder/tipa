import logging

from telegram import BotCommand
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    InlineQueryHandler,
    MessageHandler,
    PicklePersistence,
    filters,
)
from telegram.request import HTTPXRequest

from app.bot.handlers import (
    ACCOUNT_NAME,
    ACCOUNT_NUM,
    CHANNEL_LINK,
    CONFIRMATION,
    METHOD_CHOICE,
    account_name_received,
    account_number_received,
    addchannel_command,
    auto_channel_post_handler,
    cancel_registration,
    channel_link_received,
    channel_post_generator,
    confirm_registration_callback,
    dispute_command,
    export_command,
    freeze_command,
    help_command,
    inline_query_handler,
    mytips_command,
    payment_method_callback,
    payout_start,
    pro_command,
    receipt_photo_handler,
    register_start,
    resolvedispute_command,
    start_command,
    subscription_callback,
    text_input_handler,
    tip_amount_callback,
    unfreeze_command,
    verifyaccount_command,
)
from app.config import settings

logger = logging.getLogger(__name__)

bot_app: Application | None = None


async def post_init_setup(application: Application) -> None:
    """Register bot commands menu with Telegram on startup."""
    commands = [
        BotCommand("start", "Start Tipa & view tipping link"),
        BotCommand("register", "Link your payment method (mobile money / bank)"),
        BotCommand("post", "Generate channel post & tip button"),
        BotCommand("mytips", "View total earnings & tips history"),
        BotCommand("verifyaccount", "Prove your payout account is yours"),
        BotCommand("pro", "Upgrade to Tipa Pro (CSV export & more)"),
        BotCommand("export", "Download your tips as CSV or PDF"),
        BotCommand("payout", "Switch your payout bank or wallet"),
        BotCommand("help", "Command guide & instructions"),
        BotCommand("cancel", "Cancel current registration or action"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Registered Telegram Bot Command Menu successfully.")
    except TelegramError as e:
        logger.warning(f"Could not register Bot Command Menu: {e}")


def build_telegram_application() -> Application:
    """Build and configure python-telegram-bot Application instance with persistence."""
    persistence = PicklePersistence(filepath="bot_persistence.pickle")
    request_config = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=30.0)
    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .request(request_config)
        .persistence(persistence)
        .post_init(post_init_setup)
        .build()
    )

    # /register + /payout ConversationHandler (any of the 9 banks / mobile wallets)
    registration_conv = ConversationHandler(
        entry_points=[
            CommandHandler("register", register_start),
            CommandHandler("payout", payout_start),
        ],
        states={
            METHOD_CHOICE: [
                CallbackQueryHandler(payment_method_callback, pattern=r"^(method_select:|back_to_methods$)"),
            ],
            ACCOUNT_NUM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, account_number_received),
            ],
            ACCOUNT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, account_name_received),
            ],
            CHANNEL_LINK: [
                CallbackQueryHandler(channel_link_received, pattern=r"^skip_channel_link$"),
                MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.FORWARDED, channel_link_received),
            ],
            CONFIRMATION: [
                CallbackQueryHandler(confirm_registration_callback, pattern=r"^(reg_|back_to_methods$)"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_registration)],
        name="registration_conv",
        persistent=True,
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(registration_conv)
    app.add_handler(CommandHandler("mytips", mytips_command))
    app.add_handler(CommandHandler("verifyaccount", verifyaccount_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("pro", pro_command))
    app.add_handler(CommandHandler("post", channel_post_generator))
    app.add_handler(CommandHandler("addchannel", addchannel_command))
    app.add_handler(MessageHandler(filters.FORWARDED, addchannel_command))

    # Admin-only abuse-control & dispute tooling (handlers self-check admin ids)
    app.add_handler(CommandHandler("freeze", freeze_command))
    app.add_handler(CommandHandler("unfreeze", unfreeze_command))
    app.add_handler(CommandHandler("dispute", dispute_command))
    app.add_handler(CommandHandler("resolvedispute", resolvedispute_command))

    # Tipa Pro / account-verification payment claims, language picker,
    # cancels, and admin approvals
    app.add_handler(
        CallbackQueryHandler(
            subscription_callback,
            pattern=r"^(pro_|av_|lang_|approve_sub:|reject_sub:|approve_av:|reject_av:)",
        )
    )

    # Register Channel Post Auto-Attach Handler
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, auto_channel_post_handler))

    # Register Inline Query Handler
    app.add_handler(InlineQueryHandler(inline_query_handler))

    # Tip preset / custom / note / approval / direct transfer / back / cancel callbacks
    app.add_handler(
        CallbackQueryHandler(
            tip_amount_callback,
            pattern=r"^(tip_|approve_tip:|reject_tip:|back_to_amounts:)",
        )
    )

    # Handle photo receipt screenshots
    app.add_handler(MessageHandler(filters.PHOTO, receipt_photo_handler))

    # General text message handler (custom tip amounts / custom notes / payment ref codes)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_input_handler,
        )
    )

    return app


def get_telegram_application() -> Application:
    global bot_app
    if bot_app is None:
        bot_app = build_telegram_application()
    return bot_app
