"""Tests for the payment-method catalog (single source of truth for platforms)."""
from app.payment_methods import (
    PAYMENT_METHODS,
    PROVIDER_BANKS,
    account_label_for,
    deep_link_for,
    get_method,
    method_name,
    ussd_code_for,
)


def test_catalog_contains_expected_methods():
    codes = {
        "telebirr", "cbe", "cbebirr", "dashen", "awash", "boa",
        "mpesa", "zemen", "siinqee", "hellocash", "amole", "coop",
    }
    assert set(PAYMENT_METHODS) == codes


def test_every_method_has_display_name_and_deep_link():
    for code, method in PAYMENT_METHODS.items():
        assert method.name
        assert method.deep_link_url.startswith("https://")
        assert method.account_label
        assert method.kind in ("mobile", "bank")


def test_provider_verifiable_banks_mapped_in_catalog():
    assert PROVIDER_BANKS == {
        "cbe", "telebirr", "dashen", "awash", "boa", "cbebirr", "mpesa", "zemen", "siinqee",
    }
    for bank in PROVIDER_BANKS:
        assert bank in PAYMENT_METHODS


def test_legacy_telebirr_cbe_bank_codes_preserved():
    assert PAYMENT_METHODS["telebirr"].bank_code == 869
    assert PAYMENT_METHODS["cbe"].bank_code == 861


def test_ussd_codes_present_for_ussd_platforms():
    assert ussd_code_for("telebirr") == "*127#"
    assert ussd_code_for("cbe") == "*847#"
    assert ussd_code_for("dashen") == "*996#"
    assert ussd_code_for("awash") == "*901#"
    assert ussd_code_for("mpesa") == "*733#"
    # HelloCash has no single USSD code
    assert ussd_code_for("hellocash") == ""


def test_catalog_lookup_helpers():
    assert get_method("cbe").name == "CBE / Commercial Bank of Ethiopia"
    assert get_method("not_a_method") is None
    assert method_name("telebirr") == "Telebirr"
    assert method_name("nope") == "NOPE"
    assert account_label_for("mpesa") == "M-Pesa Phone"
    assert account_label_for("nope") == "Account"
    assert deep_link_for("telebirr") == "https://www.ethiotelecom.et/telebirr/"
