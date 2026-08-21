"""Signed outbound webhooks (#9): fire ``tip.verified`` events to creators' endpoints.

One webhook per creator, set with /webhook. Deliveries are HMAC-SHA256
signed (X-Tipa-Signature) so the receiver can verify authenticity; one
automatic retry on transient failure.
"""
import hashlib
import hmac as hmac_mod
import json
import logging
import secrets
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.db.models import Creator, CreatorWebhook, Tip
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 5.0


def sign_payload(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex digest of the exact request body."""
    return hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()


def build_event_body(tip: Tip, creator: Creator) -> bytes:
    payload = {
        "event": "tip.verified",
        "tip_id": str(tip.id),
        "creator_id": str(creator.id),
        "amount": float(tip.amount),
        "currency": "ETB",
        "tipper_name": tip.tipper_display_name or "A follower",
        "note": tip.note,
        "ref_id": tip.ref_id,
        "verification_method": tip.verification_method,
        "verified_at": (
            tip.verified_at.isoformat()
            if tip.verified_at
            else datetime.now(timezone.utc).isoformat()
        ),
    }
    return json.dumps(payload, separators=(",", ":")).encode()


async def set_webhook(telegram_id: int, url: str) -> tuple[bool, str]:
    """Register/replace a creator's webhook. The secret is shown exactly once."""
    url = (url or "").strip().rstrip("/")
    if not url.startswith("https://"):
        return False, "⚠️ Webhook URL must start with `https://`."

    secret = secrets.token_urlsafe(32)
    async with AsyncSessionLocal() as session:
        creator = (
            await session.execute(select(Creator).where(Creator.telegram_id == telegram_id))
        ).scalar_one_or_none()
        if not creator:
            return False, "❌ Register first with /register."

        webhook = (
            await session.execute(
                select(CreatorWebhook).where(CreatorWebhook.creator_id == creator.id)
            )
        ).scalar_one_or_none()

        if webhook:
            webhook.url = url
            webhook.secret = secret
            webhook.is_active = True
            webhook.last_status = None
            webhook.last_delivered_at = None
        else:
            session.add(CreatorWebhook(creator_id=creator.id, url=url, secret=secret))
        await session.commit()

    return (
        True,
        (
            f"🔗 **Webhook saved!**\n\nURL: `{url}`\n\n"
            f"Signing secret (shown **once** — store it now):\n`{secret}`\n\n"
            "Every verified tip POSTs a `tip.verified` JSON event with an "
            "`X-Tipa-Signature` header (HMAC-SHA256 of the raw body)."
        ),
    )


async def disable_webhook(telegram_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        creator = (
            await session.execute(select(Creator).where(Creator.telegram_id == telegram_id))
        ).scalar_one_or_none()
        if not creator:
            return False
        webhook = (
            await session.execute(
                select(CreatorWebhook).where(CreatorWebhook.creator_id == creator.id)
            )
        ).scalar_one_or_none()
        if not webhook or not webhook.is_active:
            return False
        webhook.is_active = False
        await session.commit()
        return True


async def deliver_tip_verified(tip_id: str) -> None:
    """POST the signed event to the creator's webhook. Never raises."""
    try:
        async with AsyncSessionLocal() as session:
            tip = (
                await session.execute(select(Tip).where(Tip.id == tip_id))
            ).scalar_one_or_none()
            if not tip or tip.status != "success":
                return
            creator = (
                await session.execute(select(Creator).where(Creator.id == tip.creator_id))
            ).scalar_one_or_none()
            if not creator:
                return
            webhook = (
                await session.execute(
                    select(CreatorWebhook).where(
                        CreatorWebhook.creator_id == creator.id,
                        CreatorWebhook.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if not webhook:
                return

            body = build_event_body(tip, creator)
            signature = sign_payload(webhook.secret, body)
            headers = {
                "Content-Type": "application/json",
                "X-Tipa-Event": "tip.verified",
                "X-Tipa-Signature": signature,
            }

            last_status: int | None = None
            for attempt in range(2):  # one retry on transient failure
                try:
                    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                        resp = await client.post(webhook.url, content=body, headers=headers)
                    last_status = resp.status_code
                    if 200 <= resp.status_code < 300:
                        break
                except httpx.HTTPError:
                    last_status = None

            webhook.last_status = last_status
            webhook.last_delivered_at = datetime.now(timezone.utc)
            await session.commit()
            if last_status is None or not (200 <= last_status < 300):
                logger.warning(
                    "Webhook delivery to %s for tip %s ended with status %s",
                    webhook.url,
                    tip_id,
                    last_status,
                )
    except Exception:
        logger.exception("Failed to deliver webhook for tip %s", tip_id)
