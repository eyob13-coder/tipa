"""Tests for PDF receipts (/export pdf + export.pdf API) and /payout switching."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base, Creator, Subscription, Tip
from app.receipts import build_tips_pdf
from app.subscriptions import activate_subscription
from tests.test_api import AsyncMockSession, make_init_data

# --- Pure builder tests (no DB) -------------------------------------------


def _fake_tip(**overrides):
    base = {
        "verified_at": datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc),
        "created_at": datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
        "tipper_display_name": "Generous Fan",
        "amount": Decimal("75.00"),
        "platform_fee": Decimal("2.25"),
        "tx_ref": "pdf_tx_001",
        "verification_method": "check_et",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_creator(**overrides):
    base = {
        "display_name": "PDF Creator",
        "payment_method": "cbe",
        "account_number": "1000111222333",
        "account_name": "PDF Creator",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_tips_pdf_produces_valid_document():
    pdf = build_tips_pdf([_fake_tip()], _fake_creator())
    assert isinstance(pdf, bytes)
    assert pdf[:5] == b"%PDF-"
    assert b"/Page" in pdf


def test_build_tips_pdf_sanitizes_non_latin_text():
    """Amharic glyphs can't render in stock PDF fonts and must be transliterated."""
    tip = _fake_tip(tipper_display_name="አድናቂ Fan")
    creator = _fake_creator(account_name="ፈጣሪ Creator")
    pdf = build_tips_pdf([tip], creator)
    assert "አ".encode() not in pdf
    assert "ፈ".encode() not in pdf


def test_build_tips_pdf_empty_history_is_valid_page():
    pdf = build_tips_pdf([], _fake_creator(display_name="Nobody Yet"))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 500


def test_build_tips_pdf_totals_multiple_rows():
    tips = [
        _fake_tip(amount=Decimal(50), platform_fee=Decimal("1.5")),
        _fake_tip(amount=Decimal(25), platform_fee=Decimal("0.75")),
    ]
    pdf = build_tips_pdf(tips, _fake_creator())
    assert pdf[:5] == b"%PDF-"


# --- API endpoint ----------------------------------------------------------


def _creator_row(telegram_id):
    return Creator(
        telegram_id=telegram_id,
        display_name="PDF Creator",
        bank_code=861,
        payment_method="telebirr",
        account_number=f"09112233{telegram_id % 100:02d}",
        account_name="PDF Creator",
    )


@pytest.mark.asyncio
async def test_miniapp_pdf_export_requires_pro(db_session):
    if not settings.bot_token:
        pytest.skip("BOT_TOKEN unset: initData validation is disabled")
    from app.main import app

    creator = _creator_row(4343)
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    headers = {"X-Telegram-Init-Data": make_init_data(settings.bot_token, user_id=4343)}
    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        mock_session_local.return_value = AsyncMockSession(db_session)
        response = TestClient(app).get(f"/api/creator/{creator.id}/export.pdf", headers=headers)
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_miniapp_pdf_export_returns_pdf_for_pro(db_session):
    if not settings.bot_token:
        pytest.skip("BOT_TOKEN unset: initData validation is disabled")
    from app.main import app

    creator = _creator_row(4545)
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)
    db_session.add(
        Tip(
            creator_id=creator.id,
            tipper_display_name="Pro Tipper",
            amount=60.0,
            platform_fee=1.8,
            tx_ref="pro_pdf_tx",
            status="success",
            verification_method="check_et",
            ref_id="PROPDF1",
        )
    )

    sub = Subscription(
        creator_id=creator.id,
        plan="pro",
        status="pending",
        amount=settings.pro_price_birr,
        tx_ref="pro_pdf_test",
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)
    await activate_subscription(db_session, sub, method="admin_approval")

    headers = {"X-Telegram-Init-Data": make_init_data(settings.bot_token, user_id=4545)}
    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        mock_session_local.return_value = AsyncMockSession(db_session)
        response = TestClient(app).get(f"/api/creator/{creator.id}/export.pdf", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content[:5] == b"%PDF-"
    assert f"tipa_{creator.telegram_id}_tips.pdf" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_miniapp_pdf_export_forbids_other_user(db_session):
    if not settings.bot_token:
        pytest.skip("BOT_TOKEN unset: initData validation is disabled")
    from app.main import app

    creator = _creator_row(4646)
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    headers = {"X-Telegram-Init-Data": make_init_data(settings.bot_token, user_id=9999)}
    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        mock_session_local.return_value = AsyncMockSession(db_session)
        response = TestClient(app).get(f"/api/creator/{creator.id}/export.pdf", headers=headers)
    assert response.status_code == 403


# --- /payout flow -----------------------------------------------------------


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


class _StubUser:
    def __init__(self, user_id):
        self.id = user_id
        self.first_name = "Test"
        self.last_name = None
        self.username = f"user{user_id}"


class _StubMessage:
    def __init__(self, from_user=None):
        self.from_user = from_user
        self.texts = []
        self.markups = []

    async def reply_text(self, text, reply_markup=None, parse_mode=None, **kwargs):
        self.texts.append(text)
        self.markups.append(reply_markup)


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
    def __init__(self, message=None, query=None):
        self.message = message
        self.callback_query = query

    @property
    def effective_message(self):
        return self.message

    @property
    def effective_user(self):
        if self.message:
            return self.message.from_user
        if self.callback_query:
            return self.callback_query.from_user
        return None


class _FakeBot:
    username = "TestBot"


class _FakeContext:
    def __init__(self, bot=None, user_data=None):
        self.bot = bot if bot is not None else _FakeBot()
        self.user_data = user_data if user_data is not None else {}


@pytest.mark.asyncio
async def test_payout_start_requires_registration(monkeypatch):
    from app.bot import handlers

    engine, factory = await _make_factory()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", factory)

    msg = _StubMessage(from_user=_StubUser(777000))
    state = await handlers.payout_start(_StubUpdate(message=msg), _FakeContext())

    assert state == handlers.ConversationHandler.END
    assert any("/register" in text for text in msg.texts)
    await engine.dispose()


@pytest.mark.asyncio
async def test_payout_start_enters_method_choice_for_registered_creator(monkeypatch):
    from app.bot import handlers
    from app.bot.handlers import METHOD_CHOICE

    engine, factory = await _make_factory()
    async with factory() as session:
        session.add(_creator_row(4747))
        await session.commit()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", factory)

    msg = _StubMessage(from_user=_StubUser(4747))
    context = _FakeContext()
    state = await handlers.payout_start(_StubUpdate(message=msg), context)

    assert state == METHOD_CHOICE
    assert context.user_data.get("payout_update") is True
    joined = "\n".join(msg.texts)
    assert "Telebirr" in joined  # current method echoed in prompt
    assert any(markup is not None for markup in msg.markups)  # method keyboard attached
    await engine.dispose()


@pytest.mark.asyncio
async def test_confirm_payout_update_resets_account_verification(monkeypatch):
    """Confirming a payout switch saves the new method and drops ownership proof."""
    from app.bot import handlers
    from app.bot.handlers import confirm_registration_callback

    engine, factory = await _make_factory()
    async with factory() as session:
        session.add(_creator_row(4848))
        await session.commit()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", factory)

    # Seed existing verification state that must be cleared by the switch.
    async with factory() as session:
        creator = (
            await session.execute(select(Creator).where(Creator.telegram_id == 4848))
        ).scalar_one()
        creator.account_verified = True
        creator.account_verification_code = "TIPA-ABC123"
        creator.account_verification_ref = "old-ref"
        await session.commit()

    query = _StubQuery(user_id=4848, data="reg_confirm")
    context = _FakeContext(
        user_data={
            "payout_update": True,
            "selected_method": "boa",
            "account_number": "9998887770",
            "account_name": "PDF Creator",
        }
    )
    state = await confirm_registration_callback(_StubUpdate(query=query), context)

    assert state == handlers.ConversationHandler.END
    joined = "\n".join(query.edits)
    assert "Payout Details Updated" in joined
    assert "/verifyaccount" in joined

    async with factory() as session:
        fresh = (
            await session.execute(select(Creator).where(Creator.telegram_id == 4848))
        ).scalar_one()
        assert fresh.payment_method == "boa"
        assert fresh.account_number == "9998887770"
        assert fresh.account_verified is False
        assert fresh.account_verification_code is None
        assert fresh.account_verification_ref is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_register_start_clears_stale_payout_flag():
    """A normal /register after an aborted /payout must not inherit update semantics."""
    from app.bot import handlers
    from app.bot.handlers import METHOD_CHOICE

    msg = _StubMessage(from_user=_StubUser(4949))
    context = _FakeContext(user_data={"payout_update": True})
    state = await handlers.register_start(_StubUpdate(message=msg), context)

    assert state == METHOD_CHOICE
    assert "payout_update" not in context.user_data
