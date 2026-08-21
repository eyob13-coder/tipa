"""Group B hardening regression tests: API authorization & fail-closed auth.

Covers:
- tipper identity comes from the initData signature, not the request body
  (cap bypass / attribution spoof regression)
- a verified user cannot claim another user's tip session
- production without BOT_TOKEN fails closed instead of serving open API
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import Base
from app.db.models import Creator, Tip


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


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


@pytest.mark.asyncio
async def test_initialize_tip_uses_signed_identity_not_body(monkeypatch):
    """Body-supplied tipper_telegram_id is ignored when initData is signed."""
    from conftest import make_init_data

    from app.api.routes import TipInitRequest, initialize_tip

    engine, factory = await _make_factory()
    monkeypatch.setattr("app.api.routes.AsyncSessionLocal", factory)
    monkeypatch.setattr(settings, "bot_token", "12345:test-token")

    async with factory() as session:
        creator = _creator()
        session.add(creator)
        await session.commit()
        await session.refresh(creator)

    req = TipInitRequest(
        creator_id=str(creator.id),
        amount=25,
        tipper_telegram_id=4242,  # forged: attacker wants the cap keyed elsewhere
        tipper_display_name="Forged",
    )
    init_data = make_init_data("12345:test-token", user_id=555)
    resp = await initialize_tip(req, init_data)

    async with factory() as session:
        tip = await session.get(Tip, uuid.UUID(resp["tip_id"]))
        assert tip.tipper_telegram_id == 555
    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_by_non_owner_is_forbidden(monkeypatch):
    from conftest import make_init_data

    from app.api.routes import TipClaimRequest, claim_tip_payment

    engine, factory = await _make_factory()
    monkeypatch.setattr("app.api.routes.AsyncSessionLocal", factory)
    monkeypatch.setattr(settings, "bot_token", "12345:test-token")

    async with factory() as session:
        creator = _creator()
        session.add(creator)
        await session.commit()
        await session.refresh(creator)
        tip = Tip(
            creator_id=creator.id,
            tipper_telegram_id=888,
            tipper_display_name="Owner",
            amount=10,
            platform_fee=0,
            tx_ref=f"tipa_{uuid.uuid4().hex[:12]}",
            status="pending",
        )
        session.add(tip)
        await session.commit()
        await session.refresh(tip)

    req = TipClaimRequest(tip_id=str(tip.id), ref_code="SOME_REF")
    stranger = make_init_data("12345:test-token", user_id=999)
    with pytest.raises(HTTPException) as exc_info:
        await claim_tip_payment(req, stranger, lambda: None)
    assert exc_info.value.status_code == 403

    # The owner can still proceed past the ownership gate (fails later only
    # on duplicate checks, which don't apply to this fresh tip).
    owner = make_init_data("12345:test-token", user_id=888)
    result = await claim_tip_payment(req, owner, lambda: None)
    assert result["status"] == "ok"
    await engine.dispose()


@pytest.mark.asyncio
async def test_production_without_bot_token_fails_closed(monkeypatch):
    from app.api.routes import require_valid_init_data

    monkeypatch.setattr(settings, "bot_token", "")
    monkeypatch.setattr(settings, "app_env", "production")

    with pytest.raises(HTTPException) as exc_info:
        require_valid_init_data("")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_dev_without_bot_token_stays_open(monkeypatch):
    from app.api.routes import require_valid_init_data

    monkeypatch.setattr(settings, "bot_token", "")
    monkeypatch.setattr(settings, "app_env", "development")
    assert require_valid_init_data("whatever") == "whatever"
