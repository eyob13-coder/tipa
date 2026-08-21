from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.payment_methods import PAYMENT_METHODS, PaymentMethod, get_method


def get_payment_method_selection_keyboard() -> InlineKeyboardMarkup:
    """Select preferred receiving payment method for creators (mobile wallets & banks)."""
    keyboard = []
    for method in PAYMENT_METHODS.values():
        emoji = "📱" if method.kind == "mobile" else "🏦"
        keyboard.append(
            [InlineKeyboardButton(f"{emoji} {method.name}", callback_data=f"method_select:{method.code}")]
        )
    keyboard.append([InlineKeyboardButton("❌ Cancel Registration", callback_data="reg_cancel")])
    return InlineKeyboardMarkup(keyboard)


def get_tip_amount_keyboard(creator_id: str) -> InlineKeyboardMarkup:
    """Build preset tip amount keyboard + custom amount button."""
    keyboard = [
        [
            InlineKeyboardButton("10 Birr", callback_data=f"tip_amt:{creator_id}:10"),
            InlineKeyboardButton("25 Birr", callback_data=f"tip_amt:{creator_id}:25"),
        ],
        [
            InlineKeyboardButton("50 Birr", callback_data=f"tip_amt:{creator_id}:50"),
            InlineKeyboardButton("100 Birr", callback_data=f"tip_amt:{creator_id}:100"),
        ],
        [
            InlineKeyboardButton("✏️ Custom Amount", callback_data=f"tip_custom:{creator_id}"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="tip_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_tip_note_prompt_keyboard(creator_id: str, amount: float) -> InlineKeyboardMarkup:
    """Prompt tipper to add optional note or skip to payment."""
    keyboard = [
        [
            InlineKeyboardButton("💬 Add Note for Creator", callback_data=f"tip_add_note:{creator_id}:{amount}"),
        ],
        [
            InlineKeyboardButton("💳 Proceed to Payment", callback_data=f"tip_pay_now:{creator_id}:{amount}"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to Amounts", callback_data=f"back_to_amounts:{creator_id}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_transfer_keyboard(method_code: str, tip_id: str) -> InlineKeyboardMarkup:
    """Buttons for a payment method transfer: app links & payment confirmation."""
    method: PaymentMethod = get_method(method_code) or get_method("telebirr")
    keyboard = []
    if method.android_url:
        keyboard.append([InlineKeyboardButton("📱 Open Android App", url=method.android_url)])
    if method.ios_url:
        keyboard.append([InlineKeyboardButton("📱 Open iPhone App", url=method.ios_url)])
    keyboard.append([InlineKeyboardButton("✅ I Have Sent the Payment", callback_data=f"tip_sent:{tip_id}")])
    keyboard.append([InlineKeyboardButton("❌ Cancel Tip", callback_data="tip_cancel")])
    return InlineKeyboardMarkup(keyboard)


def get_creator_approval_keyboard(tip_id: str) -> InlineKeyboardMarkup:
    """Approval buttons sent to Creator DM when tipper claims payment."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve Tip", callback_data=f"approve_tip:{tip_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_tip:{tip_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_subscription_transfer_keyboard() -> InlineKeyboardMarkup:
    """Buttons for the Tipa Pro payment step (pay Tipa directly, then claim)."""
    method: PaymentMethod = get_method(settings.tipa_receiving_method) or get_method("telebirr")
    keyboard = []
    if method.android_url:
        keyboard.append([InlineKeyboardButton("📱 Open Android App", url=method.android_url)])
    if method.ios_url:
        keyboard.append([InlineKeyboardButton("📱 Open iPhone App", url=method.ios_url)])
    keyboard.append([InlineKeyboardButton("✅ I Have Sent the Payment", callback_data="pro_sent")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="pro_cancel")])
    return InlineKeyboardMarkup(keyboard)


def get_admin_subscription_keyboard(subscription_id: str) -> InlineKeyboardMarkup:
    """Manual approval buttons sent to admins when a Pro payment can't auto-verify."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve Pro", callback_data=f"approve_sub:{subscription_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_sub:{subscription_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_av_transfer_keyboard() -> InlineKeyboardMarkup:
    """Buttons for the account-ownership micro-deposit step."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I Have Sent the Deposit", callback_data="av_sent")],
        [InlineKeyboardButton("❌ Cancel", callback_data="av_cancel")],
    ])


def get_language_keyboard() -> InlineKeyboardMarkup:
    """Language picker shown at /start (Amharic is the biggest adoption lever)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton("🇪🇹 አማርኛ", callback_data="lang_am"),
        ]
    ])


def get_admin_account_verification_keyboard(creator_id: str) -> InlineKeyboardMarkup:
    """Manual approval buttons for an account-ownership deposit that couldn't auto-verify."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve Ownership", callback_data=f"approve_av:{creator_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_av:{creator_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_confirm_registration_keyboard() -> InlineKeyboardMarkup:
    """Confirmation buttons for creator registration."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm & Register", callback_data="reg_confirm"),
        ],
        [
            InlineKeyboardButton("⬅️ Change Details", callback_data="back_to_methods"),
            InlineKeyboardButton("❌ Cancel", callback_data="reg_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_channel_post_button(bot_username: str, creator_id: str, post_id: str = "") -> InlineKeyboardMarkup:
    """Inline button attached to channel posts."""
    url = f"https://t.me/{bot_username}?start=tip_{creator_id}"
    if post_id:
        url += f"_post_{post_id}"
    keyboard = [
        [
            InlineKeyboardButton("🎁 Tip Creator in Birr (ETB)", url=url)
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
