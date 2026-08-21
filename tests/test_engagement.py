"""Tests for engagement features: tip goals (#2), top fans (#3), weekly digest (#6)."""
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Creator, Tip, TipGoal
from app.fans import fan_tier, top_tippers
from app.goals import (
    cancel_goal,
    create_goal,
    get_active_goal,
    goal_progress_line,
    goal_raised_amount,
    on_tip_verified,
    render_progress_bar,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
GOAL_START = NOW - timedelta(days=2)


# --- fakes -------------------------------------------------------------------


class _FakeBot:
    def __init__(self):
        self.edits_text = []
        self.edits_caption = []
        self.sent = []

    async def edit_message_text(self, **kwargs):
        self.edits_text.append(kwargs)

    async def edit_message_caption(self, **kwargs):
        self.edits_caption.append(kwargs)

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class _StubUser:
    def __init__(self, user_id):
        self.id = user_id


class _StubMessage:
    def __init__(self, from_user=None):
        self.from_user = from_user
        self.texts = []

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)


class _StubUpdate:
    def __init__(self, message=None):
        self.message = message

    @property
    def effective_message(self):
        return self.message

    @property
    def effective_user(self):
        return self.message.from_user if self.message else None


class _StubContext:
    def __init__(self, args=None):
        self.args = args
        self.user_data = {}
        self.bot = _FakeBot()


async def _make_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


def _creator_row(tg_id, lang="en"):
    return Creator(
        telegram_id=tg_id,
        display_name=f"Creator {tg_id}",
        bank_code=861,
        payment_method="telebirr",
        account_number="0911223344",
        account_name=f"Creator {tg_id}",
        language=lang,
    )


def _tip_row(creator_id, amount, status="success", verified=None, tipper=(111, "Fan One")):
    return Tip(
        creator_id=creator_id,
        tipper_telegram_id=tipper[0] if tipper else None,
        tipper_display_name=tipper[1] if tipper else None,
        amount=amount,
        platform_fee=Decimal(0),
        tx_ref=f"tx_{uuid_mod.uuid4().hex[:10]}",
        status=status,
        verified_at=verified,
    )


# --- pure rendering -----------------------------------------------------------


def test_render_progress_bar_math():
    width = render_progress_bar(Decimal(0), Decimal(100))
    assert width == "░" * 12
    half = render_progress_bar(Decimal(50), Decimal(100))
    assert half == "▓" * 6 + "░" * 6
    full = render_progress_bar(Decimal(120), Decimal(100))  # clamped at 100%
    assert full == "▓" * 12
    zero_target = render_progress_bar(Decimal(5), Decimal(0))
    assert zero_target == "░" * 12


def test_goal_progress_line_states():
    goal = SimpleNamespaceGoal(title="New camera", target_amount=Decimal(1000), reached_at=None)
    line = goal_progress_line(goal, Decimal(400))
    assert "New camera" in line
    assert "40%" in line
    assert "400" in line and "1,000" in line
    assert "REACHED" not in line

    done = goal_progress_line(goal, Decimal(1200))
    assert "GOAL REACHED" in done


class SimpleNamespaceGoal:
    """Duck-typed stand-in exposing only what goal_progress_line reads."""

    def __init__(self, title, target_amount, reached_at=None):
        self.title = title
        self.target_amount = target_amount
        self.reached_at = reached_at


def test_fan_tier_thresholds():
    assert fan_tier(0) == "🥉 Bronze"
    assert fan_tier(499.99) == "🥉 Bronze"
    assert fan_tier(500) == "🥈 Silver"
    assert fan_tier(1999) == "🥈 Silver"
    assert fan_tier(2000) == "🥇 Gold"
    assert fan_tier(4999) == "🥇 Gold"
    assert fan_tier(5000) == "💎 Diamond"
    assert fan_tier(99999) == "💎 Diamond"


# --- goal lifecycle ------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_goal_replaces_previous_active():
    engine, factory = await _make_factory()
    async with factory() as session:
        creator = _creator_row(5101)
        session.add(creator)
        await session.commit()

        first = await create_goal(session, creator.id, "Mic", Decimal(500))
        second = await create_goal(session, creator.id, "Camera", Decimal(900))

        await session.refresh(first)
        assert first.status == "cancelled"
        active = await get_active_goal(session, creator.id)
        assert active.id == second.id
        assert active.title == "Camera"

        # A creator with no goals: cancel_goal reports nothing to cancel.
        stranger_id = uuid_mod.uuid4()
        assert await get_active_goal(session, stranger_id) is None
        assert await cancel_goal(session, stranger_id) is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_goal_progress_counts_only_verified_tips_since_start():
    engine, factory = await _make_factory()
    async with factory() as session:
        creator = _creator_row(5201)
        session.add(creator)
        await session.commit()

        goal = TipGoal(
            creator_id=creator.id,
            title="Trip",
            target_amount=Decimal(1000),
            created_at=GOAL_START,
        )
        session.add(goal)

        session.add(_tip_row(creator.id, Decimal(100), verified=GOAL_START + timedelta(days=1)))  # counts
        session.add(_tip_row(creator.id, Decimal(500), status="pending_verification"))  # unconfirmed
        session.add(_tip_row(creator.id, Decimal(700), status="failed"))  # rejected
        session.add(
            _tip_row(creator.id, Decimal(50), verified=GOAL_START - timedelta(days=1))  # before goal
        )
        await session.commit()

        raised = await goal_raised_amount(session, goal)
        assert raised == Decimal(100)
    await engine.dispose()


@pytest.mark.asyncio
async def test_on_tip_verified_updates_bound_post_and_celebrates(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr("app.goals.AsyncSessionLocal", factory)
    bot = _FakeBot()

    async with factory() as session:
        creator = _creator_row(5301)
        session.add(creator)
        await session.commit()
        creator_id = creator.id

        goal = TipGoal(
            creator_id=creator_id,
            title="New lens",
            target_amount=Decimal(150),
            created_at=GOAL_START,
            bound_channel_id="-100999",
            bound_message_id="42",
            bound_text="Hello followers!",
        )
        session.add(goal)
        tip1 = _tip_row(creator_id, Decimal(100), verified=GOAL_START + timedelta(hours=1))
        session.add(tip1)
        await session.commit()
        goal_id, tip1_id = goal.id, tip1.id

    async with factory() as session:
        goal = await session.get(TipGoal, goal_id)
        await on_tip_verified(tip1_id, bot=bot)
        await session.refresh(goal)
        assert goal.reached_at is None  # 100/150 — not yet

    assert len(bot.edits_text) == 1
    edit = bot.edits_text[0]
    assert edit["chat_id"] == -100999 and edit["message_id"] == 42
    assert "Hello followers!" in edit["text"]
    assert "100" in edit["text"] and "150" in edit["text"]

    # Second tip crosses the target -> post updated AND celebration DM sent once.
    async with factory() as session:
        tip2 = _tip_row(creator_id, Decimal(60), verified=GOAL_START + timedelta(hours=2))
        session.add(tip2)
        await session.commit()
        tip2_id = tip2.id

    async with factory() as session:
        goal = await session.get(TipGoal, goal_id)
        await on_tip_verified(tip2_id, bot=bot)
        await session.refresh(goal)
        assert goal.status == "reached"
        assert goal.reached_at is not None

    assert len(bot.edits_text) == 2
    assert any("GOAL REACHED!" in msg["text"] for msg in bot.sent)
    await engine.dispose()


@pytest.mark.asyncio
async def test_on_tip_verified_is_noop_without_goal(monkeypatch):
    engine, factory = await _make_factory()
    monkeypatch.setattr("app.goals.AsyncSessionLocal", factory)
    bot = _FakeBot()

    async with factory() as session:
        creator = _creator_row(5401)
        session.add(creator)
        await session.commit()
        tip = _tip_row(creator.id, Decimal(10), verified=datetime.now(timezone.utc))
        session.add(tip)
        await session.commit()
        tip_id = tip.id

    await on_tip_verified(tip_id, bot=bot)  # must not raise
    assert bot.edits_text == [] and bot.sent == []
    await engine.dispose()


# --- leaderboard ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_top_tippers_orders_and_filters():
    engine, factory = await _make_factory()
    async with factory() as session:
        creator = _creator_row(5501)
        session.add(creator)
        await session.commit()

        month_ago = NOW - timedelta(days=35)  # previous month, outside the window below
        this_month = NOW - timedelta(days=3)

        session.add(_tip_row(creator.id, Decimal(200), verified=this_month, tipper=(201, "Alice")))
        session.add(_tip_row(creator.id, Decimal(300), verified=this_month, tipper=(201, "Alice")))  # Alice total 500
        session.add(_tip_row(creator.id, Decimal(900), verified=this_month, tipper=(202, "Bob")))
        session.add(_tip_row(creator.id, Decimal(999), verified=month_ago, tipper=(202, "Bob")))  # outside window
        session.add(_tip_row(creator.id, Decimal(50), verified=this_month, tipper=None))  # anonymous excluded
        await session.commit()

        rows = await top_tippers(session, creator.id, since=NOW.replace(day=1), limit=10)
        assert [r["name"] for r in rows] == ["Bob", "Alice"]
        assert rows[0]["total"] == Decimal(900) and rows[0]["tips"] == 1
        assert rows[1]["total"] == Decimal(500) and rows[1]["tips"] == 2

        all_rows = await top_tippers(session, creator.id, limit=10)
        bob_all_time = next(r for r in all_rows if r["telegram_id"] == 202)
        assert bob_all_time["total"] == Decimal(1899)  # windowless includes old tip
    await engine.dispose()


# --- weekly digest ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_skips_creators_without_recent_tips():
    engine, factory = await _make_factory()
    async with factory() as session:
        creator = _creator_row(5601)
        session.add(creator)
        await session.commit()

        from app.bot.digest import build_weekly_digest

        assert await build_weekly_digest(session, creator, now=NOW) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_digest_contains_earnings_top_fan_and_goal():
    engine, factory = await _make_factory()
    async with factory() as session:
        creator = _creator_row(5701)
        session.add(creator)
        await session.commit()

        session.add(TipGoal(creator_id=creator.id, title="Studio light", target_amount=Decimal(800), created_at=GOAL_START))
        session.add(_tip_row(creator.id, Decimal(250), verified=NOW - timedelta(days=1), tipper=(301, "Mia")))
        await session.commit()

        from app.bot.digest import build_weekly_digest

        digest = await build_weekly_digest(session, creator, now=NOW)
        assert digest is not None
        assert "250.00 ETB" in digest
        assert "Mia" in digest
        assert "Studio light" in digest
        assert "▓" in digest  # progress bar rendered
    await engine.dispose()


@pytest.mark.asyncio
async def test_send_due_digests_dedups_per_creator(monkeypatch):
    from app.bot import digest as digest_mod

    engine, factory = await _make_factory()
    monkeypatch.setattr(digest_mod, "AsyncSessionLocal", factory)
    bot = _FakeBot()

    async with factory() as session:
        creator = _creator_row(5801)
        session.add(creator)
        await session.commit()
        session.add(_tip_row(creator.id, Decimal(90), verified=NOW - timedelta(days=1)))
        await session.commit()
        tg = creator.telegram_id

    sent_first = await digest_mod.send_due_digests(bot, now=NOW)
    assert sent_first == 1
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == tg

    # Immediate re-run: timestamp stamped -> nothing resent.
    sent_second = await digest_mod.send_due_digests(bot, now=NOW + timedelta(minutes=5))
    assert sent_second == 0
    assert len(bot.sent) == 1

    # A week later the creator is due again — but only tips from the NEW
    # window count, so seed a fresh one before expecting the next digest.
    async with factory() as session:
        creator = (
            await session.execute(select(Creator).where(Creator.telegram_id == tg))
        ).scalar_one()
        session.add(_tip_row(creator.id, Decimal(40), verified=NOW + timedelta(days=8)))
        await session.commit()

    sent_third = await digest_mod.send_due_digests(bot, now=NOW + timedelta(days=8))
    assert sent_third == 1
    await engine.dispose()


# --- command handlers -------------------------------------------------------------


@pytest.mark.asyncio
async def test_goal_command_usage_and_creation(monkeypatch):
    from app.bot import handlers

    engine, factory = await _make_factory()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", factory)

    # Unregistered creator -> register nudge.
    msg = _StubMessage(from_user=_StubUser(5901))
    await handlers.goal_command(_StubUpdate(message=msg), _StubContext(args=["100", "Thing"]))
    assert any("/register" in text for text in msg.texts)

    async with factory() as session:
        session.add(_creator_row(5902))
        await session.commit()

    msg = _StubMessage(from_user=_StubUser(5902))
    await handlers.goal_command(_StubUpdate(message=msg), _StubContext(args=["750", "New", "camera"]))
    assert any("🎯" in text for text in msg.texts)
    assert any("New camera" in text for text in msg.texts)

    async with factory() as session:
        active = (
            await session.execute(select(TipGoal).where(TipGoal.creator_id == select(Creator.id).where(Creator.telegram_id == 5902).scalar_subquery()))
        ).scalars().all()
        assert len(active) == 1
        assert active[0].title == "New camera"
        assert active[0].target_amount == Decimal(750)

    # Missing args -> usage hint, no crash.
    msg = _StubMessage(from_user=_StubUser(5902))
    await handlers.goal_command(_StubUpdate(message=msg), _StubContext(args=[]))
    assert any("/goal" in text for text in msg.texts)
    await engine.dispose()


@pytest.mark.asyncio
async def test_endgoal_cancels_active_goal(monkeypatch):
    from app.bot import handlers

    engine, factory = await _make_factory()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", factory)

    async with factory() as session:
        creator = _creator_row(6001)
        session.add(creator)
        await session.commit()
        session.add(TipGoal(creator_id=creator.id, title="Old goal", target_amount=Decimal(10)))
        await session.commit()

    msg = _StubMessage(from_user=_StubUser(6001))
    await handlers.endgoal_command(_StubUpdate(message=msg), _StubContext())
    assert any("removed" in text.lower() for text in msg.texts)

    async with factory() as session:
        rows = (await session.execute(select(TipGoal))).scalars().all()
        assert rows[0].status == "cancelled"
    await engine.dispose()
