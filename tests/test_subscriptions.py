import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot import handlers
from app.config import settings
from app.db.base import Base
from app.db.models import Creator, Subscription, VerificationLog
from app.subscriptions import (
    SUB_STATUS_ACTIVE,
    SUB_STATUS_EXPIRED,
    SUB_STATUS_PENDING_VERIFICATION,
    SUB_STATUS_REJECTED,
    activate_subscription,
    auto_verify_subscription,
    expire_due_subscriptions,
    get_active_subscription,
    is_pro,
)
from app.verify.base import VerifyResult


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


def _creator(telegram_id=111):
    return Creator(
        telegram_id=telegram_id,
        telegram_username="creator1",
        display_name="Creator One",
        bank_code=869,
        payment_method="telebirr",
        account_number="0911223344",
        account_name="Creator One",
    )


def _subscription(creator_id, amount=199.0, status="pending"):
    return Subscription(
        creator_id=creator_id,
        plan="pro",
        status=status,
        amount=amount,
        tx_ref=f"pro_test_{uuid.uuid4().hex[:12]}",
    )


class _StubRegistry:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = []

    @property
    def enabled_providers(self):
        return [object()] if (self._result or self._error) else []

    async def verify(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._result


class _StubUser:
    def __init__(self, user_id):
        self.id = user_id


class _StubQuery:
    def __init__(self, user_id, data):
        self.from_user = _StubUser(user_id)
        self.data = data
        self.answers = []
        self.edits = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)


class _StubUpdate:
    def __init__(self, query):
        self.callback_query = query


class _RecordingBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class _FakeContext:
    def __init__(self, bot):
        self.bot = bot


@pytest.mark.asyncio
async def test_is_pro_false_without_subscription():
    engine, factory = await _make_factory()
    creator = await _persist(factory, _creator())

    async with factory() as session:
        assert await is_pro(session, creator.id) is False
        assert await get_active_subscription(session, creator.id) is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_activate_subscription_sets_window():
    engine, factory = await _make_factory()
    creator = await _persist(factory, _creator())
    sub = await _persist(factory, _subscription(creator.id))
    now = datetime.now(timezone.utc)

    async with factory() as session:
        loaded = await session.get(Subscription, sub.id)
        await activate_subscription(session, loaded, method="check_et", now=now)

        assert await is_pro(session, creator.id) is True
        active = await get_active_subscription(session, creator.id)
        assert active.verification_method == "check_et"
        expected = now + timedelta(days=settings.pro_duration_days)
        assert abs((active.expires_at - expected).total_seconds()) < 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_renewal_stacks_remaining_time():
    engine, factory = await _make_factory()
    creator = await _persist(factory, _creator())
    now = datetime.now(timezone.utc)

    first = await _persist(factory, _subscription(creator.id, 199.0))
    async with factory() as session:
        loaded = await session.get(Subscription, first.id)
        await activate_subscription(session, loaded, method="admin_approval", now=now)

    # Second purchase while the first is still active (10 days remaining).
    second = await _persist(factory, _subscription(creator.id, 199.0))
    later = now + timedelta(days=10)
    async with factory() as session:
        loaded = await session.get(Subscription, second.id)
        await activate_subscription(session, loaded, method="admin_approval", now=later)

        active = await get_active_subscription(session, creator.id, now=later)
        assert active.id == second.id
        # Each payment appends its full duration to the entitlement end:
        # first sub ends at now+30d, renewal adds another 30d -> now+60d.
        assert abs((active.expires_at - (now + timedelta(days=60))).total_seconds()) < 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_verify_subscription_success(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr("app.subscriptions.AsyncSessionLocal", factory)

    creator = await _persist(factory, _creator())
    sub = await _persist(factory, _subscription(creator.id, settings.pro_price_birr))

    async with factory() as session:
        loaded = await session.get(Subscription, sub.id)
        loaded.status = SUB_STATUS_PENDING_VERIFICATION
        loaded.ref_id = "TLB12345678"
        await session.commit()

    registry = _StubRegistry(
        result=VerifyResult(request_success=True, verified=True, status="success", amount=settings.pro_price_birr, provider="check_et")
    )
    monkeypatch.setattr("app.subscriptions.verify_registry", registry)

    async with factory() as session:
        loaded = await session.get(Subscription, sub.id)
        result = await auto_verify_subscription(session, loaded, "TLB12345678")

    assert result is not None and result.verified
    async with factory() as session:
        refreshed = await session.get(Subscription, sub.id)
        assert refreshed.status == SUB_STATUS_ACTIVE
        assert refreshed.verification_method == "check_et"

        log_stmt = select(VerificationLog).where(VerificationLog.subscription_id == sub.id)
        logs = (await session.execute(log_stmt)).scalars().all()
        assert len(logs) == 1
        assert logs[0].verified is True
        assert logs[0].tip_id is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_verify_subscription_amount_mismatch_does_not_activate(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr("app.subscriptions.AsyncSessionLocal", factory)

    creator = await _persist(factory, _creator())
    sub = await _persist(factory, _subscription(creator.id, settings.pro_price_birr))

    async with factory() as session:
        loaded = await session.get(Subscription, sub.id)
        loaded.status = SUB_STATUS_PENDING_VERIFICATION
        await session.commit()

    registry = _StubRegistry(
        result=VerifyResult(request_success=True, verified=True, status="success", amount=5.0, provider="check_et")
    )
    monkeypatch.setattr("app.subscriptions.verify_registry", registry)

    async with factory() as session:
        loaded = await session.get(Subscription, sub.id)
        result = await auto_verify_subscription(session, loaded, "TLB12345678")

    assert result.verified is True  # provider confirmed the transfer...
    async with factory() as session:
        refreshed = await session.get(Subscription, sub.id)
        assert refreshed.status != SUB_STATUS_ACTIVE  # ...but for the wrong amount

    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_verify_subscription_no_providers_returns_none(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr("app.subscriptions.AsyncSessionLocal", factory)

    creator = await _persist(factory, _creator())
    sub = await _persist(factory, _subscription(creator.id))
    monkeypatch.setattr("app.subscriptions.verify_registry", _StubRegistry(result=None))

    async with factory() as session:
        loaded = await session.get(Subscription, sub.id)
        result = await auto_verify_subscription(session, loaded, "TLB12345678")

    assert result is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_expire_due_subscriptions_expires_and_notifies(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr("app.subscriptions.AsyncSessionLocal", factory)

    creator = await _persist(factory, _creator())
    now = datetime.now(timezone.utc)

    expired_sub = _subscription(creator.id, 199.0)
    expired_sub.status = SUB_STATUS_ACTIVE
    expired_sub.expires_at = now - timedelta(days=1)
    expired_sub.starts_at = now - timedelta(days=31)
    await _persist(factory, expired_sub)

    future_sub = _subscription(creator.id, 199.0)
    future_sub.status = SUB_STATUS_ACTIVE
    future_sub.expires_at = now + timedelta(days=20)
    future_sub.starts_at = now
    await _persist(factory, future_sub)

    bot = _RecordingBot()
    expired_ids = await expire_due_subscriptions(bot=bot, now=now)

    assert expired_ids == [str(expired_sub.id)]
    assert len(bot.messages) == 1
    assert bot.messages[0]["chat_id"] == creator.telegram_id

    async with factory() as session:
        e = await session.get(Subscription, expired_sub.id)
        f = await session.get(Subscription, future_sub.id)
        assert e.status == SUB_STATUS_EXPIRED
        assert f.status == SUB_STATUS_ACTIVE

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_approval_activates_and_guards(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", factory)
    monkeypatch.setattr(settings, "admin_telegram_ids", "42")

    creator = await _persist(factory, _creator())
    sub = _subscription(creator.id, settings.pro_price_birr)
    sub.status = SUB_STATUS_PENDING_VERIFICATION
    sub.ref_id = "FT87654321"
    await _persist(factory, sub)

    bot = _RecordingBot()
    context = _FakeContext(bot)

    # Non-admin cannot approve.
    outsider_query = _StubQuery(user_id=999, data=f"approve_sub:{sub.id}")
    await handlers.handle_admin_subscription_approval(_StubUpdate(outsider_query), context, str(sub.id), is_approve=True)
    async with factory() as session:
        unchanged = await session.get(Subscription, sub.id)
        assert unchanged.status == SUB_STATUS_PENDING_VERIFICATION
    assert outsider_query.answers and "admin" in outsider_query.answers[0][0].lower()

    # Admin approves.
    admin_query = _StubQuery(user_id=42, data=f"approve_sub:{sub.id}")
    await handlers.handle_admin_subscription_approval(_StubUpdate(admin_query), context, str(sub.id), is_approve=True)

    async with factory() as session:
        approved = await session.get(Subscription, sub.id)
        assert approved.status == SUB_STATUS_ACTIVE
        assert approved.verification_method == "admin_approval"
    assert any(m["chat_id"] == creator.telegram_id for m in bot.messages)

    # Double-approve is rejected by the status guard.
    second_query = _StubQuery(user_id=42, data=f"approve_sub:{sub.id}")
    await handlers.handle_admin_subscription_approval(_StubUpdate(second_query), context, str(sub.id), is_approve=True)
    assert any(a[0] and "already processed" in a[0].lower() for a in second_query.answers)

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_rejection_marks_rejected_and_notifies_creator(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", factory)
    monkeypatch.setattr(settings, "admin_telegram_ids", "42")

    creator = await _persist(factory, _creator())
    sub = _subscription(creator.id, settings.pro_price_birr)
    sub.status = SUB_STATUS_PENDING_VERIFICATION
    sub.ref_id = "FT11112222"
    await _persist(factory, sub)

    bot = _RecordingBot()
    query = _StubQuery(user_id=42, data=f"reject_sub:{sub.id}")
    await handlers.handle_admin_subscription_approval(_StubUpdate(query), _FakeContext(bot), str(sub.id), is_approve=False)

    async with factory() as session:
        rejected = await session.get(Subscription, sub.id)
        assert rejected.status == SUB_STATUS_REJECTED
    assert any(m["chat_id"] == creator.telegram_id for m in bot.messages)

    await engine.dispose()


@pytest.mark.asyncio
async def test_miniapp_profile_includes_is_pro(db_session):
    from conftest import make_init_data
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    headers = {"X-Telegram-Init-Data": make_init_data(settings.bot_token, user_id=31415)}

    creator = Creator(
        telegram_id=31415,
        telegram_username="pro_flag_creator",
        display_name="Pro Flag Creator",
        bank_code=869,
        payment_method="telebirr",
        account_number="0911000111",
        account_name="Pro Flag Creator",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        class _CM:
            async def __aenter__(self):
                return db_session

            async def __aexit__(self, *args):
                pass

        mock_session_local.return_value = _CM()

        response = client.get(f"/api/creator/{creator.id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["is_pro"] is False

        sub = _subscription(creator.id, settings.pro_price_birr)
        db_session.add(sub)
        await db_session.commit()
        await db_session.refresh(sub)
        await activate_subscription(db_session, sub, method="admin_approval")

        response = client.get(f"/api/creator/{creator.id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["is_pro"] is True
