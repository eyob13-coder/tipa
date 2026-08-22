"""Group D regression tests: webhook URL SSRF guard.

Creator-supplied endpoints must be https and must never resolve to private,
loopback, or link-local addresses — at registration AND at delivery time.
"""
import socket
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.db.models import Base, Creator, CreatorWebhook, Tip
from app.webhooks import (
    _is_blocked_ip,
    deliver_tip_verified,
    set_webhook,
    validate_webhook_url,
)


def _make_db():
    engine = __import__("sqlalchemy.ext.asyncio", fromlist=["create_async_engine"]).create_async_engine(
        "sqlite+aiosqlite://"
    )
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return engine, async_sessionmaker(bind=engine, expire_on_commit=False)


def test_blocked_ip_classification():
    assert _is_blocked_ip("127.0.0.1")
    assert _is_blocked_ip("10.1.2.3")
    assert _is_blocked_ip("192.168.0.9")
    assert _is_blocked_ip("172.16.5.5")
    assert _is_blocked_ip("169.254.169.254")  # cloud metadata service
    assert _is_blocked_ip("::1")
    assert _is_blocked_ip("fe80::1")
    assert _is_blocked_ip("0.0.0.0")
    assert not _is_blocked_ip("93.184.216.34")
    assert not _is_blocked_ip("2606:2800:220:1:248:1893:25c8:19fa")


@pytest.mark.asyncio
async def test_validate_rejects_private_literal_ips():
    for url in (
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest/meta-data/",
        "https://192.168.1.10/x",
        "https://[::1]/x",
        "http://example.com/x",  # scheme, not IP, but still rejected
    ):
        with pytest.raises(ValueError):
            await validate_webhook_url(url)


@pytest.mark.asyncio
async def test_validate_resolves_hostnames_and_blocks_internal(monkeypatch):
    fake_loopback = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
    ]
    monkeypatch.setattr("app.webhooks.socket.getaddrinfo", staticmethod(lambda *a, **k: fake_loopback), raising=True)
    with pytest.raises(ValueError):
        await validate_webhook_url("https://internal.corp.example/hook")

    # Public resolution passes.
    fake_public = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
    ]
    monkeypatch.setattr("app.webhooks.socket.getaddrinfo", staticmethod(lambda *a, **k: fake_public), raising=True)
    await validate_webhook_url("https://api.example.com/hooks/tipa")


@pytest.mark.asyncio
async def test_set_webhook_rejects_internal_endpoint():
    ok, message = await set_webhook(1, "https://169.254.169.254/latest/meta-data/")
    assert ok is False
    assert "not reachable" in message


@pytest.mark.asyncio
async def test_delivery_skips_when_url_turns_internal():
    engine, factory = _make_db()

    from app.db.session import AsyncSessionLocal  # noqa: F401  (ensure module loaded)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        creator = Creator(
            telegram_id=8300,
            display_name="D",
            account_number="1",
            account_name="D",
            payment_method="cbe",
        )
        session.add(creator)
        await session.commit()
        tip = Tip(
            creator_id=creator.id,
            amount=Decimal(50),
            platform_fee=Decimal(0),
            tx_ref="tx_ssrf",
            ref_id="REFSSRF1",
            status="success",
            verified_at=datetime.now(timezone.utc),
        )
        session.add(tip)
        session.add(
            CreatorWebhook(
                creator_id=creator.id,
                url="https://metadata.internal.example/hook",
                secret="s3cret",
                is_active=True,
            )
        )
        await session.commit()
        tip_id = str(tip.id)

    posted = []

    class _NoPostClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            posted.append(url)

            class _R:
                status_code = 200

            return _R()

    fake_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.99", 443))]

    with (
        patch("app.webhooks.AsyncSessionLocal", factory),
        patch("app.webhooks.httpx.AsyncClient", _NoPostClient),
        patch("app.webhooks.socket.getaddrinfo", staticmethod(lambda *a, **k: fake_dns)),
    ):
        # Must not raise and must NOT deliver to the internal address.
        await deliver_tip_verified(tip_id)

    assert posted == []
    await engine.dispose()


