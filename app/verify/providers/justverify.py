"""JustVerify provider adapter.

Docs: https://justverify.et
POST /v1/verify with Bearer token. Returns amount, status, and payer info in
one request — no polling needed.
"""
import logging
from decimal import Decimal, InvalidOperation

import httpx

from app.config import settings
from app.verify.base import VerificationError, VerificationProvider, VerifyResult

logger = logging.getLogger(__name__)


def _as_decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, TypeError, ValueError):
        return None


class JustVerifyProvider(VerificationProvider):
    name = "justverify"
    supported_banks = ("cbe", "telebirr", "dashen", "awash", "boa", "cbebirr", "mpesa", "zemen", "siinqee")

    def __init__(self, api_key: str | None = None, base_url: str = "https://justverify.et"):
        self.api_key = api_key if api_key is not None else settings.justverify_api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = 20.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def verify_payment(
        self,
        bank: str,
        reference: str,
        account_number: str | None = None,
        idempotency_key: str | None = None,
    ) -> VerifyResult:
        if not self.enabled:
            raise VerificationError("justverify is not configured (no JUSTVERIFY_API_KEY)")

        payload: dict[str, str] = {"provider": bank, "reference": reference}

        url = f"{self.base_url}/v1/verify"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            logger.exception("justverify request failed")
            raise VerificationError(f"justverify connection error: {e}")

        if response.status_code != 200:
            try:
                body = response.json()
                message = body.get("message") if isinstance(body, dict) else ""
            except ValueError:
                body = {}
                message = ""
            raise VerificationError(f"justverify HTTP {response.status_code}: {message or response.text[:200]}")

        body = response.json()
        success = bool(body.get("success"))
        status = str(body.get("status") or "unknown")
        amount = _as_decimal(body.get("amount"))

        if success and status == "completed":
            return VerifyResult(
                request_success=True,
                verified=True,
                status="success",
                amount=amount,
                request_id=reference,
                message=str(body.get("message") or ""),
            )
        return VerifyResult(
            request_success=True,
            verified=False,
            status=status,
            amount=amount,
            message=str(body.get("message") or ""),
        )
