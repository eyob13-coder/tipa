"""Provider registry with automatic failover across verification providers.

Ordering matters: providers that are more reliable for a given bank go first.
A provider that errors out (unreachable, broken response) is skipped in favor
of the next one. A terminal answer — confirmed success OR a definitive
not-found/failed — stops the chain: we never ask a second provider to override
a definitive negative.
"""
import logging
from typing import Dict, List, Optional, Tuple

from app.verify.base import VerificationError, VerificationProvider, VerifyResult
from app.verify.providers.check_et import CheckEtProvider
from app.verify.providers.justverify import JustVerifyProvider
from app.verify.providers.verify_et import VerifyEtProvider

logger = logging.getLogger(__name__)

# Priority order per bank. verify.et's Telebirr rail is known to be unreliable
# (upstream Ethio Telecom issue), so check.et leads for Telebirr. verify.et only
# supports CBE and Telebirr; the remaining banks fall back to check.et then
# justverify (both cover all nine banks).
BANK_PRIORITY: Dict[str, Tuple[str, ...]] = {
    "cbe": ("verify_et", "check_et", "justverify"),
    "telebirr": ("check_et", "verify_et", "justverify"),
    "dashen": ("check_et", "justverify"),
    "awash": ("check_et", "justverify"),
    "boa": ("check_et", "justverify"),
    "cbebirr": ("check_et", "justverify"),
    "mpesa": ("check_et", "justverify"),
    "zemen": ("check_et", "justverify"),
    "siinqee": ("check_et", "justverify"),
}


class ProviderRegistry:
    def __init__(
        self,
        providers: Optional[List[VerificationProvider]] = None,
        priority: Optional[Dict[str, Tuple[str, ...]]] = None,
    ):
        self._providers: Dict[str, VerificationProvider] = {
            p.name: p for p in (providers or self._default_providers())
        }
        self._priority = priority or BANK_PRIORITY

    @staticmethod
    def _default_providers() -> List[VerificationProvider]:
        return [VerifyEtProvider(), CheckEtProvider(), JustVerifyProvider()]

    @property
    def enabled_providers(self) -> List[VerificationProvider]:
        return [p for p in self._providers.values() if p.enabled]

    def providers_for(self, bank: str) -> List[VerificationProvider]:
        order = self._priority.get(bank, tuple(self._providers))
        return [self._providers[name] for name in order if name in self._providers]

    async def verify(
        self,
        bank: str,
        reference: str,
        account_number: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> VerifyResult:
        attempts: List[VerifyResult] = []
        for provider in self.providers_for(bank):
            if not provider.enabled or bank not in provider.supported_banks:
                continue
            try:
                result = await provider.verify_payment(
                    bank=bank,
                    reference=reference,
                    account_number=account_number,
                    idempotency_key=idempotency_key,
                )
            except VerificationError as e:
                logger.warning("verification provider %s failed, trying next: %s", provider.name, e)
                attempts.append(VerifyResult(provider=provider.name, message=str(e)))
                continue
            result.provider = provider.name
            if result.conclusive:
                return result
            attempts.append(result)

        if attempts:
            return attempts[0]
        return VerifyResult(request_success=False, message="No verification provider available")


verify_registry = ProviderRegistry()
