"""Tipa Pro subscription service.

Creators buy Pro by paying Tipa directly through the same no-custody
direct-transfer flow used for tips: /pro shows Tipa's account, the creator
pays and submits the receipt reference, and the payment is auto-verified via
the provider registry (or approved manually by an admin). The verification
engine doubles as the billing engine.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Creator, Subscription, VerificationLog
from app.db.session import AsyncSessionLocal
from app.verify.base import VerificationError, VerifyResult
from app.verify.registry import verify_registry
from app.verify.service import ACCOUNT_NUMBER_METHODS, _amount_matches

logger = logging.getLogger(__name__)

SUB_STATUS_PENDING = "pending"
SUB_STATUS_PENDING_VERIFICATION = "pending_verification"
SUB_STATUS_ACTIVE = "active"
SUB_STATUS_EXPIRED = "expired"
SUB_STATUS_REJECTED = "rejected"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_active_subscription(
    session: AsyncSession,
    creator_id: uuid.UUID,
    now: datetime | None = None,
) -> Subscription | None:
    """Return the creator's currently active Pro subscription, if any."""
    now = now or _utcnow()
    stmt = (
        select(Subscription)
        .where(
            Subscription.creator_id == creator_id,
            Subscription.status == SUB_STATUS_ACTIVE,
            Subscription.expires_at > now,
        )
        .order_by(desc(Subscription.expires_at))
        .limit(1)
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def is_pro(session: AsyncSession, creator_id: uuid.UUID, now: datetime | None = None) -> bool:
    """True when the creator holds an unexpired active Pro subscription."""
    return await get_active_subscription(session, creator_id, now=now) is not None


def _pro_expiry_from(now: datetime, current_expiry: datetime | None) -> datetime:
    """Renewals stack on top of remaining time instead of being cut short."""
    base = now
    if current_expiry is not None:
        current_expiry = _as_utc(current_expiry)
        if current_expiry and current_expiry > now:
            base = current_expiry
    return base + timedelta(days=settings.pro_duration_days)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def log_subscription_verification(
    session: AsyncSession,
    subscription_id: uuid.UUID,
    provider: str,
    status: str,
    verified: bool = False,
    amount: Decimal | None = None,
    message: str = "",
) -> None:
    """Append one row to the verification audit trail for a subscription."""
    session.add(
        VerificationLog(
            tip_id=None,
            subscription_id=subscription_id,
            provider=provider,
            status=status,
            verified=verified,
            amount=amount,
            message=message or None,
        )
    )
    await session.commit()


async def activate_subscription(
    session: AsyncSession,
    sub: Subscription,
    method: str,
    now: datetime | None = None,
) -> Subscription:
    """Mark a paid subscription active and set its window (stacking renewals)."""
    now = _as_utc(now) or _utcnow()
    current = await get_active_subscription(session, sub.creator_id, now=now)
    sub.status = SUB_STATUS_ACTIVE
    sub.starts_at = now
    sub.expires_at = _pro_expiry_from(now, current.expires_at if current else None)
    sub.verification_method = method
    await session.commit()
    logger.info(
        "Subscription %s activated for creator %s until %s via %s",
        sub.id,
        sub.creator_id,
        sub.expires_at,
        method,
    )
    return sub


async def auto_verify_subscription(
    session: AsyncSession,
    sub: Subscription,
    ref_code: str,
) -> VerifyResult | None:
    """Try to confirm a claimed Pro payment across the provider registry.

    Verifies against Tipa's own receiving account. Returns None when no
    provider is configured or Tipa's receiving method is not verifiable, in
    which case the caller falls back to manual admin approval.
    """
    if not verify_registry.enabled_providers:
        return None

    bank = settings.tipa_receiving_method
    account_number = settings.tipa_receiving_account if bank in ACCOUNT_NUMBER_METHODS else None

    try:
        result = await verify_registry.verify(
            bank=bank,
            reference=ref_code,
            account_number=account_number,
            idempotency_key=f"{sub.id}-{ref_code}",
        )
    except VerificationError as e:
        logger.exception("verify registry failed for subscription %s", sub.id)
        result = VerifyResult(request_success=False, message=str(e))

    verified = result.verified and _amount_matches(result.amount, sub.amount)

    if verified:
        await activate_subscription(session, sub, method=result.provider)
    else:
        logger.info(
            "Verification did not confirm subscription %s: provider=%s status=%s verified=%s amount=%s",
            sub.id,
            result.provider,
            result.status,
            result.verified,
            result.amount,
        )

    await log_subscription_verification(
        session,
        subscription_id=sub.id,
        provider=result.provider,
        status=result.status,
        verified=verified,
        amount=result.amount,
        message=result.message,
    )
    return result


async def expire_due_subscriptions(bot=None, now: datetime | None = None) -> list[str]:
    """Expire active subscriptions whose window has ended and notify creators."""
    now = _as_utc(now) or _utcnow()
    expired_ids: list[str] = []

    async with AsyncSessionLocal() as session:
        stmt = select(Subscription).where(Subscription.status == SUB_STATUS_ACTIVE)
        res = await session.execute(stmt)
        subs = res.scalars().all()

        for sub in subs:
            expiry = _as_utc(sub.expires_at)
            if expiry is None or expiry > now:
                continue
            sub.status = SUB_STATUS_EXPIRED
            await session.commit()
            expired_ids.append(str(sub.id))
            await _notify_expired(session, sub, bot=bot)

    return expired_ids


async def _notify_expired(session: AsyncSession, sub: Subscription, bot=None) -> None:
    try:
        if bot is None:
            from app.bot.bot import get_telegram_application

            bot = get_telegram_application().bot
        c_res = await session.execute(select(Creator).where(Creator.id == sub.creator_id))
        creator = c_res.scalar_one_or_none()
        if creator is None:
            return
        await bot.send_message(
            chat_id=creator.telegram_id,
            text=(
                "⭐ **Tipa Pro Expired**\n\n"
                "Your Pro subscription has ended, so Pro features are locked again.\n"
                "Run `/pro` anytime to renew and keep your Pro features active!"
            ),
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Failed to notify creator %s about expired subscription", sub.creator_id)
