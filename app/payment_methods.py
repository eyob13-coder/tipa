"""Catalog of payment methods (mobile wallets and banks) creators can receive tips on.

Single source of truth for the display name, USSD dial code, deep link, and app
store links shown to tippers, plus whether any verification provider can confirm
a receipt for the method. New methods are added here and light up everywhere
(registration keyboard, tip instructions, Mini App, claim flow) automatically.

USSD codes should be treated as best-effort guidance; they change when banks
migrate mobile banking platforms.
"""
from dataclasses import dataclass
from typing import Dict, Optional

# Codes used by the verification providers (check.et / justverify). Anything not
# listed here still works end-to-end but falls back to creator approval because
# no provider can confirm its receipts.
PROVIDER_BANKS = {
    "cbe",
    "telebirr",
    "dashen",
    "awash",
    "boa",
    "cbebirr",
    "mpesa",
    "zemen",
    "siinqee",
}

MOBILE_METHODS = {"telebirr", "mpesa", "cbebirr", "hellocash", "amole"}


@dataclass(frozen=True)
class PaymentMethod:
    code: str
    name: str
    kind: str  # 'mobile' (phone number) | 'bank' (account number)
    ussd_code: Optional[str]
    deep_link_url: str
    bank_code: int = 0  # legacy Chapa-era bank code, kept for the creators.bank_code column
    android_url: Optional[str] = None
    ios_url: Optional[str] = None
    account_label: str = ""


PAYMENT_METHODS: Dict[str, PaymentMethod] = {
    "telebirr": PaymentMethod(
        code="telebirr",
        name="Telebirr",
        kind="mobile",
        ussd_code="*127#",
        deep_link_url="https://www.ethiotelecom.et/telebirr/",
        bank_code=869,
        android_url="https://play.google.com/store/apps/details?id=cn.tydic.ethiopay",
        ios_url="https://apps.apple.com/us/app/telebirr/id1553601084",
        account_label="Telebirr Phone",
    ),
    "cbe": PaymentMethod(
        code="cbe",
        name="CBE / Commercial Bank of Ethiopia",
        kind="bank",
        ussd_code="*847#",
        deep_link_url="https://www.combanketh.et/",
        bank_code=861,
        android_url="https://play.google.com/store/apps/details?id=prod.cbe.birr",
        ios_url="https://apps.apple.com/us/app/cbebirr-plus/id1600841787",
        account_label="CBE Account",
    ),
    "cbebirr": PaymentMethod(
        code="cbebirr",
        name="CBE Birr",
        kind="mobile",
        ussd_code="*847#",
        deep_link_url="https://www.combanketh.et/",
        account_label="CBE Birr Phone",
    ),
    "dashen": PaymentMethod(
        code="dashen",
        name="Dashen Bank",
        kind="bank",
        ussd_code="*996#",
        deep_link_url="https://www.dashenbanksc.com/",
        account_label="Dashen Account",
    ),
    "awash": PaymentMethod(
        code="awash",
        name="Awash Bank",
        kind="bank",
        ussd_code="*901#",
        deep_link_url="https://www.awashbank.com/",
        account_label="Awash Account",
    ),
    "boa": PaymentMethod(
        code="boa",
        name="Bank of Abyssinia",
        kind="bank",
        ussd_code="*815#",
        deep_link_url="https://www.bankofabyssinia.com/",
        account_label="BoA Account",
    ),
    "mpesa": PaymentMethod(
        code="mpesa",
        name="M-Pesa (Safaricom Ethiopia)",
        kind="mobile",
        ussd_code="*733#",
        deep_link_url="https://www.safaricom.et/",
        account_label="M-Pesa Phone",
    ),
    "zemen": PaymentMethod(
        code="zemen",
        name="Zemen Bank",
        kind="bank",
        ussd_code="*844#",
        deep_link_url="https://www.zemenbank.com/",
        account_label="Zemen Account",
    ),
    "siinqee": PaymentMethod(
        code="siinqee",
        name="Siinqee Bank",
        kind="bank",
        ussd_code="*871#",
        deep_link_url="https://www.siinqeebank.com/",
        account_label="Siinqee Account",
    ),
    "hellocash": PaymentMethod(
        code="hellocash",
        name="HelloCash",
        kind="mobile",
        ussd_code=None,  # HelloCash uses partner-bank USSD codes
        deep_link_url="https://www.hellocash.net/",
        account_label="HelloCash Phone",
    ),
    "amole": PaymentMethod(
        code="amole",
        name="Amole (Awash)",
        kind="mobile",
        ussd_code="*901#",
        deep_link_url="https://www.awashbank.com/",
        account_label="Amole Phone",
    ),
    "coop": PaymentMethod(
        code="coop",
        name="Cooperative Bank of Oromia",
        kind="bank",
        ussd_code="*896#",
        deep_link_url="https://www.coopbankoromia.com/",
        account_label="Coop Account",
    ),
}


def get_method(code: str) -> Optional[PaymentMethod]:
    return PAYMENT_METHODS.get(code)


def method_name(code: str) -> str:
    method = get_method(code)
    return method.name if method else code.upper()


def ussd_code_for(code: str) -> str:
    method = get_method(code)
    return method.ussd_code if method and method.ussd_code else ""


def deep_link_for(code: str) -> str:
    method = get_method(code)
    return method.deep_link_url if method else ""


def account_label_for(code: str) -> str:
    method = get_method(code)
    return method.account_label if method else "Account"
