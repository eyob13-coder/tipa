"""verify.et (Suba Software) provider adapter.

Docs: https://verify.et/docs
Submits a reference for verification and polls a queued result until terminal.
"""
import asyncio
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.config import settings
from app.verify.base import VerificationError, VerificationProvider, VerifyResult

logger = logging.getLogger(__name__)


class VerifyEtProvider(VerificationProvider):
    name = "verify_et"
    supported_banks = ("cbe", "telebirr")

    def __init__(self, api_key: str | None = None, base_url: str = "https://verify.et"):
        self.api_key = api_key if api_key is not None else settings.verify_et_api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = 20.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self, idempotency_key: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "Idempotency-Key": idempotency_key,
        }

    @staticmethod
    def _extract_verification(payload: Any) -> dict[str, Any] | None:
        """Find the verification record in either the 200 or 202 response shape."""
        if not isinstance(payload, dict):
            return None
        for key in ("verification", "data"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]
        return None

    async def verify_payment(
        self,
        bank: str,
        reference: str,
        account_number: str | None = None,
        idempotency_key: str | None = None,
        wait_ms: int = 8000,
        max_polls: int = 5,
    ) -> VerifyResult:
        if not self.enabled:
            raise VerificationError("verify.et is not configured (no VERIFY_ET_API_KEY)")

        payload: dict[str, Any] = {"bank": bank}
        if bank == "cbe":
            payload["referenceNumber"] = reference
            if account_number and len(account_number) >= 8:
                payload["accountSuffix"] = account_number[-8:]
        elif bank == "telebirr":
            payload["transactionNumber"] = reference
        else:
            payload["reference"] = reference

        id_key = idempotency_key or uuid.uuid4().hex

        url = f"{self.base_url}/api/verify?waitMs={wait_ms}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=self._headers(id_key))
                if response.status_code == 202:
                    return await self._poll(client, response.json(), max_polls=max_polls)
                if response.status_code == 200:
                    return self._parse_completed(response.json())
                try:
                    body = response.json()
                    message = body.get("message") if isinstance(body, dict) else ""
                except ValueError:
                    message = ""
                raise VerificationError(
                    f"verify.et HTTP {response.status_code}: {message or response.text[:200]}"
                )
        except VerificationError:
            raise
        except (httpx.HTTPError, ValueError) as e:
            logger.exception("verify.et request failed")
            raise VerificationError(f"verify.et connection error: {e}")

    async def _poll(self, client: httpx.AsyncClient, queued: dict[str, Any], max_polls: int) -> VerifyResult:
        status_url = ""
        for key in ("statusUrl",):
            value = queued.get(key)
            if value:
                status_url = str(value)
                break
        if not status_url:
            links = queued.get("links") if isinstance(queued, dict) else None
            if isinstance(links, dict):
                status_url = str(links.get("statusUrl") or "")
        request_id = str(
            queued.get("requestId")
            or (queued.get("verification") or {}).get("requestId")
            or ""
        )
        if not status_url and request_id:
            status_url = f"{self.base_url}/api/verify/{request_id}"
        if not status_url:
            return VerifyResult(request_id=request_id, message="No status URL returned")

        poll_after = 1.5
        for _ in range(max_polls):
            await asyncio.sleep(poll_after)
            try:
                response = await client.get(status_url, headers=self._headers(request_id))
            except httpx.HTTPError as e:
                logger.warning("verify.et poll failed: %s", e)
                break
            if response.status_code != 200:
                break
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                processing = data.get("processingStatus")
                if processing in ("completed", "failed"):
                    return self._from_record(data, request_id=request_id)
                poll_after = float(
                    data.get("pollAfterMs") or payload.get("links", {}).get("pollAfterMs") or 1500
                ) / 1000.0
            else:
                completed = self._extract_verification(payload)
                if completed and completed.get("processingStatus") in ("completed", "failed"):
                    return self._from_record(completed, request_id=request_id)
        return VerifyResult(
            request_id=request_id, status="pending", message="Verification still queued after polling timeout"
        )

    def _parse_completed(self, payload: dict[str, Any]) -> VerifyResult:
        record = self._extract_verification(payload)
        request_id = str(payload.get("requestId") or "") or (str(record.get("requestId")) if record else "")
        result = self._from_record(record, request_id=request_id)
        if not result.message:
            result.message = str(payload.get("message") or "")
        return result

    @staticmethod
    def _from_record(record: dict[str, Any] | None, request_id: str = "") -> VerifyResult:
        if record is None:
            return VerifyResult(request_id=request_id, message="No verification record in response")
        status = str(record.get("status") or "unknown")
        verified = bool(record.get("verified"))
        amount = record.get("amount")
        try:
            amount_dec = Decimal(str(amount)) if amount is not None else None
        except (InvalidOperation, TypeError, ValueError):
            amount_dec = None
        return VerifyResult(
            request_success=True,
            verified=verified or status == "success",
            status=status,
            amount=amount_dec,
            request_id=request_id,
            message=str(record.get("message") or ""),
        )
