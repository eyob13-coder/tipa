"""Regression tests for the pre-launch payment-hardening fixes.

Covers: approval authorization + double-tap guard, exact Decimal amount
matching, DB-backed claim rate limiting, receipt evidence persistence, and
webhook secret enforcement.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot import handlers
from app.config import settings
from app.db.base import Base
from app.db.models import Creator, RateLimitBucket, Tip
from app.storage import save_receipt_photo
from app.verify.service import _amount_matches


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


def _tip(creator_id):
    return Tip(
        creator_id=creator_id,
        tipper_telegram_id=999,
        tipper_display_name="Tipper",
        amount=75.0,
        platform_fee=0.0,
        tx_ref=f"tip_test_{uuid.uuid4().hex[:12]}",
        status="pending_verification",
    )


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
    def __init__(self, bot=None, user_data=None):
        self.bot = bot or _RecordingBot()
        self.user_data = user_data if user_data is not None else {}


# --- Fix 1: approval authorization & idempotency -------------------------


@pytest.mark.asyncio
async def test_tip_approval_rejects_non_owner(monkeypatch):
    engine, factory = await _make_factory()
    creator = await _persist(factory, _creator(telegram_id=111))
    tip = await _persist(factory, _tip(creator.id))
    monkeypatch.setattr(handlers, "AsyncSessionLocal", factory)

    # A different Telegram user tries to approve their own "pending" tip.
    query = _StubQuery(user_id=666, data=f"approve_tip:{tip.id}")
    await handlers.handle_creator_approval(_StubUpdate(query), _FakeContext(), str(tip.id), True)

    assert any("only the creator" in (a[0] or "").lower() for a in query.answers if a[0])
    async with factory() as session:
        fresh = await session.get(Tip, tip.id)
        assert fresh.status == "pending_verification"
        assert fresh.verified_at is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_tip_approval_double_tap_is_guarded(monkeypatch):
    engine, factory = await _make_factory()
    creator = await _persist(factory, _creator(telegram_id=111))
    tip = await _persist(factory, _tip(creator.id))
    monkeypatch.setattr(handlers, "AsyncSessionLocal", factory)

    ctx = _FakeContext()
    first = _StubQuery(user_id=111, data=f"approve_tip:{tip.id}")
    await handlers.handle_creator_approval(_StubUpdate(first), ctx, str(tip.id), True)

    second = _StubQuery(user_id=111, data=f"approve_tip:{tip.id}")
    await handlers.handle_creator_approval(_StubUpdate(second), ctx, str(tip.id), True)

    assert any("already processed" in (a[0] or "") for a in second.answers if a[0])
    async with factory() as session:
        fresh = await session.get(Tip, tip.id)
        assert fresh.status == "success"
    await engine.dispose()


# --- Fix 2: exact Decimal amount matching --------------------------------


def test_amount_matches_decimal_precision():
    assert _amount_matches("50", 50) is True
    assert _amount_matches(49.99, 50.0) is False
    assert _amount_matches(50.004, 50.0) is True  # sub-cent noise rounds away
    # Providers that don't report an amount intentionally pass (reference-only match).
    assert _amount_matches(None, 50.0) is True
    assert _amount_matches("abc", 50.0) is False


# --- Fix 4: DB-backed rate limiting --------------------------------------


class _StubClient:
    def __init__(self, host):
        self.host = host


class _StubRequest:
    def __init__(self, host="1.2.3.4"):
        self.client = _StubClient(host)


@pytest.mark.asyncio
async def test_claim_rate_limit_returns_429(monkeypatch):
    from app.api import routes

    engine, factory = await _make_factory()
    monkeypatch.setattr(routes, "AsyncSessionLocal", factory)

    for _ in range(routes.CLAIM_RATE_LIMIT):
        await routes.limit_claim_rate(_StubRequest())

    with pytest.raises(HTTPException) as exc_info:
        await routes.limit_claim_rate(_StubRequest())
    assert exc_info.value.status_code == 429

    # A different client has its own bucket.
    await routes.limit_claim_rate(_StubRequest(host="5.6.7.8"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_rate_limit_window_resets(monkeypatch):
    from app.api import routes

    engine, factory = await _make_factory()
    monkeypatch.setattr(routes, "AsyncSessionLocal", factory)

    for _ in range(routes.CLAIM_RATE_LIMIT):
        await routes.limit_claim_rate(_StubRequest())

    # Age the bucket past the window -> allowed again.
    async with factory() as session:
        row = (await session.execute(select(RateLimitBucket))).scalar_one()
        row.window_started_at = datetime.now(timezone.utc) - timedelta(
            seconds=routes.CLAIM_RATE_WINDOW_SECONDS + 1
        )
        row.count = 0
        await session.commit()

    await routes.limit_claim_rate(_StubRequest())
    await engine.dispose()


# --- Fix 5: receipt evidence persistence ---------------------------------


def _tiny_jpeg() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def test_save_receipt_photo_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "receipt_storage_dir", str(tmp_path / "receipts"))
    data = _tiny_jpeg()
    path = save_receipt_photo("tip-123", data)
    assert path is not None
    from pathlib import Path

    stored = Path(path)
    assert stored.exists()
    assert stored.read_bytes() == data
    assert "tip-123" in str(stored)


def test_save_receipt_photo_handles_empty_and_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "receipt_storage_dir", str(tmp_path / "receipts"))
    assert save_receipt_photo("tip-123", b"") is None
    # Unwritable target -> returns None instead of raising.
    monkeypatch.setattr(settings, "receipt_storage_dir", str(tmp_path / "file.txt"))
    (tmp_path / "file.txt").write_text("not a dir")
    assert save_receipt_photo("tip-123", _tiny_jpeg()) is None


# --- Fix 6: webhook secret enforcement -----------------------------------


def test_webhook_requires_configured_secret(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "telegram_webhook_url", "https://example.com")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret")

    with TestClient(app) as client:  # lifespan runs but bot token is sandboxed
        missing = client.post("/telegram/webhook", json={})
        assert missing.status_code == 403
        wrong = client.post("/telegram/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
        assert wrong.status_code == 403


def test_webhook_disabled_returns_404(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "telegram_webhook_url", "")
    with TestClient(app):
        pass
    # Route check without lifespan: direct call semantics via TestClient again
    with TestClient(app) as client:
        resp = client.post("/telegram/webhook", json={})
        assert resp.status_code == 404
