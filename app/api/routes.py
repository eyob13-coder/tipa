import uuid
import hmac
import hashlib
import urllib.parse
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.db.models import Creator, Tip
from app.bot.bot import get_telegram_application
from app.bot.keyboards import get_creator_approval_keyboard


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
    except Exception:
        return False


router = APIRouter(prefix="/api", tags=["miniapp"])


class TipInitRequest(BaseModel):
    creator_id: str
    amount: float = Field(..., gt=0, le=50000)
    note: Optional[str] = None
    post_id: Optional[str] = None
    tipper_telegram_id: Optional[int] = None
    tipper_display_name: Optional[str] = None


class TipClaimRequest(BaseModel):
    tip_id: str
    ref_code: str


@router.get("/creator/{identifier}")
async def get_creator_profile(identifier: str):
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

        return {
            "id": str(creator.id),
            "telegram_id": creator.telegram_id,
            "telegram_username": creator.telegram_username,
            "display_name": creator.display_name,
            "payment_method": creator.payment_method,
            "account_number": creator.account_number,
            "account_name": creator.account_name,
            "deep_link": deep_link,
            "total_earned": float(total_amount),
            "total_count": total_count,
            "recent_tips": tips_data,
        }


@router.post("/tip/initialize")
async def initialize_tip(req: TipInitRequest):
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
            chapa_tx_ref=tx_ref,
            status="pending",
            note=req.note,
            post_id=req.post_id,
        )
        session.add(tip)
        await session.commit()
        await session.refresh(tip)

        method = creator.payment_method
        ussd_code = "*127#" if method == "telebirr" else "*847#"
        deep_link_url = "https://www.ethiotelecom.et/telebirr/" if method == "telebirr" else "https://www.combanketh.et/"

        return {
            "tip_id": str(tip.id),
            "tx_ref": tx_ref,
            "amount": req.amount,
            "note": req.note,
            "creator_name": creator.display_name,
            "payment_method": method,
            "account_number": creator.account_number,
            "account_name": creator.account_name,
            "ussd_code": ussd_code,
            "deep_link_url": deep_link_url,
        }


@router.post("/tip/claim")
async def claim_tip_payment(req: TipClaimRequest):
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

        tip.status = "pending_verification"
        tip.chapa_ref_id = req.ref_code
        tip.claimed_at = datetime.now(timezone.utc)
        await session.commit()

    # Send 1-tap approval notification to Creator DM
    try:
        bot_app = get_telegram_application()
        tipper_name = tip.tipper_display_name or "A follower"
        note_str = f"\n💬 **Note:** *\"{tip.note}\"*\n" if tip.note else ""
        method_name = creator.payment_method.upper()

        msg = (
            f"💸 **New Tip Claimed via Mini App ({method_name})!**\n\n"
            f"**{tipper_name}** claims they sent **{float(tip.amount):g} ETB** to your `{creator.account_number}`.\n"
            f"Receipt / Ref Code: `{req.ref_code}`\n"
            f"{note_str}\n"
            f"Please check your {method_name} app and tap **Approve Tip** below to confirm:"
        )

        approval_kb = get_creator_approval_keyboard(str(tip.id))
        await bot_app.bot.send_message(
            chat_id=creator.telegram_id,
            text=msg,
            reply_markup=approval_kb,
            parse_mode="Markdown",
        )
    except Exception:
        pass

    return {"status": "ok", "message": "Payment claim submitted successfully"}
