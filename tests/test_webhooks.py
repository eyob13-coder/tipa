"""Tests for signed webhooks + public creator stats endpoint (#9)."""
import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Creator, CreatorWebhook, Tip
from app.webhooks import (
    build_event_body,
    deliver_tip_verified,
    disable_webhook,
    set_webhook,
    sign_payload,
)


def _make_db():
    engine = create_async_engine("sqlite+aiosqlite://")
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False)


async def _seed(engine, factory):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        creator = Creator(
            telegram_id=8100,
            display_name="Hooked",
            account_number="9",
            account_name="H",
            payment_method="cbe",
        )
        session.add(creator)
        await session.commit()
        now = datetime.now(timezone.utc)
        tip = Tip(
            creator_id=creator.id,
            amount=Decimal(300),
            platform_fee=Decimal(0),
            tx_ref="tx_hook1",
            ref_id="CBETR999",
            status="success",
            tipper_telegram_id=4242,
            tipper_display_name="Regular Fan",
            note="keep it up",
            verification_method="auto_telebirr",
            claimed_at=now,
            verified_at=now,
        )
        session.add(tip)
        await session.commit()
        return creator.id, str(tip.id)


# --- signing -------------------------------------------------------------------


def test_signature_matches_hmac_sha256():
    secret = "s3cret"
    body = b'{"event":"tip.verified"}'
    assert sign_payload(secret, body) == hmac.new(
        b"s3cret", body, hashlib.sha256
    ).hexdigest()


def test_event_body_shape():
    tip = MagicMock(
        id="tid", amount=Decimal("12.5"), tipper_display_name=None,
        note=None, ref_id="R1", verification_method="creator_approval",
        verified_at=None,
    )
    creator = MagicMock(id="cid")
    body = build_event_body(tip, creator)
    payload = json.loads(body)
    assert payload["event"] == "tip.verified"
    assert payload["amount"] == 12.5
    assert payload["currency"] == "ETB"
    assert payload["tipper_name"] == "A follower"
    assert payload["verified_at"]  # falls back to now


# --- registration ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_webhook_rejects_http():
    ok, message = await set_webhook(1, "http://insecure.example/hook")
    assert ok is False
    assert "https://" in message


def _patch_dns_public():
    """Make every hostname resolve to a public IP so tests need no network."""
    import socket as socket_mod

    fake = [
        (
            socket_mod.AF_INET,
            socket_mod.SOCK_STREAM,
            6,
            "",
            ("93.184.216.34", 443),
        )
    ]
    return patch("app.webhooks.socket.getaddrinfo", create=True, return_value=fake)


@pytest.mark.asyncio
async def test_set_and_disable_webhook_roundtrip():
    engine, factory = _make_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            Creator(
                telegram_id=8200, display_name="W", account_number="1",
                account_name="W", payment_method="cbe",
            )
        )
        await session.commit()

    with patch("app.webhooks.AsyncSessionLocal", factory), _patch_dns_public():
        ok, message = await set_webhook(8200, "https://api.example.com/hooks/tipa/")
        assert ok is True
        # Secret is shown exactly once in the confirmation.
        first_secret = message.split("`")[3]

        ok2, message2 = await set_webhook(8200, "https://other.example/hook")
        assert ok2 is True
        second_secret = message2.split("`")[3]
        assert second_secret != first_secret  # rotated on replace

        async with factory() as session:
            stored = (await session.execute(select(CreatorWebhook))).scalar_one()
        assert stored.url == "https://other.example/hook"
        assert stored.is_active is True

        assert await disable_webhook(8200) is True
        assert await disable_webhook(8200) is False
    await engine.dispose()


# --- delivery --------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def _patch_http(status_sequence):
    """Patch httpx.AsyncClient so post() pops statuses from the sequence."""
    calls = []

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None):
            calls.append({"url": url, "body": content, "headers": headers})
            status = status_sequence.pop(0) if len(status_sequence) > 1 else status_sequence[0]
            return _FakeResponse(status)

    return patch("app.webhooks.httpx.AsyncClient", _FakeClient), calls


@pytest.mark.asyncio
async def test_delivery_posts_signed_event():
    engine, factory = _make_db()
    creator_id, tip_id = await _seed(engine, factory)
    async with factory() as session:
        session.add(
            CreatorWebhook(
                creator_id=creator_id,
                url="https://receiver.example/tipa",
                secret="topsecret",
            )
        )
        await session.commit()

    patcher, calls = _patch_http([200])
    with patch("app.webhooks.AsyncSessionLocal", factory), patcher:
        await deliver_tip_verified(tip_id)

    assert len(calls) == 1
    sent = calls[0]
    assert sent["url"] == "https://receiver.example/tipa"
    assert sent["headers"]["X-Tipa-Event"] == "tip.verified"
    expected_sig = sign_payload("topsecret", sent["body"])
    assert sent["headers"]["X-Tipa-Signature"] == expected_sig
    payload = json.loads(sent["body"])
    assert payload["ref_id"] == "CBETR999"
    assert payload["amount"] == 300

    async with factory() as session:
        hook = (await session.execute(select(CreatorWebhook))).scalar_one()
        assert hook.last_status == 200
        assert hook.last_delivered_at is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_delivery_retries_once_on_server_error():
    engine, factory = _make_db()
    creator_id, tip_id = await _seed(engine, factory)
    async with factory() as session:
        session.add(
            CreatorWebhook(
                creator_id=creator_id,
                url="https://flaky.example/hook",
                secret="k",
            )
        )
        await session.commit()

    patcher, calls = _patch_http([500, 200])
    with patch("app.webhooks.AsyncSessionLocal", factory), patcher:
        await deliver_tip_verified(tip_id)

    assert len(calls) == 2
    async with factory() as session:
        hook = (await session.execute(select(CreatorWebhook))).scalar_one()
        assert hook.last_status == 200
    await engine.dispose()


@pytest.mark.asyncio
async def test_delivery_skipped_without_active_webhook():
    engine, factory = _make_db()
    _, tip_id = await _seed(engine, factory)

    patcher, calls = _patch_http([200])
    with patch("app.webhooks.AsyncSessionLocal", factory), patcher:
        await deliver_tip_verified(tip_id)

    assert calls == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_delivery_never_raises_on_network_error():
    engine, factory = _make_db()
    creator_id, tip_id = await _seed(engine, factory)
    async with factory() as session:
        session.add(
            CreatorWebhook(
                creator_id=creator_id,
                url="https://blackhole.example/hook",
                secret="k",
            )
        )
        await session.commit()

    import httpx as httpx_mod

    class _ExplodingClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, **kwargs):
            raise httpx_mod.ConnectError("boom")

    with (
        patch("app.webhooks.AsyncSessionLocal", factory),
        patch("app.webhooks.httpx.AsyncClient", _ExplodingClient),
    ):
        await deliver_tip_verified(tip_id)  # must not raise

    async with factory() as session:
        hook = (await session.execute(select(CreatorWebhook))).scalar_one()
        assert hook.last_status is None
    await engine.dispose()


# --- public stats endpoint ---------------------------------------------------------


@pytest.mark.asyncio
async def test_public_stats_endpoint_hides_account_details(db_session):
    from fastapi.testclient import TestClient

    from app.main import app

    creator = Creator(
        telegram_id=8300,
        display_name="Public Creator",
        account_number="SECRET-1000234567",
        account_name="Secret Person",
        payment_method="cbe",
    )
    db_session.add(creator)
    await db_session.commit()
    now = datetime.now(timezone.utc)
    db_session.add(
        Tip(
            creator_id=creator.id,
            amount=Decimal(75),
            platform_fee=Decimal(0),
            tx_ref="tx_pub1",
            status="success",
            tipper_display_name="Fan One",
            claimed_at=now,
            verified_at=now,
        )
    )
    await db_session.commit()

    client = TestClient(app)
    with patch("app.api.routes.AsyncSessionLocal") as mock_local:

        class _SessionCtx:
            async def __aenter__(self):
                return db_session

            async def __aexit__(self, *exc):
                return False

        mock_local.side_effect = lambda: _SessionCtx()

        resp = client.get(f"/api/public/creators/{creator.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "Public Creator"
        assert data["total_earned"] == 75.0
        assert data["tip_count"] == 1
        assert data["recent_tips"][0]["tipper_name"] == "Fan One"
        # Payout details must never leak through the public endpoint.
        assert "SECRET" not in resp.text
        assert "account_number" not in resp.text
        assert "account_name" not in resp.text

        # Telegram-id lookup also works; unknown id -> 404.
        resp2 = client.get("/api/public/creators/8300")
        assert resp2.status_code == 200
        resp3 = client.get("/api/public/creators/not-a-real-id")
        assert resp3.status_code == 404
