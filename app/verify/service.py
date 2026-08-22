"""Auto-verification service used by both the bot claim flow and the Mini App API."""
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Creator, Tip, VerificationLog
from app.payment_methods import PROVIDER_BANKS
from app.verify.base import VerificationError, VerifyResult
from app.verify.registry import verify_registry

logger = logging.getLogger(__name__)

# Methods whose receipts can be auto-confirmed by a provider.
PROVIDER_VERIFIABLE_BANKS = PROVIDER_BANKS

# Methods that verify against a bank account number (rather than a phone number).
ACCOUNT_NUMBER_METHODS = {"cbe", "dashen", "awash", "boa", "zemen", "siinqee", "coop"}


async def log_verification_attempt(
    session: AsyncSession,
    tip_id,
    provider: str,
    status: str,
    verified: bool = False,
    amount: Decimal | None = None,
    message: str = "",
) -> None:
    """Append one row to the verification audit trail."""
    session.add(
        VerificationLog(
            tip_id=tip_id,
            provider=provider,
            status=status,
            verified=verified,
            amount=amount,
            message=message or None,
        )
    )
    await session.commit()


def _amount_matches(verified_amount, expected) -> bool:
    """Exact cent-level comparison — never float arithmetic on money.

    A missing provider amount can NEVER confirm a payment: some rails return
    ``verified=True`` without echoing the amount, and the reference alone does
    not prove the tipper sent the claimed sum. Fail safe to creator approval
    instead.
    """
    if verified_amount is None:
        return False
    try:
        verified = Decimal(str(verified_amount)).quantize(Decimal("0.01"))
        expected_dec = Decimal(str(expected)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return False
    return verified == expected_dec


async def auto_verify_tip(
    session: AsyncSession,
    tip: Tip,
    creator: Creator,
    ref_code: str,
) -> VerifyResult | None:
    """Try to confirm a claimed direct transfer across the provider registry.

    Providers are tried in priority order with automatic failover (see
    ``app.verify.registry``). On a confirmed, amount-matching result the tip is
    marked ``success`` and the commit is made inside this function. Returns
    None when no provider is configured, in which case the caller falls back to
    creator approval.
    """
    if not verify_registry.enabled_providers:
        return None

    bank = creator.payment_method
    if bank not in PROVIDER_VERIFIABLE_BANKS:
        return None
    account_number = creator.account_number if bank in ACCOUNT_NUMBER_METHODS else None

    try:
        result = await verify_registry.verify(
            bank=bank,
            reference=ref_code,
            account_number=account_number,
            idempotency_key=f"{tip.id}-{ref_code}",
        )
    except VerificationError as e:
        logger.exception("verify registry failed for tip %s", tip.id)
        return VerifyResult(request_success=False, message=str(e))

    if result.verified and _amount_matches(result.amount, tip.amount):
        tip.status = "success"
        tip.verification_method = result.provider
        tip.verified_amount = result.amount
        tip.verified_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info(
            "Tip %s auto-verified via %s (ref %s, amount %s)",
            tip.id,
            result.provider,
            ref_code,
            result.amount,
        )
        # Fire-and-forget side effects: goal refresh + VIP unlock invite.
        import asyncio

        from app.goals import on_tip_verified

        asyncio.create_task(on_tip_verified(str(tip.id)))

        async def _unlock() -> None:
            from app.unlock import send_unlock_invite

            await send_unlock_invite(str(tip.id))

        asyncio.create_task(_unlock())

        # Outbound signed webhook (fire-and-forget).
        from app.webhooks import deliver_tip_verified

        asyncio.create_task(deliver_tip_verified(str(tip.id)))

        # Live overlay alert for OBS viewers (in-process hub).
        try:
            from app.overlay import publish_tip

            publish_tip(
                str(tip.creator_id),
                {"amount": float(tip.amount), "tipper": tip.tipper_display_name, "note": tip.note},
            )
        except Exception:
            logger.exception("Overlay publish failed for tip %s", tip.id)
    else:
        logger.info(
            "Verification did not confirm tip %s: provider=%s status=%s verified=%s amount=%s",
            tip.id,
            result.provider,
            result.status,
            result.verified,
            result.amount,
        )

    await log_verification_attempt(
        session,
        tip_id=tip.id,
        provider=result.provider,
        status=result.status,
        verified=result.verified,
        amount=result.amount,
        message=result.message,
    )
    return result
