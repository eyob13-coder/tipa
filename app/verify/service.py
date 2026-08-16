"""Auto-verification service used by both the bot claim flow and the Mini App API."""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Creator, Tip, VerificationLog
from app.payment_methods import PROVIDER_BANKS
from app.verify.base import VerifyResult
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
    amount: Optional[float] = None,
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


def _amount_matches(verified_amount: Optional[float], expected: float) -> bool:
    if verified_amount is None:
        return True
    return abs(verified_amount - expected) < 0.01


async def auto_verify_tip(
    session: AsyncSession,
    tip: Tip,
    creator: Creator,
    ref_code: str,
) -> Optional[VerifyResult]:
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
    except Exception as e:
        logger.exception("verify registry failed for tip %s: %s", tip.id, e)
        return VerifyResult(request_success=False, message=str(e))

    if result.verified and _amount_matches(result.amount, float(tip.amount)):
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
