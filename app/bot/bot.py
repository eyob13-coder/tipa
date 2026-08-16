import logging
from typing import Optional

from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    InlineQueryHandler,
    PicklePersistence,
    filters,
)
from telegram.request import HTTPXRequest

from app.config import settings
from app.bot.handlers import (
    start_command,
    help_command,
    register_start,
    payment_method_callback,
    account_number_received,
    account_name_received,
    channel_link_received,
    confirm_registration_callback,
    cancel_registration,
    tip_amount_callback,
    text_input_handler,
    mytips_command,
    channel_post_generator,
    inline_query_handler,
    auto_channel_post_handler,
    addchannel_command,
    receipt_photo_handler,
    METHOD_CHOICE,
    ACCOUNT_NUM,
    ACCOUNT_NAME,
    CHANNEL_LINK,
    CONFIRMATION,
)

logger = logging.getLogger(__name__)

bot_app: Optional[Application] = None


async def post_init_setup(application: Application) -> None:
    """Register bot commands menu with Telegram on startup."""
    commands = [
        BotCommand("start", "Start Tipa & view tipping link"),
        BotCommand("register", "Link your payment method (mobile money / bank)"),
        BotCommand("post", "Generate channel post & tip button"),
        BotCommand("mytips", "View total earnings & tips history"),
        BotCommand("help", "Command guide & instructions"),
        BotCommand("cancel", "Cancel current registration or action"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Registered Telegram Bot Command Menu successfully.")
    except Exception as e:
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

    # /register ConversationHandler (Telebirr / CBE)
    registration_conv = ConversationHandler(
        entry_points=[CommandHandler("register", register_start)],
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
    app.add_handler(CommandHandler("post", channel_post_generator))
    app.add_handler(CommandHandler("addchannel", addchannel_command))
    app.add_handler(MessageHandler(filters.FORWARDED, addchannel_command))

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
