"""Parse Ethiopian mobile-money / bank payment SMS messages.

Tippers can paste or forward the whole confirmation SMS instead of hunting
for the reference code. We extract the transaction reference (and amount
when present). Anything that doesn't match falls back to the existing
bare-code entry path.
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class ParsedSms:
    reference: str
    amount: Decimal | None
    provider: str


# Reference-code extraction: label-based ("Ref No: X", "Transaction Reference: X").
_REF_RE = re.compile(
    r"(?:transaction\s+)?(?:ref(?:erence)?|receipt(?:\s+no)?)(?:\s*(?:no|number|num|id))?"
    r"\s*[.:=>\-]+\s*([A-Za-z0-9][A-Za-z0-9\-]{5,23}[A-Za-z0-9])",
    re.IGNORECASE,
)

# Amount extraction: "500.00 ETB", "ETB 1,000", "100 birr".
_AMOUNT_AFTER = re.compile(r"([1-9][\d,]*(?:\.\d{1,2})?)\s*(?:ETB|birr)", re.IGNORECASE)
_AMOUNT_BEFORE = re.compile(r"(?:ETB)\s*[:\-]?\s*([1-9][\d,]*(?:\.\d{1,2})?)", re.IGNORECASE)

_PROVIDER_KEYWORDS = (
    ("telebirr", "telebirr"),
    ("cbe birr", "cbe"),
    ("commercial bank", "cbe"),
    ("awash", "awash"),
    ("dashen", "dashen"),
    ("abyssinia", "abyssinia"),
    ("amole", "dashen"),
    ("hellocash", "hellocash"),
    ("mpesa", "mpesa"),
)


def _detect_provider(text: str) -> str | None:
    lowered = text.lower()
    for keyword, provider in _PROVIDER_KEYWORDS:
        if keyword in lowered:
            return provider
    # Currency marker alone still signals a payment SMS even without brand name.
    if re.search(r"\bETB\b|\bbirr\b", text, re.IGNORECASE):
        return "generic"
    return None


def _parse_amount(text: str) -> Decimal | None:
    match = _AMOUNT_AFTER.search(text) or _AMOUNT_BEFORE.search(text)
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def parse_payment_sms(text: str) -> ParsedSms | None:
    """Extract {reference, amount, provider} from a payment SMS.

    Returns None when the text does not look like a transfer confirmation,
    so callers can fall back to treating input as a bare reference code.
    """
    if not text or len(text) < 15:
        return None

    provider = _detect_provider(text)
    ref_match = _REF_RE.search(text)

    # A labelled reference plus any currency/bank signal is enough. Without a
    # bank/currency signal we also require an amount so ordinary sentences
    # containing the word "receipt" are not misread as SMS claims.
    if not ref_match:
        return None
    if not provider and _parse_amount(text) is None:
        return None

    reference = ref_match.group(1).upper()
    return ParsedSms(reference=reference, amount=_parse_amount(text), provider=provider or "generic")
