"""Tests for ops-hardening: idempotency, velocity caps, freeze, circuit breaker, i18n, headers."""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db.models import Creator, RateLimitBucket, Tip
from app.i18n import STRINGS, t
from app.verify.base import VerificationError, VerifyResult
from app.verify.registry import ProviderRegistry
from tests.test_api import INIT_HEADERS, AsyncMockSession


@pytest.mark.asyncio
async def test_tip_initialize_idempotency(db_session):
    from app.main import app

    client = TestClient(app)
    creator = Creator(
        telegram_id=101112131,
        display_name="Idem Creator",
        bank_code=861,
        payment_method="cbe",
        account_number="1000111222",
        account_name="Idem Creator",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    payload = {
        "creator_id": str(creator.id),
        "amount": 25.0,
        "idempotency_key": "idem-key-abc",
    }

    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        mock_session_local.return_value = AsyncMockSession(db_session)

        first = client.post("/api/tip/initialize", json=payload, headers=INIT_HEADERS)
        assert first.status_code == 200
        tip_id_1 = first.json()["tip_id"]

        # Same key replay -> the original tip, no ghost row.
        second = client.post("/api/tip/initialize", json=payload, headers=INIT_HEADERS)
        assert second.status_code == 200
        assert second.json()["tip_id"] == tip_id_1

        rows = (
            (await db_session.execute(select(Tip).where(Tip.idempotency_key == "idem-key-abc")))
            .scalars()
            .all()
        )
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_tip_claim_replay_conflicts(db_session):
    from app.main import app

    client = TestClient(app)
    creator = Creator(
        telegram_id=201213141,
        display_name="Replay Creator",
        bank_code=869,
        payment_method="telebirr",
        account_number="0911223344",
        account_name="Replay Creator",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    tip = Tip(
        creator_id=creator.id,
        tipper_display_name="Tipper",
        amount=20.0,
        platform_fee=0.0,
        tx_ref=f"replay_{uuid.uuid4().hex[:8]}",
        status="pending_verification",
        ref_id="REPLAY001",
        claimed_at=datetime.now(timezone.utc),
    )
    db_session.add(tip)
    await db_session.commit()
    await db_session.refresh(tip)

    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        mock_session_local.return_value = AsyncMockSession(db_session)

        resp = client.post(
            "/api/tip/claim",
            json={"tip_id": str(tip.id), "ref_code": "REPLAY002"},
            headers=INIT_HEADERS,
        )
        assert resp.status_code == 409


@pytest.mark.asyncio
async def test_frozen_creator_cannot_receive_tips(db_session):
    from app.main import app

    client = TestClient(app)
    creator = Creator(
        telegram_id=301314151,
        display_name="Frozen Creator",
        bank_code=869,
        payment_method="telebirr",
        account_number="0911445566",
        account_name="Frozen Creator",
        is_frozen=True,
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    payload = {"creator_id": str(creator.id), "amount": 10.0}
    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        mock_session_local.return_value = AsyncMockSession(db_session)
        resp = client.post("/api/tip/initialize", json=payload, headers=INIT_HEADERS)
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_tipper_hourly_velocity_cap(db_session, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "tipper_hourly_init_limit", 2)
    client = TestClient(app)
    creator = Creator(
        telegram_id=401415161,
        display_name="Velocity Creator",
        bank_code=869,
        payment_method="telebirr",
        account_number="0911778899",
        account_name="Velocity Creator",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    # Isolate the bucket store so other tests don't interfere.
    stale = (
        (
            await db_session.execute(
                select(RateLimitBucket).where(RateLimitBucket.key == "tipinit:998877665")
            )
        )
        .scalars()
        .all()
    )
    for row in stale:
        await db_session.delete(row)
    await db_session.commit()

    payload = {
        "creator_id": str(creator.id),
        "amount": 5.0,
        "tipper_telegram_id": 998877665,
    }
    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        mock_session_local.return_value = AsyncMockSession(db_session)
        r1 = client.post("/api/tip/initialize", json=payload, headers=INIT_HEADERS)
        r2 = client.post("/api/tip/initialize", json=payload, headers=INIT_HEADERS)
        r3 = client.post("/api/tip/initialize", json=payload, headers=INIT_HEADERS)
        assert (r1.status_code, r2.status_code) == (200, 200)
        assert r3.status_code == 429


def _make_registry(monkeypatch, fail_count_to_open=2, cooldown=60.0):
    """Registry whose single provider fails N times then would succeed.

    Uses a bank absent from BANK_PRIORITY so the fallback ordering
    (tuple(self._providers)) selects our stub.
    """

    class _FlakyProvider:
        name = "flaky"
        enabled = True

        def __init__(self):
            self.supported_banks = ["testbank"]
            self.calls = 0

        async def verify_payment(self, **kwargs):
            self.calls += 1
            if self.calls <= fail_count_to_open:
                raise VerificationError("upstream down")
            return VerifyResult(verified=True, amount=10.0, status="success")

    monkeypatch.setattr(settings, "breaker_failure_threshold", fail_count_to_open)
    registry = ProviderRegistry(providers=[_FlakyProvider()], failure_threshold=fail_count_to_open, cooldown_seconds=cooldown)
    return registry


@pytest.mark.asyncio
async def test_circuit_breaker_skips_dead_provider_then_recovers(monkeypatch):
    registry = _make_registry(monkeypatch, fail_count_to_open=2, cooldown=60.0)

    # Two failures open the breaker...
    for _ in range(2):
        result = await registry.verify(bank="testbank", reference="R1")
        assert not result.verified

    # ...so the next call is skipped entirely (no provider attempted).
    class _Probe:
        name = "flaky"
        calls = 0
        enabled = True
        supported_banks = ("testbank",)

        async def verify_payment(self, **kwargs):
            type(self).calls += 1
            raise VerificationError("should not be called")

    probe = _Probe()
    registry._providers["flaky"] = probe  # replace to prove it isn't called
    result = await registry.verify(bank="testbank", reference="R2")
    assert probe.calls == 0
    assert not result.verified

    # Cooldown elapses -> half-open, provider gets another shot (and fails again).
    registry._open_until["flaky"] = 0.0
    result = await registry.verify(bank="testbank", reference="R3")
    assert probe.calls == 1  # half-open attempt happened
    assert not result.verified

    # A healthy provider call resets its failure count.
    class _Healthy:
        name = "healthy"
        enabled = True

        def __init__(self):
            self.supported_banks = ["testbank"]

        async def verify_payment(self, **kwargs):
            return VerifyResult(request_success=True, verified=True, amount=10.0, status="success")

    registry._providers["healthy"] = HealthyStub = _Healthy()
    result = await registry.verify(bank="testbank", reference="R4")
    assert result.verified
    assert HealthyStub.name == "healthy"


def test_i18n_translation_and_fallback():
    assert t("en", "no_tips_yet") == STRINGS["en"]["no_tips_yet"]
    am = t("am", "no_tips_yet")
    assert am != STRINGS["en"]["no_tips_yet"]
    assert "ስጦታ" in am
    # Unknown language/key falls back without raising.
    assert t("fr", "missing_key_xyz") == "missing_key_xyz"
    assert t("am", "pro_line_inactive") == STRINGS["am"]["pro_line_inactive"]


@pytest.mark.asyncio
async def test_language_picker_persists_for_creator(db_session):
    from app.bot import handlers

    creator = Creator(
        telegram_id=501516171,
        display_name="Lang Creator",
        bank_code=869,
        payment_method="telebirr",
        account_number="0911000000",
        account_name="Lang Creator",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    engine_factory_holder = {}

    class _FactoryCM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    engine_factory_holder["factory"] = lambda: _FactoryCM()

    query = _LangQuery(user_id=creator.telegram_id, data="lang_am")

    class _Ctx:
        def __init__(self):
            self.user_data = {}
            self.bot = type("_B", (), {"username": "TipaPayBot"})()

    ctx = _Ctx()

    orig = handlers.AsyncSessionLocal
    handlers.AsyncSessionLocal = engine_factory_holder["factory"]
    try:
        update = type("_U", (), {"callback_query": query})()
        await handlers.subscription_callback(update, ctx)
    finally:
        handlers.AsyncSessionLocal = orig

    await db_session.refresh(creator)
    assert creator.language == "am"
    assert any("እንኳን ደህና መጡ" in edit for edit in query.edits)


class _LangUser:
    def __init__(self, user_id, first_name="Lang"):
        self.id = user_id
        self.first_name = first_name


class _LangQuery:
    def __init__(self, user_id, data):
        self.from_user = _LangUser(user_id)
        self.data = data
        self.answers = []
        self.edits = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)


def test_security_headers_present():
    from app.main import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert "frame-ancestors" in resp.headers.get("Content-Security-Policy", "")
    assert "web.telegram.org" in resp.headers.get("Content-Security-Policy", "")


def test_metrics_endpoint_shape(db_session):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.main import app

    factory = async_sessionmaker(bind=db_session.bind, class_=AsyncSession, expire_on_commit=False)
    with patch("app.main.AsyncSessionLocal", factory):
        client = TestClient(app)
        resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_hours"] == 24
    assert isinstance(body["providers"], dict)
