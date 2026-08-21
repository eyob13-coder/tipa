import hashlib
import hmac
import io
import json
import logging
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from telegram.error import TelegramError

from app.bot.bot import get_telegram_application
from app.bot.keyboards import get_creator_approval_keyboard
from app.bot.notifications import notify_tip_success
from app.config import settings
from app.db.models import Creator, RateLimitBucket, Tip
from app.db.session import AsyncSessionLocal
from app.export import build_tips_csv
from app.payment_methods import (
    account_label_for,
    deep_link_for,
    get_method,
    method_name,
    ussd_code_for,
)
from app.subscriptions import is_pro
from app.verify.service import auto_verify_tip

logger = logging.getLogger(__name__)


def validate_telegram_init_data(init_data: str) -> bool:
    """Validate Telegram WebApp initData HMAC-SHA256 signature."""
    if not init_data or not settings.bot_token:
        return False
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        hash_check = parsed_data.pop("hash", None)
        if not hash_check:
            return False

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(calculated_hash, hash_check)
    except (TypeError, ValueError, UnicodeError):
        return False


def parse_init_data_user(init_data: str) -> int | None:
    """Return the verified Telegram user id from initData, or None."""
    if not init_data or not validate_telegram_init_data(init_data):
        return None
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        user = parsed_data.get("user")
        if not user:
            return None
        return int(json.loads(user).get("id"))
    except (TypeError, ValueError):
        return None


def require_valid_init_data(x_telegram_init_data: str = Header(default="")) -> str:
    """Dependency: reject Mini App calls that don't carry valid Telegram initData.

    When ``BOT_TOKEN`` is unset (local dev / CI) validation is skipped so the
    app stays runnable outside Telegram; in production every /api call must be
    signed by Telegram.
    """
    if not settings.bot_token:
        return x_telegram_init_data
    if not validate_telegram_init_data(x_telegram_init_data):
        raise HTTPException(status_code=401, detail="Missing or invalid Telegram initData")
    return x_telegram_init_data


CLAIM_RATE_LIMIT = 10
CLAIM_RATE_WINDOW_SECONDS = 60.0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def limit_claim_rate(request: Request) -> None:
    """Fixed-window rate limit on the claim endpoint per client IP.

    Counters live in the database so the limit is shared across uvicorn
    workers and replicas (the old in-memory deque reset on every restart,
    was per-process, and leaked an entry for every client IP).
    """
    client_ip = request.client.host if request.client else "unknown"
    key = f"claim:{client_ip}"
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        stmt = select(RateLimitBucket).where(RateLimitBucket.key == key)
        row = (await session.execute(stmt)).scalar_one_or_none()

        if row is None or _as_utc(row.window_started_at) <= now - timedelta(seconds=CLAIM_RATE_WINDOW_SECONDS):
            if row is None:
                session.add(RateLimitBucket(key=key, window_started_at=now, count=1))
            else:
                row.window_started_at = now
                row.count = 1
            await session.commit()
            return

        row.count += 1
        await session.commit()
        if row.count > CLAIM_RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Too many claims. Please try again later.")


router = APIRouter(prefix="/api", tags=["miniapp"])


class TipInitRequest(BaseModel):
    creator_id: str
    amount: Decimal = Field(..., gt=0, le=Decimal(50000))
    note: str | None = None
    post_id: str | None = None
    tipper_telegram_id: int | None = None
    tipper_display_name: str | None = None


class TipClaimRequest(BaseModel):
    tip_id: str
    ref_code: str


@router.get("/creator/{identifier}")
async def get_creator_profile(identifier: str, _init_data: str = Depends(require_valid_init_data)):
    """Fetch creator profile & stats by UUID or Telegram ID."""
    async with AsyncSessionLocal() as session:
        # Try UUID first, then Telegram ID
        creator = None
        try:
            creator_uuid = uuid.UUID(identifier)
            stmt = select(Creator).where(Creator.id == creator_uuid)
            res = await session.execute(stmt)
            creator = res.scalar_one_or_none()
        except ValueError:
            pass

        if not creator and identifier.isdigit():
            stmt = select(Creator).where(Creator.telegram_id == int(identifier))
            res = await session.execute(stmt)
            creator = res.scalar_one_or_none()

        if not creator:
            raise HTTPException(status_code=404, detail="Creator not found")

        # Stats query
        tot_stmt = (
            select(
                func.coalesce(func.sum(Tip.amount), 0),
                func.count(Tip.id),
            )
            .where(Tip.creator_id == creator.id)
            .where(Tip.status == "success")
        )
        tot_res = await session.execute(tot_stmt)
        total_amount, total_count = tot_res.first() or (0, 0)

        # Recent tips
        rec_stmt = (
            select(Tip)
            .where(Tip.creator_id == creator.id)
            .where(Tip.status == "success")
            .order_by(desc(Tip.verified_at), desc(Tip.created_at))
            .limit(10)
        )
        rec_res = await session.execute(rec_stmt)
        recent_tips = rec_res.scalars().all()

        tips_data = [
            {
                "id": str(t.id),
                "tipper_name": t.tipper_display_name or "Anonymous",
                "amount": float(t.amount),
                "note": t.note,
                "date": t.verified_at.strftime("%Y-%m-%d %H:%M") if t.verified_at else t.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for t in recent_tips
        ]

        bot_username = settings.bot_username
        deep_link = f"https://t.me/{bot_username}?start=tip_{creator.id}"

        creator_is_pro = await is_pro(session, creator.id)

        return {
            "id": str(creator.id),
            "telegram_id": creator.telegram_id,
            "telegram_username": creator.telegram_username,
            "display_name": creator.display_name,
            "payment_method": creator.payment_method,
            "account_number": creator.account_number,
            "account_name": creator.account_name,
            "deep_link": deep_link,
            "is_pro": creator_is_pro,
            "total_earned": float(total_amount),
            "total_count": total_count,
            "recent_tips": tips_data,
        }


@router.get("/creator/{identifier}/export")
async def export_creator_tips_csv(
    identifier: str,
    init_data: str = Depends(require_valid_init_data),
):
    """Export a creator's verified tip history as CSV (reconciliation trail)."""
    async with AsyncSessionLocal() as session:
        creator = None
        try:
            creator_uuid = uuid.UUID(identifier)
            stmt = select(Creator).where(Creator.id == creator_uuid)
            res = await session.execute(stmt)
            creator = res.scalar_one_or_none()
        except ValueError:
            pass

        if not creator and identifier.isdigit():
            stmt = select(Creator).where(Creator.telegram_id == int(identifier))
            res = await session.execute(stmt)
            creator = res.scalar_one_or_none()

        if not creator:
            raise HTTPException(status_code=404, detail="Creator not found")

        telegram_user_id = parse_init_data_user(init_data)
        if telegram_user_id is None or telegram_user_id != creator.telegram_id:
            raise HTTPException(status_code=403, detail="You can only export your own tips")

        if not await is_pro(session, creator.id):
            raise HTTPException(
                status_code=402,
                detail="CSV export is a Tipa Pro feature. Upgrade with /pro in the bot.",
            )

        stmt = (
            select(Tip)
            .where(Tip.creator_id == creator.id, Tip.status == "success")
            .order_by(desc(Tip.verified_at), desc(Tip.created_at))
        )
        res = await session.execute(stmt)
        tips = res.scalars().all()

    csv_text = build_tips_csv(tips)
    filename = f"tipa_{creator.telegram_id}_tips.csv"
    return StreamingResponse(
        io.StringIO(csv_text),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/tip/initialize")
async def initialize_tip(req: TipInitRequest, _init_data: str = Depends(require_valid_init_data)):
    """Initialize tip session from Mini App."""
    try:
        creator_uuid = uuid.UUID(req.creator_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid creator_id format")

    async with AsyncSessionLocal() as session:
        stmt = select(Creator).where(Creator.id == creator_uuid)
        res = await session.execute(stmt)
        creator = res.scalar_one_or_none()

        if not creator:
            raise HTTPException(status_code=404, detail="Creator not found")

        tx_ref = f"tipa_{uuid.uuid4().hex[:12]}"
        tip = Tip(
            creator_id=creator.id,
            tipper_telegram_id=req.tipper_telegram_id,
            tipper_display_name=req.tipper_display_name or "Anonymous",
            amount=req.amount,
            platform_fee=settings.platform_fee_birr,
            tx_ref=tx_ref,
            status="pending",
            note=req.note,
            post_id=req.post_id,
        )
        session.add(tip)
        await session.commit()
        await session.refresh(tip)

        method = creator.payment_method
        method_info = get_method(method)

        return {
            "tip_id": str(tip.id),
            "tx_ref": tx_ref,
            "amount": req.amount,
            "note": req.note,
            "creator_name": creator.display_name,
            "payment_method": method,
            "payment_method_name": method_info.name if method_info else method.upper(),
            "account_number": creator.account_number,
            "account_name": creator.account_name,
            "account_label": account_label_for(method),
            "ussd_code": ussd_code_for(method),
            "deep_link_url": deep_link_for(method),
        }


@router.post("/tip/claim")
async def claim_tip_payment(
    req: TipClaimRequest,
    _init_data: str = Depends(require_valid_init_data),
    _rate_limited: None = Depends(limit_claim_rate),
):
    """Claim payment sent with SMS/receipt reference code from Mini App."""
    try:
        tip_uuid = uuid.UUID(req.tip_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tip_id format")

    async with AsyncSessionLocal() as session:
        stmt = select(Tip).where(Tip.id == tip_uuid)
        res = await session.execute(stmt)
        tip = res.scalar_one_or_none()

        if not tip:
            raise HTTPException(status_code=404, detail="Tip session not found")

        c_stmt = select(Creator).where(Creator.id == tip.creator_id)
        c_res = await session.execute(c_stmt)
        creator = c_res.scalar_one_or_none()

        if not creator:
            raise HTTPException(status_code=404, detail="Creator not found")

        dup_stmt = (
            select(Tip.id)
            .where(
                Tip.ref_id == req.ref_code,
                Tip.id != tip.id,
                Tip.status.in_(["pending", "pending_verification", "success"]),
            )
            .limit(1)
        )
        dup_res = await session.execute(dup_stmt)
        if dup_res.first() is not None:
            raise HTTPException(status_code=409, detail="This reference code was already used for another tip")

        tip.ref_id = req.ref_code
        tip.claimed_at = datetime.now(timezone.utc)
        await session.commit()

        verify_result = await auto_verify_tip(session, tip, creator, req.ref_code)

        if verify_result is not None and verify_result.verified:
            await notify_tip_success(str(tip.id))
            return {
                "status": "ok",
                "verified": True,
                "message": "Tip payment verified successfully",
                "amount": float(tip.amount),
            }

        tip.status = "pending_verification"
        await session.commit()

    # Send 1-tap approval notification to Creator DM
    try:
        bot_app = get_telegram_application()
        tipper_name = tip.tipper_display_name or "A follower"
        note_str = f"\n💬 **Note:** *\"{tip.note}\"*\n" if tip.note else ""
        method_str = method_name(creator.payment_method)

        msg = (
            f"💸 **New Tip Claimed via Mini App ({method_str})!**\n\n"
            f"**{tipper_name}** claims they sent **{float(tip.amount):g} ETB** to your `{creator.account_number}`.\n"
            f"Receipt / Ref Code: `{req.ref_code}`\n"
            f"{note_str}\n"
            f"Please check your {method_str} app and tap **Approve Tip** below to confirm:"
        )

        approval_kb = get_creator_approval_keyboard(str(tip.id))
        await bot_app.bot.send_message(
            chat_id=creator.telegram_id,
            text=msg,
            reply_markup=approval_kb,
            parse_mode="Markdown",
        )
    except TelegramError as e:
        logger.warning("Failed to send creator approval notification: %s", e)

    return {"status": "ok", "verified": False, "message": "Payment claim submitted for creator approval"}
