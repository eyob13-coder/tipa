import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.db.models import Creator, Tip


@pytest.mark.asyncio
async def test_chapa_webhook_flow(db_session):
    # Setup test creator and pending tip in DB
    creator = Creator(
        telegram_id=111222333,
        telegram_username="creator_webhook",
        display_name="Creator Webhook",
        bank_code=861,
        account_number="1000999888777",
        account_name="Creator Webhook",
        chapa_subaccount_id="ACCT_sub_webhook",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    tx_ref = "tipa_tx_ref_webhook_test"
    tip = Tip(
        creator_id=creator.id,
        tipper_telegram_id=444555666,
        tipper_display_name="Tipper Webhook",
        amount=25.0,
        platform_fee=2.0,
        chapa_tx_ref=tx_ref,
        status="pending",
    )
    db_session.add(tip)
    await db_session.commit()

    # Patch AsyncSessionLocal in webhook module to use our test DB session
    with patch("app.webhooks.chapa_webhook.AsyncSessionLocal") as mock_session_local, \
         patch("app.chapa.client.chapa_client.verify_transaction") as mock_verify, \
         patch("app.webhooks.chapa_webhook.notify_tip_success"):

        # Configure session mock context manager
        session_cm = AsyncMock()
        session_cm.__aenter__.return_value = db_session
        session_cm.__aexit__.return_value = None
        mock_session_local.return_value = session_cm

        mock_verify.return_value = {
            "status": "success",
            "reference": "CHAPA_REF_TEST_WEBHOOK",
            "amount": 25.0,
        }

        client = TestClient(app)
        response = client.get(f"/webhooks/chapa?trx_ref={tx_ref}&status=success")

        assert response.status_code == 200
        res_data = response.json()
        assert res_data.get("status") == "ok"

        # Verify tip status was updated in DB
        await db_session.refresh(tip)
        assert tip.status == "success"
        assert tip.chapa_ref_id == "CHAPA_REF_TEST_WEBHOOK"
        assert tip.verified_at is not None
