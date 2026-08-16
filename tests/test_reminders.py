import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

import app.bot.reminders as reminders
from app.db.base import Base
from app.db.models import Creator, Tip


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def _persist(factory, obj):
    async with factory() as session:
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
    return obj


class _RecordingBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return None


def _creator(telegram_id=111):
    return Creator(
        telegram_id=telegram_id,
        telegram_username="creator1",
        display_name="Creator One",
        bank_code=869,
        payment_method="telebirr",
        account_number="0911223344",
        account_name="Creator One",
        channel_id="-100111",
    )


@pytest.mark.asyncio
async def test_young_claim_is_untouched(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr(reminders, "AsyncSessionLocal", factory)

    creator = await _persist(factory, _creator())
    now = datetime.now(timezone.utc)
    tip = Tip(
        creator_id=creator.id,
        tipper_telegram_id=222,
        tipper_display_name="Tipper",
        amount=10,
        platform_fee=0,
        tx_ref="tipa_young",
        status="pending_verification",
        claimed_at=now - timedelta(hours=2),
    )
    await _persist(factory, tip)

    bot = _RecordingBot()
    result = await reminders.remind_and_expire_pending_tips(
        bot=bot, now=now, reminder_hours=24, expiry_hours=72
    )

    assert result == {"reminded": [], "expired": []}
    assert bot.messages == []

    async with factory() as session:
        t = await session.get(Tip, tip.id)
        assert t.status == "pending_verification"
        assert t.last_reminder_at is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_gets_reminder_once(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr(reminders, "AsyncSessionLocal", factory)

    creator = await _persist(factory, _creator())
    now = datetime.now(timezone.utc)
    tip = Tip(
        creator_id=creator.id,
        tipper_telegram_id=222,
        tipper_display_name="Tipper",
        amount=10,
        platform_fee=0,
        tx_ref="tipa_remind",
        ref_id="REF123",
        status="pending_verification",
        claimed_at=now - timedelta(hours=30),
    )
    await _persist(factory, tip)

    bot = _RecordingBot()
    result = await reminders.remind_and_expire_pending_tips(
        bot=bot, now=now, reminder_hours=24, expiry_hours=72
    )

    assert result["reminded"] == [str(tip.id)]
    assert result["expired"] == []
    assert len(bot.messages) == 1
    msg = bot.messages[0]
    assert msg["chat_id"] == creator.telegram_id
    assert "Reminder" in msg["text"]
    assert msg["reply_markup"] is not None

    # A second pass right after must not resend (last_reminder_at is recent).
    bot.messages.clear()
    result2 = await reminders.remind_and_expire_pending_tips(
        bot=bot, now=now + timedelta(hours=1), reminder_hours=24, expiry_hours=72
    )
    assert result2 == {"reminded": [], "expired": []}
    assert bot.messages == []

    async with factory() as session:
        t = await session.get(Tip, tip.id)
        assert t.status == "pending_verification"
        assert t.last_reminder_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_claim_fails_and_notifies_both(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr(reminders, "AsyncSessionLocal", factory)

    creator = await _persist(factory, _creator())
    now = datetime.now(timezone.utc)
    tip = Tip(
        creator_id=creator.id,
        tipper_telegram_id=222,
        tipper_display_name="Tipper",
        amount=20,
        platform_fee=0,
        tx_ref="tipa_expire",
        ref_id="REF456",
        status="pending_verification",
        claimed_at=now - timedelta(hours=80),
    )
    await _persist(factory, tip)

    bot = _RecordingBot()
    result = await reminders.remind_and_expire_pending_tips(
        bot=bot, now=now, reminder_hours=24, expiry_hours=72
    )

    assert result["expired"] == [str(tip.id)]
    assert result["reminded"] == []
    # Creator + tipper both notified.
    chat_ids = sorted(m["chat_id"] for m in bot.messages)
    assert chat_ids == [creator.telegram_id, tip.tipper_telegram_id]

    async with factory() as session:
        t = await session.get(Tip, tip.id)
        assert t.status == "failed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_loop_cancels_cleanly(monkeypatch):
    called = []

    async def _stub():
        called.append(True)
        await asyncio.sleep(3600)

    monkeypatch.setattr(reminders, "remind_and_expire_pending_tips", _stub)

    task = asyncio.create_task(reminders.run_tip_reminder_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert called
