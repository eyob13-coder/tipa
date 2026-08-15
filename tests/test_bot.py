from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from telegram import Chat, Message, Update

import app.bot.handlers as handlers
from app.bot.keyboards import (
    get_payment_method_selection_keyboard,
    get_bank_selection_keyboard,
    get_tip_amount_keyboard,
    get_tip_note_prompt_keyboard,
    get_payment_link_keyboard,
    get_channel_post_button,
    get_telebirr_transfer_keyboard,
    get_cbe_transfer_keyboard,
)
from app.db.base import Base
from app.db.models import Creator


def test_payment_method_selection_keyboard():
    kb = get_payment_method_selection_keyboard()
    assert len(kb.inline_keyboard) == 3
    assert kb.inline_keyboard[0][0].text == "📱 Telebirr (Phone Number)"
    assert kb.inline_keyboard[1][0].text == "🏦 CBE / Commercial Bank of Ethiopia"
    assert kb.inline_keyboard[2][0].text == "❌ Cancel Registration"


def test_bank_selection_keyboard_pagination():
    banks = [
        {"id": 856, "name": "Abay Bank", "code": "856"},
        {"id": 857, "name": "Addis International Bank", "code": "857"},
        {"id": 858, "name": "Awash Bank", "code": "858"},
        {"id": 859, "name": "Bank of Abyssinia", "code": "859"},
        {"id": 860, "name": "Berhan Bank", "code": "860"},
        {"id": 861, "name": "CBE", "code": "861"},
        {"id": 862, "name": "Dashen Bank", "code": "862"},
    ]

    kb_page0 = get_bank_selection_keyboard(banks, page=0, page_size=5)
    # 5 bank rows + 1 pagination row + 1 back row
    assert len(kb_page0.inline_keyboard) == 7
    assert kb_page0.inline_keyboard[0][0].text == "Abay Bank"

    kb_page1 = get_bank_selection_keyboard(banks, page=1, page_size=5)
    # 2 bank rows + 1 pagination row + 1 back row
    assert len(kb_page1.inline_keyboard) == 4
    assert kb_page1.inline_keyboard[0][0].text == "CBE"


def test_tip_amount_keyboard():
    kb = get_tip_amount_keyboard("mock-creator-uuid")
    # 2 preset rows + 1 custom row + 1 cancel row
    assert len(kb.inline_keyboard) == 4
    assert kb.inline_keyboard[0][0].text == "10 Birr"
    assert kb.inline_keyboard[0][1].text == "25 Birr"
    assert kb.inline_keyboard[1][0].text == "50 Birr"
    assert kb.inline_keyboard[1][1].text == "100 Birr"
    assert kb.inline_keyboard[2][0].text == "✏️ Custom Amount"
    assert kb.inline_keyboard[3][0].text == "❌ Cancel"


def test_tip_note_prompt_keyboard():
    kb = get_tip_note_prompt_keyboard("mock-creator-uuid", 50.0)
    # note row + pay row + back row
    assert len(kb.inline_keyboard) == 3
    assert "Add Note" in kb.inline_keyboard[0][0].text
    assert "Proceed to Payment" in kb.inline_keyboard[1][0].text
    assert "Back to Amounts" in kb.inline_keyboard[2][0].text


def test_payment_link_keyboard():
    url = "https://checkout.chapa.co/test"
    kb = get_payment_link_keyboard(url, 50.0, "Amanuel")
    # pay link row + cancel row
    assert len(kb.inline_keyboard) == 2
    assert kb.inline_keyboard[0][0].url == url
    assert "50 ETB" in kb.inline_keyboard[0][0].text
    assert kb.inline_keyboard[1][0].text == "❌ Cancel Tip"


def test_channel_post_button():
    kb = get_channel_post_button("TipaPayBot", "creator-uuid-123")
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].url == "https://t.me/TipaPayBot?start=tip_creator-uuid-123"
    assert "Tip Creator in Birr" in kb.inline_keyboard[0][0].text


def test_channel_post_button_with_post_id():
    kb = get_channel_post_button("TipaPayBot", "creator-uuid-123", "42")
    assert kb.inline_keyboard[0][0].url == "https://t.me/TipaPayBot?start=tip_creator-uuid-123_post_42"


def test_telebirr_transfer_keyboard_urls_are_valid():
    kb = get_telebirr_transfer_keyboard("tip-1")
    urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
    assert len(urls) == 2
    for url in urls:
        assert url.startswith("https://")
    assert not any("telebirr.et" in url for url in urls)


def test_cbe_transfer_keyboard_urls_are_valid():
    kb = get_cbe_transfer_keyboard("tip-1")
    urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
    assert len(urls) == 2
    for url in urls:
        assert url.startswith("https://")


class _RecordingBot:
    username = "TipaPayBot"

    def __init__(self):
        self.calls = []

    async def edit_message_reply_markup(self, **kwargs):
        self.calls.append(kwargs)
        return True


class _FakeContext:
    def __init__(self, bot):
        self.bot = bot


@pytest.mark.asyncio
async def test_auto_channel_post_handler_attaches_correct_button(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    creator = Creator(
        telegram_id=1053005565,
        telegram_username="lehavatest",
        display_name="Lehava",
        bank_code=869,
        payment_method="telebirr",
        account_number="0911223344",
        account_name="Lehava",
        chapa_subaccount_id="manual_1053005565",
        channel_id="-1001896209701",
    )
    async with session_factory() as session:
        session.add(creator)
        await session.commit()
        await session.refresh(creator)

    monkeypatch.setattr(handlers, "AsyncSessionLocal", session_factory)

    chat = Chat(id=-1001896209701, type="channel", title="GlitchCraft", username="glitchcrafts")
    msg = Message(message_id=137, date=datetime.now(), chat=chat, text="hello channel")
    update = Update(update_id=1, channel_post=msg)

    bot = _RecordingBot()
    await handlers.auto_channel_post_handler(update, _FakeContext(bot))

    assert len(bot.calls) == 1
    assert bot.calls[0]["chat_id"] == -1001896209701
    assert bot.calls[0]["message_id"] == 137
    url = bot.calls[0]["reply_markup"].inline_keyboard[0][0].url
    assert url == f"https://t.me/TipaPayBot?start=tip_{creator.id}_post_137"

    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_channel_post_handler_skips_unlinked_channel(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    creator = Creator(
        telegram_id=999,
        telegram_username="nobody",
        display_name="Nobody",
        bank_code=869,
        payment_method="telebirr",
        account_number="0911223344",
        account_name="Nobody",
        chapa_subaccount_id="manual_999",
        channel_id="-100999",
    )
    async with session_factory() as session:
        session.add(creator)
        await session.commit()

    monkeypatch.setattr(handlers, "AsyncSessionLocal", session_factory)

    chat = Chat(id=-100111, type="channel", title="Some Other Channel")
    msg = Message(message_id=1, date=datetime.now(), chat=chat, text="hello")
    update = Update(update_id=1, channel_post=msg)

    bot = _RecordingBot()
    await handlers.auto_channel_post_handler(update, _FakeContext(bot))

    assert bot.calls == []

    await engine.dispose()
