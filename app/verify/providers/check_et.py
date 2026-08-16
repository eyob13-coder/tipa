"""Check.et provider adapter.

Docs: https://docs.check.et/api-reference/verify
One endpoint (POST /verify), Bearer token auth, normalized response across
CBE, Telebirr, Dashen, Awash, BOA, CBE Birr, M-Pesa, Zemen, and Siinqee.
"""
import logging
from typing import Dict, Optional

import httpx

from app.config import settings
from app.verify.base import VerificationError, VerificationProvider, VerifyResult

logger = logging.getLogger(__name__)


def _as_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class CheckEtProvider(VerificationProvider):
    name = "check_et"
    supported_banks = ("cbe", "telebirr", "dashen", "awash", "boa", "cbebirr", "mpesa", "zemen", "siinqee")

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.check.et/api/v1"):
        self.api_key = api_key if api_key is not None else settings.check_et_api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = 20.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def verify_payment(
        self,
        bank: str,
        reference: str,
        account_number: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> VerifyResult:
        if not self.enabled:
            raise VerificationError("check.et is not configured (no CHECK_ET_API_KEY)")

        payload: Dict[str, object] = {"bank": bank, "transaction_number": reference}
        if bank in ("cbe", "cbebirr", "boa") and account_number:
            payload["account_number"] = account_number

        url = f"{self.base_url}/verify"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers())
        except Exception as e:
            logger.exception("check.et request failed: %s", e)
            raise VerificationError(f"check.et connection error: {e}")

        try:
            body = response.json()
        except Exception:
            body = {}

        if response.status_code == 404:
            return VerifyResult(
                request_success=True,
                verified=False,
                status="not_found",
                message=str(body.get("message") or "Transaction not found."),
            )
        if response.status_code != 200:
            message = body.get("message") if isinstance(body, dict) else ""
            raise VerificationError(f"check.et HTTP {response.status_code}: {message or response.text[:200]}")

        success = bool(body.get("success"))
        data = body.get("data") or {}
        receipt = data.get("receipt") or {}
        amount = _as_float(receipt.get("amount") or data.get("amount"))

        if success:
            return VerifyResult(
                request_success=True,
                verified=True,
                status="success",
                amount=amount,
                request_id=str(data.get("verification_id") or ""),
                message=str(body.get("message") or ""),
            )
        if body.get("exists") is False:
            return VerifyResult(
                request_success=True,
                verified=False,
                status="not_found",
                message=str(body.get("message") or "Transaction not found."),
            )
        return VerifyResult(
            request_success=True,
            verified=False,
            status="failed",
            amount=amount,
            message=str(body.get("message") or ""),
        )
