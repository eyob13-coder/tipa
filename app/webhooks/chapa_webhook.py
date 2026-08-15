import logging
from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.db.models import Tip, Creator
from app.chapa.client import chapa_client
from app.bot.bot import get_telegram_application

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def notify_tip_success(tip_id: str):
    """Background task to send Telegram notification to creator and tipper."""
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Tip).where(Tip.id == tip_id)
            res = await session.execute(stmt)
            tip = res.scalar_one_or_none()

            if not tip or tip.status != "success":
                return

            c_stmt = select(Creator).where(Creator.id == tip.creator_id)
            c_res = await session.execute(c_stmt)
            creator = c_res.scalar_one_or_none()

        if not creator:
            return

        bot_app = get_telegram_application()
        tipper_name = tip.tipper_display_name or "A follower"

        # Notify Creator
        note_text = f"\n💬 **Message:** *\"{tip.note}\"*\n" if tip.note else ""
        creator_msg = (
            f"🎉 **Tip Received!**\n\n"
            f"**{tipper_name}** just tipped you **{float(tip.amount):g} ETB**!\n"
            f"{note_text}"
            f"The funds have been transferred directly to your bank account.\n\n"
            f"Run `/mytips` to view your updated dashboard."
        )
        try:
            await bot_app.bot.send_message(
                chat_id=creator.telegram_id,
                text=creator_msg,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram notification to creator {creator.telegram_id}: {e}")

        # Notify Tipper if telegram ID is present
        if tip.tipper_telegram_id:
            tipper_msg = (
                f"✅ **Tip Sent Successfully!**\n\n"
                f"Your tip of **{float(tip.amount):g} ETB** to **{creator.display_name}** has been confirmed.\n"
                f"Thank you for supporting creators on Tipa! 🙏"
            )
            try:
                await bot_app.bot.send_message(
                    chat_id=tip.tipper_telegram_id,
                    text=tipper_msg,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Failed to send Telegram notification to tipper {tip.tipper_telegram_id}: {e}")

    except Exception as e:
        logger.exception(f"Error in notify_tip_success background task: {e}")


async def process_chapa_callback(tx_ref: str, raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """Verify transaction with Chapa authoritatively and update tip record."""
    if not tx_ref:
        raise HTTPException(status_code=400, detail="Missing tx_ref parameter")

    async with AsyncSessionLocal() as session:
        stmt = select(Tip).where(Tip.chapa_tx_ref == tx_ref)
        res = await session.execute(stmt)
        tip = res.scalar_one_or_none()

        if not tip:
            logger.warning(f"Webhook received for unknown tx_ref: {tx_ref}")
            return {"status": "error", "message": "Transaction reference not found"}

        # Idempotency check
        if tip.status == "success":
            return {"status": "ok", "message": "Transaction already verified as success"}

        # Authoritative verification with Chapa API
        verify_res = await chapa_client.verify_transaction(tx_ref)
        verify_status = verify_res.get("status")

        if verify_status == "success":
            tip.status = "success"
            tip.chapa_ref_id = str(verify_res.get("reference") or verify_res.get("id") or raw_data.get("ref_id", ""))
            tip.verified_at = datetime.now(timezone.utc)
            await session.commit()

            # Schedule notification background job
            await notify_tip_success(str(tip.id))

            return {"status": "ok", "message": "Tip payment verified successfully"}
        else:
            tip.status = "failed"
            await session.commit()
            return {"status": "failed", "message": "Transaction verification failed at Chapa"}


@router.get("/chapa")
async def chapa_webhook_get(request: Request):
    """Handle GET callback from Chapa."""
    params = dict(request.query_params)
    tx_ref = params.get("trx_ref") or params.get("tx_ref") or ""
    return await process_chapa_callback(tx_ref, params)


@router.post("/chapa")
async def chapa_webhook_post(request: Request):
    """Handle POST webhook from Chapa."""
    try:
        body = await request.json()
    except Exception:
        body = dict(request.query_params)

    tx_ref = body.get("trx_ref") or body.get("tx_ref") or ""
    return await process_chapa_callback(tx_ref, body)
