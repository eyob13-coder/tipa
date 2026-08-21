"""Tests for pay-to-unlock VIP channels (#4)."""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Creator, Tip


def _make_db():
    engine = create_async_engine("sqlite+aiosqlite://")
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False)


async def _seed(engine, factory, **creator_kwargs):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        creator = Creator(
            telegram_id=7002,
            display_name="Vip",
            account_number="1",
            account_name="V",
            payment_method="cbe",
            **creator_kwargs,
        )
        session.add(creator)
        await session.commit()
        now = datetime.now(timezone.utc)
        tip = Tip(
            creator_id=creator.id,
            amount=Decimal(250),
            platform_fee=Decimal(0),
            tx_ref=f"tx_{creator.telegram_id:08d}",
            status="success",
            note="vip please",
            tipper_telegram_id=555000,
            tipper_display_name="Fan",
            claimed_at=now,
            verified_at=now,
        )
        session.add(tip)
        await session.commit()
        return str(tip.id)


class _FakeBot:
    def __init__(self):
        self.sent = []
        self.invites = []

    async def create_chat_invite_link(self, chat_id, member_limit=None, name=None):
        self.invites.append({"chat_id": chat_id, "member_limit": member_limit, "name": name})
        invite = MagicMock()
        invite.invite_link = "https://t.me/+ONE_TIME_LINK"
        return invite

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


@pytest.mark.asyncio
async def test_unlock_sends_one_time_invite():
    engine, factory = _make_db()
    tip_id = await _seed(engine, factory, vip_channel_id="-100999")

    fake_bot = _FakeBot()
    from app.unlock import send_unlock_invite

    with patch("app.unlock.AsyncSessionLocal", factory):
        await send_unlock_invite(tip_id, bot=fake_bot)

    assert len(fake_bot.invites) == 1
    assert fake_bot.invites[0]["chat_id"] == -100999
    assert fake_bot.invites[0]["member_limit"] == 1
    assert len(fake_bot.sent) == 1
    assert "https://t.me/+ONE_TIME_LINK" in fake_bot.sent[0]["text"]
    assert fake_bot.sent[0]["chat_id"] == 555000
    await engine.dispose()


@pytest.mark.asyncio
async def test_unlock_is_noop_without_vip_channel():
    engine, factory = _make_db()
    tip_id = await _seed(engine, factory)

    fake_bot = _FakeBot()
    from app.unlock import send_unlock_invite

    with patch("app.unlock.AsyncSessionLocal", factory):
        await send_unlock_invite(tip_id, bot=fake_bot)

    assert fake_bot.invites == []
    assert fake_bot.sent == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_set_vip_requires_admin_bot(monkeypatch):
    from app import unlock as unlock_mod

    fake_app = MagicMock()
    fake_app.bot.get_chat = AsyncMock(
        return_value=MagicMock(id=-100777, title="Secret Club", type="channel")
    )
    fake_app.bot.get_chat_member = AsyncMock(return_value=MagicMock(status="member"))
    fake_app.bot.id = 42

    with patch(
        "app.bot.handlers.get_telegram_application_lazy", return_value=fake_app
    ):
        ok, message = await unlock_mod.set_vip_channel(7004, "@secretclub")

    assert ok is False
    assert "admin" in message.lower()


@pytest.mark.asyncio
async def test_set_vip_stores_channel_and_unset_clears():
    engine, factory = _make_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            Creator(
                telegram_id=7005,
                display_name="Fans",
                account_number="3",
                account_name="F",
                payment_method="cbe",
            )
        )
        await session.commit()

    from app import unlock as unlock_mod

    fake_app = MagicMock()
    fake_app.bot.get_chat = AsyncMock(
        return_value=MagicMock(id=-100888, title="Fans Only", type="channel")
    )
    fake_app.bot.get_chat_member = AsyncMock(return_value=MagicMock(status="administrator"))

    with (
        patch("app.bot.handlers.get_telegram_application_lazy", return_value=fake_app),
        patch("app.unlock.AsyncSessionLocal", factory),
    ):
        ok, message = await unlock_mod.set_vip_channel(7005, "@fansonly")
        assert ok is True
        assert "Fans Only" in message

    async with factory() as session:
        stored = (await session.execute(select(Creator))).scalar_one()
    assert stored.vip_channel_id == "-100888"

    with patch("app.unlock.AsyncSessionLocal", factory):
        assert await unlock_mod.unset_vip_channel(7005) is True
        assert await unlock_mod.unset_vip_channel(7005) is False  # already off

    async with factory() as session:
        stored = (await session.execute(select(Creator))).scalar_one()
    assert stored.vip_channel_id is None
    await engine.dispose()
