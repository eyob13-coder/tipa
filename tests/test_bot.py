from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telegram import Chat, Message, Update

from app.bot import handlers
from app.bot.keyboards import (
    get_channel_post_button,
    get_payment_method_selection_keyboard,
    get_tip_amount_keyboard,
    get_tip_note_prompt_keyboard,
    get_transfer_keyboard,
)
from app.db.base import Base
from app.db.models import Creator
from app.payment_methods import PAYMENT_METHODS


def test_payment_method_selection_keyboard():
    kb = get_payment_method_selection_keyboard()
    rows = kb.inline_keyboard
    assert len(rows) == len(PAYMENT_METHODS) + 1
    texts = [row[0].text for row in rows]
    for code, method in PAYMENT_METHODS.items():
        emoji = "📱" if method.kind == "mobile" else "🏦"
        assert f"{emoji} {method.name}" in texts
        row = next((r for r in rows if r[0].callback_data == f"method_select:{code}"), None)
        assert row is not None, f"missing method_select button for {code}"
    assert rows[-1][0].text == "❌ Cancel Registration"


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


def test_channel_post_button():
    kb = get_channel_post_button("TipaPayBot", "creator-uuid-123")
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].url == "https://t.me/TipaPayBot?start=tip_creator-uuid-123"
    assert "Tip Creator in Birr" in kb.inline_keyboard[0][0].text


def test_channel_post_button_with_post_id():
    kb = get_channel_post_button("TipaPayBot", "creator-uuid-123", "42")
    assert kb.inline_keyboard[0][0].url == "https://t.me/TipaPayBot?start=tip_creator-uuid-123_post_42"


def test_transfer_keyboard_urls_are_valid():
    kb = get_transfer_keyboard("telebirr", "tip-1")
    urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
    assert len(urls) == 2
    for url in urls:
        assert url.startswith("https://")
    assert not any("telebirr.et" in url for url in urls)


def test_cbe_transfer_keyboard_urls_are_valid():
    kb = get_transfer_keyboard("cbe", "tip-1")
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
        channel_id="-1001896209701",
    )
    async with session_factory() as session:
        session.add(creator)
        await session.commit()
        await session.refresh(creator)

    monkeypatch.setattr(handlers, "AsyncSessionLocal", session_factory)

    chat = Chat(id=-1001896209701, type="channel", title="GlitchCraft", username="glitchcrafts")
    msg = Message(message_id=137, date=datetime.now(timezone.utc), chat=chat, text="hello channel")
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
        channel_id="-100999",
    )
    async with session_factory() as session:
        session.add(creator)
        await session.commit()

    monkeypatch.setattr(handlers, "AsyncSessionLocal", session_factory)

    chat = Chat(id=-100111, type="channel", title="Some Other Channel")
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=chat, text="hello")
    update = Update(update_id=1, channel_post=msg)

    bot = _RecordingBot()
    await handlers.auto_channel_post_handler(update, _FakeContext(bot))

    assert bot.calls == []

    await engine.dispose()
