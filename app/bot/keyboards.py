from typing import List, Dict, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_payment_method_selection_keyboard() -> InlineKeyboardMarkup:
    """Select preferred receiving payment method for creators (Telebirr or CBE)."""
    keyboard = [
        [
            InlineKeyboardButton("📱 Telebirr (Phone Number)", callback_data="method_select:telebirr"),
        ],
        [
            InlineKeyboardButton("🏦 CBE / Commercial Bank of Ethiopia", callback_data="method_select:cbe"),
        ],
        [
            InlineKeyboardButton("❌ Cancel Registration", callback_data="reg_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_bank_selection_keyboard(banks: List[Dict[str, Any]], page: int = 0, page_size: int = 5) -> InlineKeyboardMarkup:
    """Build a paginated inline keyboard for choosing a bank."""
    total_banks = len(banks)
    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_banks)
    current_banks = banks[start_idx:end_idx]

    keyboard = []
    for bank in current_banks:
        code = str(bank.get("code") or bank.get("id"))
        name = bank.get("name", f"Bank {code}")
        keyboard.append([InlineKeyboardButton(name, callback_data=f"bank_select:{code}:{name}")])

    # Navigation row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"bank_page:{page - 1}"))
    page_count = (total_banks + page_size - 1) // page_size
    if page_count > 1:
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{page_count}", callback_data="bank_noop"))
    if end_idx < total_banks:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"bank_page:{page + 1}"))

    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("⬅️ Back to Payment Methods", callback_data="back_to_methods")])
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


def get_telebirr_transfer_keyboard(tip_id: str) -> InlineKeyboardMarkup:
    """Buttons for Telebirr payment with app store links & payment confirmation."""
    keyboard = [
        [
            InlineKeyboardButton(
                "📱 Open Telebirr App (Android)",
                url="https://play.google.com/store/apps/details?id=cn.tydic.ethiopay",
            ),
        ],
        [
            InlineKeyboardButton(
                "📱 Open Telebirr App (iPhone)",
                url="https://apps.apple.com/us/app/telebirr/id1553601084",
            ),
        ],
        [
            InlineKeyboardButton("✅ I Have Sent the Payment", callback_data=f"tip_sent:{tip_id}"),
        ],
        [
            InlineKeyboardButton("❌ Cancel Tip", callback_data="tip_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_cbe_transfer_keyboard(tip_id: str) -> InlineKeyboardMarkup:
    """Buttons for CBE payment with app store links & payment confirmation."""
    keyboard = [
        [
            InlineKeyboardButton(
                "📱 Open CBE Birr App (Android)",
                url="https://play.google.com/store/apps/details?id=prod.cbe.birr",
            ),
        ],
        [
            InlineKeyboardButton(
                "📱 Open CBE Birr App (iPhone)",
                url="https://apps.apple.com/us/app/cbebirr-plus/id1600841787",
            ),
        ],
        [
            InlineKeyboardButton("✅ I Have Sent the Payment", callback_data=f"tip_sent:{tip_id}"),
        ],
        [
            InlineKeyboardButton("❌ Cancel Tip", callback_data="tip_cancel"),
        ],
    ]
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


def get_payment_link_keyboard(checkout_url: str, amount: float, creator_name: str) -> InlineKeyboardMarkup:
    """Build checkout button for Chapa payment."""
    keyboard = [
        [
            InlineKeyboardButton(f"💳 Pay {amount:g} ETB Tip for {creator_name}", url=checkout_url)
        ],
        [
            InlineKeyboardButton("❌ Cancel Tip", callback_data="tip_cancel"),
        ],
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
