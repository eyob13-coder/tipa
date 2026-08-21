"""Group A hardening regression tests: Decimal money + claim idempotency.

Covers:
- providers normalize amounts to Decimal (no float money across the wire)
- unique backstops exist on tips.ref_id, subscriptions.ref_id, and
  creators.account_verification_ref
- API claim of a reused reference returns 409
- bot tip/subscription/AV claims degrade to a friendly message when the DB
  unique constraint rejects a concurrent duplicate (IntegrityError path)
- creator approval is an atomic transition (double-tap cannot double-fire)
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


# --- Providers emit Decimal amounts ----------------------------------------


def test_provider_amounts_are_decimal():
    from decimal import Decimal

    from app.verify.providers.check_et import _as_decimal
    from app.verify.providers.justverify import _as_decimal as _jv_as_decimal
    from app.verify.providers.verify_et import VerifyEtProvider

    assert _as_decimal("50.10") == Decimal("50.10")
    assert isinstance(_as_decimal("50.10"), Decimal)
    assert _as_decimal("abc") is None
    assert _as_decimal(None) is None
    assert _jv_as_decimal(12.5) == Decimal("12.5")

    record = {"status": "success", "verified": True, "amount": "75.25"}
    result = VerifyEtProvider._from_record(record)
    assert isinstance(result.amount, Decimal)
    assert result.amount == Decimal("75.25")


# --- Unique backstops -------------------------------------------------------


@pytest.mark.asyncio
async def test_unique_backstops_exist_in_schema():
    from sqlalchemy import inspect

    engine, _factory = await _make_factory()

    async def _unique_sigs(table):
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: {
                    tuple(u["column_names"])
                    for u in inspect(sync_conn).get_unique_constraints(table)
                }
                | {
                    tuple(ix["column_names"])
                    for ix in inspect(sync_conn).get_indexes(table)
                    if ix.get("unique")
                }
            )

    tip_unique = await _unique_sigs("tips")
    sub_unique = await _unique_sigs("subscriptions")
    creator_unique = await _unique_sigs("creators")

    assert ("ref_id",) in tip_unique
    assert ("ref_id",) in sub_unique
    assert ("account_verification_ref",) in creator_unique
    await engine.dispose()


# --- API claim: reused reference -> 409 -------------------------------------


@pytest.mark.asyncio
async def test_api_claim_reused_reference_rejected(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import TipClaimRequest, claim_tip_payment

    monkeypatch.setattr("app.api.routes.AsyncSessionLocal", None)  # guard: must be patched below

    engine, factory = await _make_factory()
    monkeypatch.setattr("app.api.routes.AsyncSessionLocal", factory)

    creator = creator_row = None
    async with factory() as session:
        creator_row = _creator(telegram_id=111)
        session.add(creator_row)
        await session.commit()
        await session.refresh(creator_row)
        creator = creator_row

    tip_a = Tip(
        creator_id=creator.id,
        tipper_telegram_id=999,
        tipper_display_name="A",
        amount=50,
        platform_fee=0,
        tx_ref=f"tipa_{uuid.uuid4().hex[:12]}",
        status="success",
        ref_id="REFX1",
    )
    tip_b = Tip(
        creator_id=creator.id,
        tipper_telegram_id=888,
        tipper_display_name="B",
        amount=50,
        platform_fee=0,
        tx_ref=f"tipa_{uuid.uuid4().hex[:12]}",
        status="pending",
    )
    async with factory() as session:
        session.add_all([tip_a, tip_b])
        await session.commit()
        await session.refresh(tip_b)

    req = TipClaimRequest(tip_id=str(tip_b.id), ref_code="REFX1")
    with pytest.raises(HTTPException) as exc_info:
        await claim_tip_payment(req, "", lambda: None)
    assert exc_info.value.status_code == 409
    await engine.dispose()


# --- Bot flows: IntegrityError degrades to friendly message -----------------


class _RacyCommitSession:
    """Delegates everything to the real session but commit() raises IntegrityError."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def commit(self):
        raise IntegrityError("UNIQUE constraint failed", None, Exception("stub"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return await self._session.__aexit__(*args)


class _StubUser:
    def __init__(self, user_id):
        self.id = user_id


class _StubMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class _StubUpdate:
    def __init__(self, user_id=None, message=None):
        self.effective_user = _StubUser(user_id) if user_id else None
        self.effective_message = message


class _FakeContext:
    def __init__(self):
        self.user_data = {}


@pytest.mark.asyncio
async def test_bot_tip_claim_integrity_error_is_friendly(monkeypatch):
    from app.bot import handlers

    engine, factory = await _make_factory()

    class _RacyFactory:
        def __call__(self):
            return _RacyCommitSession(factory())

    creator = None
    async with factory() as session:
        creator = _creator(telegram_id=111)
        session.add(creator)
        await session.commit()
        await session.refresh(creator)

    tip = Tip(
        creator_id=creator.id,
        tipper_telegram_id=999,
        tipper_display_name="T",
        amount=50,
        platform_fee=0,
        tx_ref=f"tipa_{uuid.uuid4().hex[:12]}",
        status="pending",
    )
    async with factory() as session:
        session.add(tip)
        await session.commit()
        await session.refresh(tip)

    monkeypatch.setattr(handlers, "AsyncSessionLocal", _RacyFactory())
    message = _StubMessage()
    update = _StubUpdate(user_id=999, message=message)
    update.effective_message.reply_text = message.reply_text

    await handlers.process_tip_verification_claim(update, _FakeContext(), str(tip.id), "NEWREF1")

    assert any("Duplicate Reference Code" in r for r in message.replies)
    # The ref must not have been persisted.
    async with factory() as session:
        fresh = await session.get(Tip, tip.id)
        assert fresh.ref_id is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_av_claim_integrity_error_is_friendly(monkeypatch):
    from app.bot import handlers

    engine, factory = await _make_factory()

    class _RacyFactory:
        def __call__(self):
            return _RacyCommitSession(factory())

    creator = None
    async with factory() as session:
        creator = _creator(telegram_id=222)
        creator.account_verification_code = "av_test1234"
        session.add(creator)
        await session.commit()
        await session.refresh(creator)

    monkeypatch.setattr(handlers, "AsyncSessionLocal", _RacyFactory())
    message = _StubMessage()

    await handlers.process_account_verification_claim(
        _StubUpdate(user_id=222, message=message), _FakeContext(), "BANKREF9"
    )

    assert any("already used for another verification" in r for r in message.replies)
    await engine.dispose()


# --- Approval atomicity: loser never double-fires ---------------------------


@pytest.mark.asyncio
async def test_approval_side_effects_fire_exactly_once(monkeypatch):
    """Sequential double approval: second tap is answered, not re-processed."""
    import asyncio

    from app.bot import handlers

    engine, factory = await _make_factory()
    async with factory() as session:
        creator = _creator(telegram_id=111)
        session.add(creator)
        await session.commit()
        await session.refresh(creator)
        tip = Tip(
            creator_id=creator.id,
            tipper_telegram_id=999,
            tipper_display_name="T",
            amount=100,
            platform_fee=0,
            tx_ref=f"tipa_{uuid.uuid4().hex[:12]}",
            status="pending_verification",
        )
        session.add(tip)
        await session.commit()
        await session.refresh(tip)

    monkeypatch.setattr(handlers, "AsyncSessionLocal", factory)

    webhook_calls = []

    async def _fake_deliver(tip_id):
        webhook_calls.append(tip_id)

    # handlers imports deliver_tip_verified lazily inside the approve branch,
    # so patching the module attribute is picked up at call time.
    import app.webhooks as webhooks_mod

    monkeypatch.setattr(webhooks_mod, "deliver_tip_verified", _fake_deliver)

    class _Q:
        def __init__(self, user_id):
            self.from_user = _StubUser(user_id)
            self.answers = []
            self.edits = []

        async def answer(self, text=None, show_alert=False):
            self.answers.append((text, show_alert))

        async def edit_message_text(self, text, **kwargs):
            self.edits.append(text)

    class _U:
        def __init__(self, q):
            self.callback_query = q

    ctx = handlers_context_stub()
    first = _Q(111)
    await handlers.handle_creator_approval(_U(first), ctx, str(tip.id), True)
    second = _Q(111)
    await handlers.handle_creator_approval(_U(second), ctx, str(tip.id), True)
    # Let fire-and-forget tasks scheduled by the winner actually run.
    for _ in range(5):
        await asyncio.sleep(0)

    assert any("already processed" in (a[0] or "") for a in second.answers if a[0])
    assert len(first.edits) == 1
    assert len(second.edits) == 0
    assert webhook_calls.count(str(tip.id)) == 1
    await engine.dispose()


def handlers_context_stub():
    class _Bot:
        async def send_message(self, **kwargs):
            return None

    class _Ctx:
        def __init__(self):
            self.bot = _Bot()
            self.user_data = {}

    return _Ctx()
