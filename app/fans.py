"""Top-fan leaderboard and supporter tier badges.

Tiers are based on a tipper's all-time verified total with one creator:
🥉 Bronze → 🥈 Silver (500+) → 🥇 Gold (2,000+) → 💎 Diamond (5,000+ ETB).
Leaderboards are computed on demand — no counters to keep in sync.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.db.models import Tip

# (threshold_in_ETB, badge) — first match wins, checked highest-first.
TIERS: list[tuple[Decimal, str]] = [
    (Decimal(5000), "💎 Diamond"),
    (Decimal(2000), "🥇 Gold"),
    (Decimal(500), "🥈 Silver"),
    (Decimal(0), "🥉 Bronze"),
]


def fan_tier(total_amount: Decimal | float) -> str:
    """Badge label for a tipper's cumulative verified contribution."""
    total = Decimal(str(total_amount))
    for threshold, badge in TIERS:
        if total >= threshold:
            return badge
    return TIERS[-1][1]


async def top_tippers(session, creator_id, since=None, limit: int = 10) -> list[dict]:
    """Best supporters by verified amount, newest window optional.

    Rows: {telegram_id, name, total, tips}.
    """
    stmt = (
        select(
            Tip.tipper_telegram_id.label("telegram_id"),
            func.max(Tip.tipper_display_name).label("name"),
            func.sum(Tip.amount).label("total"),
            func.count(Tip.id).label("tips"),
        )
        .where(Tip.creator_id == creator_id, Tip.status == "success")
        .where(Tip.tipper_telegram_id.isnot(None))
        .group_by(Tip.tipper_telegram_id)
        .order_by(func.sum(Tip.amount).desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(Tip.verified_at >= since)

    rows = (await session.execute(stmt)).all()
    return [
        {
            "telegram_id": row.telegram_id,
            "name": row.name or f"Supporter #{row.telegram_id}",
            "total": Decimal(str(row.total)),
            "tips": int(row.tips),
        }
        for row in rows
    ]


async def top_fan_of_week(session, creator_id, now: datetime | None = None) -> dict | None:
    """Single best supporter over the last 7 days (for the weekly digest)."""
    now = now or datetime.now(timezone.utc)
    board = await top_tippers(session, creator_id, since=now - timedelta(days=7), limit=1)
    return board[0] if board else None
