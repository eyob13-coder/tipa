"""Provider registry with automatic failover across verification providers.

Ordering matters: providers that are more reliable for a given bank go first.
A provider that errors out (unreachable, broken response) is skipped in favor
of the next one. A terminal answer — confirmed success OR a definitive
not-found/failed — stops the chain: we never ask a second provider to override
a definitive negative.
"""
import logging
import time

from app.config import settings
from app.verify.base import VerificationError, VerificationProvider, VerifyResult
from app.verify.providers.check_et import CheckEtProvider
from app.verify.providers.justverify import JustVerifyProvider
from app.verify.providers.verify_et import VerifyEtProvider

logger = logging.getLogger(__name__)

# Priority order per bank. verify.et's Telebirr rail is known to be unreliable
# (upstream Ethio Telecom issue), so check.et leads for Telebirr. verify.et only
# supports CBE and Telebirr; the remaining banks fall back to check.et then
# justverify (both cover all nine banks).
BANK_PRIORITY: dict[str, tuple[str, ...]] = {
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
        providers: list[VerificationProvider] | None = None,
        priority: dict[str, tuple[str, ...]] | None = None,
        failure_threshold: int | None = None,
        cooldown_seconds: float | None = None,
    ):
        self._providers: dict[str, VerificationProvider] = {
            p.name: p for p in (providers or self._default_providers())
        }
        self._priority = priority or BANK_PRIORITY
        # Circuit breaker: after N consecutive failures a provider is skipped
        # for a cooldown window, so a dead verify.et doesn't get retried first
        # on every single request.
        self._failure_threshold = (
            failure_threshold if failure_threshold is not None else settings.breaker_failure_threshold
        )
        self._cooldown_seconds = (
            cooldown_seconds if cooldown_seconds is not None else settings.breaker_cooldown_seconds
        )
        self._consecutive_failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    @staticmethod
    def _default_providers() -> list[VerificationProvider]:
        return [VerifyEtProvider(), CheckEtProvider(), JustVerifyProvider()]

    @property
    def enabled_providers(self) -> list[VerificationProvider]:
        return [p for p in self._providers.values() if p.enabled]

    def _breaker_open(self, name: str) -> bool:
        until = self._open_until.get(name)
        if until is None:
            return False
        if time.monotonic() >= until:
            # Cooldown elapsed — half-open: give the provider one more shot.
            del self._open_until[name]
            self._consecutive_failures[name] = self._failure_threshold - 1
            return False
        return True

    def _record_failure(self, name: str) -> None:
        count = self._consecutive_failures.get(name, 0) + 1
        self._consecutive_failures[name] = count
        if count >= self._failure_threshold:
            self._open_until[name] = time.monotonic() + self._cooldown_seconds
            logger.warning(
                "verification provider %s opened circuit breaker for %.0fs after %d failures",
                name,
                self._cooldown_seconds,
                count,
            )

    def providers_for(self, bank: str) -> list[VerificationProvider]:
        order = self._priority.get(bank, tuple(self._providers))
        return [self._providers[name] for name in order if name in self._providers]

    async def verify(
        self,
        bank: str,
        reference: str,
        account_number: str | None = None,
        idempotency_key: str | None = None,
    ) -> VerifyResult:
        attempts: list[VerifyResult] = []
        for provider in self.providers_for(bank):
            if not provider.enabled or bank not in provider.supported_banks:
                continue
            if self._breaker_open(provider.name):
                logger.info("skipping verification provider %s (breaker open)", provider.name)
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
                self._record_failure(provider.name)
                attempts.append(VerifyResult(provider=provider.name, message=str(e)))
                continue
            self._consecutive_failures[provider.name] = 0
            result.provider = provider.name
            if result.conclusive:
                return result
            attempts.append(result)

        if attempts:
            return attempts[0]
        return VerifyResult(request_success=False, message="No verification provider available")


verify_registry = ProviderRegistry()
