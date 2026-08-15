import logging
from typing import Any, Dict, List, Optional
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class ChapaError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, raw_response: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.raw_response = raw_response


class ChapaClient:
    def __init__(self, secret_key: Optional[str] = None, base_url: str = "https://api.chapa.co/v1"):
        self.secret_key = secret_key or settings.chapa_secret_key
        self.base_url = base_url.rstrip("/")
        self._bank_cache: Optional[List[Dict[str, Any]]] = None

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    async def list_banks(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch list of supported banks from Chapa."""
        if self._bank_cache is not None and not force_refresh:
            return self._bank_cache

        url = f"{self.base_url}/transfer/list-banks"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=self._get_headers(), timeout=15.0)
                if response.status_code == 200:
                    res_json = response.json()
                    banks = res_json.get("data", [])
                    if isinstance(banks, list):
                        self._bank_cache = banks
                        return banks
                logger.error(f"Failed to fetch bank list: {response.status_code} - {response.text}")
            except Exception as e:
                logger.exception(f"Exception during list_banks: {e}")

        # Fallback default Ethiopian banks list if API call fails or offline/sandbox test
        fallback_banks = [
            {"id": 856, "name": "Abay Bank", "code": "856"},
            {"id": 857, "name": "Addis International Bank", "code": "857"},
            {"id": 858, "name": "Awash Bank", "code": "858"},
            {"id": 859, "name": "Bank of Abyssinia", "code": "859"},
            {"id": 860, "name": "Berhan Bank", "code": "860"},
            {"id": 861, "name": "CBE (Commercial Bank of Ethiopia)", "code": "861"},
            {"id": 862, "name": "Cooperative Bank of Oromia", "code": "862"},
            {"id": 863, "name": "Dashen Bank", "code": "863"},
            {"id": 864, "name": "Enat Bank", "code": "864"},
            {"id": 865, "name": "Hibret Bank", "code": "865"},
            {"id": 866, "name": "Lion International Bank", "code": "866"},
            {"id": 867, "name": "NIB International Bank", "code": "867"},
            {"id": 868, "name": "Oromia Bank", "code": "868"},
            {"id": 869, "name": "Telebirr", "code": "869"},
            {"id": 870, "name": "Wegagen Bank", "code": "870"},
            {"id": 871, "name": "Zemen Bank", "code": "871"},
        ]
        self._bank_cache = fallback_banks
        return fallback_banks

    async def create_subaccount(
        self,
        account_name: str,
        bank_code: int,
        account_number: str,
        split_value: Optional[float] = None,
    ) -> str:
        """Create a Chapa subaccount for creator payout splitting.

        Returns:
            subaccount_id (str)
        """
        fee = split_value if split_value is not None else settings.platform_fee_birr
        payload = {
            "account_name": account_name,
            "bank_code": bank_code,
            "account_number": str(account_number),
            "split_type": "flat",
            "split_value": fee,
        }

        url = f"{self.base_url}/subaccount"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, headers=self._get_headers(), timeout=15.0)
                res_json = response.json()
                if response.status_code in (200, 201) and res_json.get("status") == "success":
                    data = res_json.get("data")
                    if isinstance(data, str):
                        return data
                    elif isinstance(data, dict):
                        sub_id = data.get("subaccount_id") or data.get("id")
                        if sub_id:
                            return str(sub_id)
                    raise ChapaError(f"Unexpected subaccount response format: {data}", raw_response=res_json)
                else:
                    msg = res_json.get("message") or res_json.get("detail") or "Failed to create subaccount"
                    raise ChapaError(f"Chapa Error: {msg}", status_code=response.status_code, raw_response=res_json)
            except httpx.HTTPError as e:
                logger.exception(f"HTTP error creating subaccount: {e}")
                raise ChapaError(f"HTTP Connection Error: {e}")

    async def initialize_transaction(
        self,
        amount: float,
        creator_name: str,
        subaccount_id: str,
        tx_ref: str,
        tipper_telegram_id: Optional[int] = None,
        tipper_first_name: Optional[str] = None,
        tipper_last_name: Optional[str] = None,
        callback_url: Optional[str] = None,
        return_url: Optional[str] = None,
    ) -> str:
        """Initialize a tip payment transaction with split subaccount payout.

        Returns:
            checkout_url (str)
        """
        email_id = tipper_telegram_id or "anonymous"
        email = f"tipper+{email_id}@tipa.app"
        first_name = tipper_first_name or "Tipa"
        last_name = tipper_last_name or "User"
        c_url = callback_url or f"{settings.webhook_base_url.rstrip('/')}/webhooks/chapa"
        r_url = return_url or f"https://t.me/{settings.bot_username}"

        payload = {
            "amount": str(amount),
            "currency": "ETB",
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "tx_ref": tx_ref,
            "callback_url": c_url,
            "return_url": r_url,
            "customization": {
                "title": f"Tip for {creator_name}",
                "description": "Support their channel via Tipa",
            },
            "subaccounts": {
                "id": subaccount_id,
            },
        }

        url = f"{self.base_url}/transaction/initialize"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, headers=self._get_headers(), timeout=15.0)
                res_json = response.json()
                if response.status_code in (200, 201) and res_json.get("status") == "success":
                    data = res_json.get("data", {})
                    checkout_url = data.get("checkout_url")
                    if checkout_url:
                        return checkout_url
                    raise ChapaError("Missing checkout_url in Chapa response", raw_response=res_json)
                else:
                    msg = res_json.get("message") or res_json.get("detail") or "Failed to initialize transaction"
                    raise ChapaError(f"Chapa Error: {msg}", status_code=response.status_code, raw_response=res_json)
            except httpx.HTTPError as e:
                logger.exception(f"HTTP error initializing transaction: {e}")
                raise ChapaError(f"HTTP Connection Error: {e}")

    async def verify_transaction(self, tx_ref: str) -> Dict[str, Any]:
        """Authoritatively verify payment status with Chapa."""
        url = f"{self.base_url}/transaction/verify/{tx_ref}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=self._get_headers(), timeout=15.0)
                res_json = response.json()
                if response.status_code == 200 and res_json.get("status") == "success":
                    return res_json.get("data", {})
                else:
                    msg = res_json.get("message") or "Verification failed"
                    logger.warning(f"Verify tx_ref {tx_ref} failed: {msg}")
                    return {"status": "failed", "message": msg, "raw": res_json}
            except Exception as e:
                logger.exception(f"HTTP error verifying transaction {tx_ref}: {e}")
                raise ChapaError(f"Verification HTTP error: {e}")


# Singleton instance
chapa_client = ChapaClient()
