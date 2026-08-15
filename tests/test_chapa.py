import pytest
from unittest.mock import patch, MagicMock
from app.chapa.client import ChapaClient


@pytest.mark.asyncio
async def test_chapa_list_banks():
    client = ChapaClient(secret_key="CHASECK_TEST-mock")

    mock_banks = [
        {"id": 861, "name": "Commercial Bank of Ethiopia", "code": "861"},
        {"id": 858, "name": "Awash Bank", "code": "858"},
    ]

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "data": mock_banks}
        mock_get.return_value = mock_resp

        banks = await client.list_banks(force_refresh=True)
        assert len(banks) == 2
        assert banks[0]["name"] == "Commercial Bank of Ethiopia"


@pytest.mark.asyncio
async def test_chapa_create_subaccount():
    client = ChapaClient(secret_key="CHASECK_TEST-mock")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "message": "Subaccount created successfully",
            "data": "ACCT_sub_mock_9999",
        }
        mock_post.return_value = mock_resp

        sub_id = await client.create_subaccount(
            account_name="Amanuel Worku",
            bank_code=861,
            account_number="1000111222333",
            split_value=2.0,
        )

        assert sub_id == "ACCT_sub_mock_9999"


@pytest.mark.asyncio
async def test_chapa_initialize_transaction():
    client = ChapaClient(secret_key="CHASECK_TEST-mock")

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "message": "Hosted Link",
            "data": {
                "checkout_url": "https://checkout.chapa.co/checkout/payment/mock123"
            },
        }
        mock_post.return_value = mock_resp

        checkout_url = await client.initialize_transaction(
            amount=50.0,
            creator_name="Amanuel Worku",
            subaccount_id="ACCT_sub_mock_9999",
            tx_ref="tipa_tx_ref_123",
        )

        assert checkout_url == "https://checkout.chapa.co/checkout/payment/mock123"


@pytest.mark.asyncio
async def test_chapa_verify_transaction():
    client = ChapaClient(secret_key="CHASECK_TEST-mock")

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "message": "Payment details",
            "data": {
                "status": "success",
                "reference": "CHAPA_REF_888",
                "amount": 50,
                "currency": "ETB",
            },
        }
        mock_get.return_value = mock_resp

        res = await client.verify_transaction("tipa_tx_ref_123")
        assert res.get("status") == "success"
        assert res.get("reference") == "CHAPA_REF_888"
