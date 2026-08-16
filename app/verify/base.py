"""Shared verification primitives: normalized result, errors, and the provider interface."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


class VerificationError(Exception):
    """Raised when a verification provider cannot be reached or returns a broken response."""


@dataclass
class VerifyResult:
    """Normalized outcome of a provider payment check."""

    request_success: bool = False
    verified: bool = False
    status: str = "unknown"  # success | failed | not_found | pending | unknown
    amount: float | None = None
    request_id: str | None = None
    message: str = ""
    provider: str = ""

    @property
    def conclusive(self) -> bool:
        """True when the provider returned a terminal answer we can trust.

        ``success`` confirms the transfer. ``failed`` / ``not_found`` are
        definitive negatives — a provider that cannot find the transaction in
        the bank's records is authoritative enough to stop the failover chain.
        """
        return self.status in ("success", "failed", "not_found")


class VerificationProvider(ABC):
    """Adapter interface every verification provider must implement."""

    name: str = ""
    supported_banks: tuple = ("cbe", "telebirr")

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """False when the provider has no API key configured."""

    @abstractmethod
    async def verify_payment(
        self,
        bank: str,
        reference: str,
        account_number: str | None = None,
        idempotency_key: str | None = None,
    ) -> VerifyResult:
        """Ask the provider to confirm a transfer by its SMS/receipt reference.

        Raises ``VerificationError`` when the provider cannot be reached or
        answers with something unexpected — the caller treats that as a reason
        to fail over to the next provider, never as proof a payment failed.
        """
