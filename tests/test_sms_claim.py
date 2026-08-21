"""Tests for SMS-forward claiming (#5): parse_payment_sms + handler resolution."""
import pytest

from app.sms_parse import parse_payment_sms

TELEBIRR_SMS = (
    "You have received 500.00 ETB from +251911****23. "
    "Transaction Reference: CBETR123456789012. Your Telebirr balance is 2,300.00 ETB."
)
CBE_SMS = (
    "CBE Birr: You have credited with ETB 1,000.00 from CHALA BEKELE. "
    "Reference No: FT2515AB12CD. Thank you for banking with us."
)
AWASH_SMS = (
    "Dear customer, your account is credited with ETB 750 birr. Ref Number: AW987654321."
)


@pytest.mark.parametrize(
    ("text", "expected_ref"),
    [
        (TELEBIRR_SMS, "CBETR123456789012"),
        (CBE_SMS, "FT2515AB12CD"),
        (AWASH_SMS, "AW987654321"),
        # lowercase sms, uppercase-normalised ref
        (TELEBIRR_SMS.lower(), "CBETR123456789012"),
    ],
)
def test_parses_reference_from_common_formats(text, expected_ref):
    parsed = parse_payment_sms(text)
    assert parsed is not None
    assert parsed.reference == expected_ref


def test_parses_amounts():
    tele = parse_payment_sms(TELEBIRR_SMS)
    assert tele.amount is not None and str(tele.amount) == "500.00"

    cbe = parse_payment_sms(CBE_SMS)
    assert cbe.amount == 1000

    awash = parse_payment_sms(AWASH_SMS)
    assert awash.amount == 750


def test_provider_detection():
    assert parse_payment_sms(TELEBIRR_SMS).provider == "telebirr"
    assert parse_payment_sms(CBE_SMS).provider == "cbe"
    assert parse_payment_sms("Received ETB 50. Receipt No: XY123456").provider == "generic"


def test_bare_code_is_not_sms():
    # Tipper typing the code alone must NOT be treated as SMS text.
    assert parse_payment_sms("TLB12345678") is None
    assert parse_payment_sms("") is None
    assert parse_payment_sms("short") is None


def test_ordinary_sentence_not_misread():
    assert (
        parse_payment_sms(
            "Hey I sent it just now, check your receipt tomorrow morning please!"
        )
        is None
    )


def test_labelled_ref_without_bank_or_amount_rejected():
    # Has a label but no currency/bank keyword and no amount -> not an SMS.
    assert parse_payment_sms("my reference number is ABC123456 ok") is None


def test_handler_resolver_falls_back_to_bare_code():
    from app.bot.handlers import _resolve_claim_reference

    reference, from_sms = _resolve_claim_reference("TLB12345678")
    assert (reference, from_sms) == ("TLB12345678", False)


def test_handler_resolver_detects_forwarded_sms():
    from app.bot.handlers import _resolve_claim_reference

    reference, from_sms = _resolve_claim_reference(TELEBIRR_SMS)
    assert from_sms is True
    assert reference == "CBETR123456789012"
