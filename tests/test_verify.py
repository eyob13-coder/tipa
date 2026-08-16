import pytest
from unittest.mock import MagicMock, patch
from decimal import Decimal

from app.db.models import Creator, Tip
from app.verify.base import VerificationError, VerifyResult, VerificationProvider
from app.verify.providers.verify_et import VerifyEtProvider
from app.verify.providers.check_et import CheckEtProvider
from app.verify.providers.justverify import JustVerifyProvider
from app.verify.registry import ProviderRegistry, BANK_PRIORITY
from app.verify.service import auto_verify_tip


class _FakeProvider(VerificationProvider):
    """Scripted provider for registry failover tests."""

    def __init__(self, name, enabled=True, banks=("cbe", "telebirr"), results=None, error=None):
        self.name = name
        self._enabled = enabled
        self.supported_banks = banks
        self.results = results or []
        self.error = error
        self.calls = []

    @property
    def enabled(self):
        return self._enabled

    async def verify_payment(self, bank, reference, account_number=None, idempotency_key=None):
        self.calls.append((bank, reference))
        if self.error:
            raise self.error
        if self.results:
            return self.results.pop(0)
        return VerifyResult()


# ---------------------------------------------------------------------------
# VerifyEtProvider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_et_cbe_completed_200():
    provider = VerifyEtProvider(api_key="VERIFY_BANK_ET_test_key", base_url="https://verify.et")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "requestId": "req-123",
            "verification": {
                "status": "success",
                "verified": True,
                "amount": 50.0,
                "message": "Payment verified",
            },
        }
        mock_post.return_value = mock_resp

        result = await provider.verify_payment(
            bank="cbe",
            reference="TXN999",
            account_number="1000123498765432",
            idempotency_key="idem-1",
        )

    assert result.request_success is True
    assert result.verified is True
    assert result.status == "success"
    assert result.amount == 50.0
    assert result.conclusive is True

    call_kwargs = mock_post.call_args
    sent_payload = call_kwargs.kwargs["json"]
    sent_headers = call_kwargs.kwargs["headers"]
    assert sent_payload["bank"] == "cbe"
    assert sent_payload["referenceNumber"] == "TXN999"
    assert sent_payload["accountSuffix"] == "98765432"
    assert sent_headers["x-api-key"] == "VERIFY_BANK_ET_test_key"
    assert sent_headers["Idempotency-Key"] == "idem-1"


@pytest.mark.asyncio
async def test_verify_et_telebirr_uses_transaction_number():
    provider = VerifyEtProvider(api_key="VERIFY_BANK_ET_test_key")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "requestId": "req-tele",
            "verification": {"status": "success", "verified": True, "amount": 25.0},
        }
        mock_post.return_value = mock_resp

        result = await provider.verify_payment(bank="telebirr", reference="TX5566", idempotency_key="idem-2")

    assert result.verified is True
    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["bank"] == "telebirr"
    assert sent_payload["transactionNumber"] == "TX5566"
    assert "accountSuffix" not in sent_payload


@pytest.mark.asyncio
async def test_verify_et_202_polls_until_completed():
    provider = VerifyEtProvider(api_key="VERIFY_BANK_ET_test_key", base_url="https://verify.et")

    queued_resp = MagicMock()
    queued_resp.status_code = 202
    queued_resp.json.return_value = {"requestId": "req-poll", "statusUrl": "https://verify.et/api/verify/req-poll"}

    completed_resp = MagicMock()
    completed_resp.status_code = 200
    completed_resp.json.return_value = {
        "data": {"processingStatus": "completed", "status": "success", "verified": True, "amount": 10.0}
    }

    with patch("httpx.AsyncClient.post", return_value=queued_resp), \
         patch("httpx.AsyncClient.get", return_value=completed_resp) as mock_get, \
         patch("app.verify.providers.verify_et.asyncio.sleep") as mock_sleep:
        result = await provider.verify_payment(bank="cbe", reference="TXN556", idempotency_key="idem-3")

    assert result.verified is True
    assert result.amount == 10.0
    assert result.status == "success"
    mock_get.assert_called_once()
    mock_sleep.assert_awaited()


@pytest.mark.asyncio
async def test_verify_et_disabled_raises():
    provider = VerifyEtProvider(api_key="")
    with pytest.raises(VerificationError):
        await provider.verify_payment(bank="cbe", reference="TXN")


@pytest.mark.asyncio
async def test_verify_et_http_error_raises_verification_error():
    provider = VerifyEtProvider(api_key="VERIFY_BANK_ET_test_key")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "server error"
        mock_post.return_value = mock_resp

        with pytest.raises(VerificationError):
            await provider.verify_payment(bank="cbe", reference="TXN")


# ---------------------------------------------------------------------------
# CheckEtProvider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_et_cbe_success():
    provider = CheckEtProvider(api_key="check_key", base_url="https://api.check.et/api/v1")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "data": {
                "verification_id": "v-42",
                "receipt": {"status": "success", "amount": "50.00"},
            },
        }
        mock_post.return_value = mock_resp

        result = await provider.verify_payment(
            bank="cbe",
            reference="TXN111",
            account_number="1000123498765432",
            idempotency_key="idem-4",
        )

    assert result.verified is True
    assert result.status == "success"
    assert result.amount == 50.0
    assert result.conclusive is True

    call_kwargs = mock_post.call_args
    sent_payload = call_kwargs.kwargs["json"]
    sent_headers = call_kwargs.kwargs["headers"]
    assert sent_payload["bank"] == "cbe"
    assert sent_payload["transaction_number"] == "TXN111"
    assert sent_payload["account_number"] == "1000123498765432"
    assert sent_headers["Authorization"] == "Bearer check_key"


@pytest.mark.asyncio
async def test_check_et_not_found_is_conclusive():
    provider = CheckEtProvider(api_key="check_key")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"message": "Transaction not found."}
        mock_post.return_value = mock_resp

        result = await provider.verify_payment(bank="telebirr", reference="TX404")

    assert result.verified is False
    assert result.status == "not_found"
    assert result.conclusive is True


@pytest.mark.asyncio
async def test_check_et_http_error_raises():
    provider = CheckEtProvider(api_key="check_key")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "unauthorized"
        mock_post.return_value = mock_resp

        with pytest.raises(VerificationError):
            await provider.verify_payment(bank="cbe", reference="TXN")


# ---------------------------------------------------------------------------
# JustVerifyProvider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_justverify_completed_success():
    provider = JustVerifyProvider(api_key="jv_key", base_url="https://justverify.et")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "provider": "cbe",
            "amount": 30.0,
            "currency": "ETB",
            "status": "completed",
        }
        mock_post.return_value = mock_resp

        result = await provider.verify_payment(bank="cbe", reference="TXN222", idempotency_key="idem-5")

    assert result.verified is True
    assert result.status == "success"
    assert result.amount == 30.0
    assert result.conclusive is True

    sent_payload = mock_post.call_args.kwargs["json"]
    sent_headers = mock_post.call_args.kwargs["headers"]
    assert sent_payload["provider"] == "cbe"
    assert sent_payload["reference"] == "TXN222"
    assert sent_headers["Authorization"] == "Bearer jv_key"


@pytest.mark.asyncio
async def test_justverify_non_completed_not_verified():
    provider = JustVerifyProvider(api_key="jv_key")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "status": "failed", "amount": 0}
        mock_post.return_value = mock_resp

        result = await provider.verify_payment(bank="cbe", reference="TXN333")

    assert result.verified is False
    assert result.status == "failed"
    assert result.conclusive is True


@pytest.mark.asyncio
async def test_justverify_http_error_raises():
    provider = JustVerifyProvider(api_key="jv_key")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "boom"
        mock_post.return_value = mock_resp

        with pytest.raises(VerificationError):
            await provider.verify_payment(bank="cbe", reference="TXN")


# ---------------------------------------------------------------------------
# ProviderRegistry failover
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registry_fails_over_on_error():
    ok = _FakeProvider(
        "check_et",
        results=[VerifyResult(request_success=True, verified=True, status="success", amount=50.0)],
    )
    broken = _FakeProvider("verify_et", error=VerificationError("timeout"))
    registry = ProviderRegistry(providers=[broken, ok], priority={"cbe": ("verify_et", "check_et")})

    result = await registry.verify(bank="cbe", reference="TXN", idempotency_key="idem")

    assert result.verified is True
    assert result.provider == "check_et"
    assert len(broken.calls) == 1
    assert len(ok.calls) == 1


@pytest.mark.asyncio
async def test_registry_stops_on_conclusive_negative():
    not_found = _FakeProvider(
        "check_et",
        results=[VerifyResult(request_success=True, verified=False, status="not_found")],
    )
    would_pass = _FakeProvider(
        "verify_et",
        results=[VerifyResult(request_success=True, verified=True, status="success", amount=50.0)],
    )
    registry = ProviderRegistry(providers=[not_found, would_pass], priority={"cbe": ("check_et", "verify_et")})

    result = await registry.verify(bank="cbe", reference="TXN", idempotency_key="idem")

    assert result.status == "not_found"
    assert result.verified is False
    assert result.provider == "check_et"
    assert len(would_pass.calls) == 0


@pytest.mark.asyncio
async def test_registry_returns_first_attempt_when_nothing_conclusive():
    pending = _FakeProvider("check_et", results=[VerifyResult(request_success=True, status="pending")])
    broken = _FakeProvider("verify_et", error=VerificationError("down"))
    registry = ProviderRegistry(providers=[pending, broken], priority={"cbe": ("check_et", "verify_et")})

    result = await registry.verify(bank="cbe", reference="TXN", idempotency_key="idem")

    assert result.status == "pending"
    assert result.provider == "check_et"


@pytest.mark.asyncio
async def test_registry_no_provider_available():
    registry = ProviderRegistry(providers=[_FakeProvider("check_et", enabled=False)])
    result = await registry.verify(bank="cbe", reference="TXN", idempotency_key="idem")

    assert result.request_success is False
    assert result.message == "No verification provider available"


@pytest.mark.asyncio
async def test_registry_skips_unsupported_bank_for_provider():
    cbe_only = _FakeProvider("check_et", banks=("cbe",))
    tele = _FakeProvider(
        "justverify",
        results=[VerifyResult(request_success=True, verified=True, status="success", amount=10.0)],
    )
    registry = ProviderRegistry(providers=[cbe_only, tele], priority={"telebirr": ("check_et", "justverify")})

    result = await registry.verify(bank="telebirr", reference="TXN", idempotency_key="idem")

    assert result.provider == "justverify"
    assert len(cbe_only.calls) == 0


def test_bank_priority_leads_with_reliable_provider():
    assert BANK_PRIORITY["cbe"][0] == "verify_et"
    assert BANK_PRIORITY["telebirr"][0] == "check_et"


def test_bank_priority_covers_all_provider_banks():
    from app.payment_methods import PROVIDER_BANKS

    for bank in PROVIDER_BANKS:
        assert bank in BANK_PRIORITY, f"missing BANK_PRIORITY entry for {bank}"
    for bank in ("dashen", "awash", "boa", "cbebirr", "mpesa", "zemen", "siinqee"):
        assert BANK_PRIORITY[bank][0] == "check_et"
        assert "justverify" in BANK_PRIORITY[bank]


# ---------------------------------------------------------------------------
# auto_verify_tip service
# ---------------------------------------------------------------------------

async def _make_creator(session, method="cbe"):
    creator = Creator(
        telegram_id=424242,
        telegram_username="verify_creator",
        display_name="Verify Creator",
        bank_code=861,
        payment_method=method,
        account_number="1000123498765432",
        account_name="Verify Creator",
    )
    session.add(creator)
    await session.commit()
    await session.refresh(creator)
    return creator


async def _make_tip(session, creator, amount=50.0):
    tip = Tip(
        creator_id=creator.id,
        tipper_display_name="Tipper",
        amount=Decimal(str(amount)),
        platform_fee=Decimal("2.00"),
        tx_ref="tipa_tx_verify",
        status="pending",
    )
    session.add(tip)
    await session.commit()
    await session.refresh(tip)
    return tip


class _FakeRegistry:
    def __init__(self, result=None, error=None, enabled=True):
        self._result = result
        self._error = error
        self.enabled_providers = [object()] if enabled else []
        self.last_kwargs = None

    async def verify(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error:
            raise self._error
        result = self._result
        result.provider = result.provider or "check_et"
        return result


@pytest.mark.asyncio
async def test_auto_verify_returns_none_when_disabled(db_session):
    creator = await _make_creator(db_session, method="cbe")
    tip = await _make_tip(db_session, creator)

    with patch("app.verify.service.verify_registry", _FakeRegistry(enabled=False)):
        result = await auto_verify_tip(db_session, tip, creator, "TXN999")

    assert result is None
    assert tip.status == "pending"


@pytest.mark.asyncio
async def test_auto_verify_marks_tip_success_on_match(db_session):
    creator = await _make_creator(db_session, method="cbe")
    tip = await _make_tip(db_session, creator, amount=50.0)

    fake = _FakeRegistry(
        result=VerifyResult(request_success=True, verified=True, status="success", amount=50.0)
    )
    with patch("app.verify.service.verify_registry", fake):
        result = await auto_verify_tip(db_session, tip, creator, "TXN999")

    assert result.verified is True
    assert tip.status == "success"
    assert tip.verification_method == "check_et"
    assert float(tip.verified_amount) == 50.0
    assert tip.verified_at is not None

    assert fake.last_kwargs["bank"] == "cbe"
    assert fake.last_kwargs["reference"] == "TXN999"
    assert fake.last_kwargs["account_number"] == "1000123498765432"


@pytest.mark.asyncio
async def test_auto_verify_amount_mismatch_not_success(db_session):
    creator = await _make_creator(db_session, method="cbe")
    tip = await _make_tip(db_session, creator, amount=50.0)

    fake = _FakeRegistry(
        result=VerifyResult(request_success=True, verified=True, status="success", amount=49.0)
    )
    with patch("app.verify.service.verify_registry", fake):
        result = await auto_verify_tip(db_session, tip, creator, "TXN999")

    assert result.verified is True
    assert tip.status == "pending"


@pytest.mark.asyncio
async def test_auto_verify_error_returns_request_success_false(db_session):
    creator = await _make_creator(db_session, method="cbe")
    tip = await _make_tip(db_session, creator, amount=50.0)

    fake = _FakeRegistry(error=VerificationError("boom"))
    with patch("app.verify.service.verify_registry", fake):
        result = await auto_verify_tip(db_session, tip, creator, "TXN999")

    assert result.request_success is False
    assert result.verified is False
    assert tip.status == "pending"
