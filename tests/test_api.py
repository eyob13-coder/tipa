import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.db.models import Creator

client = TestClient(app)


class AsyncMockSession:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.mark.asyncio
async def test_miniapp_creator_api(db_session):
    creator = Creator(
        telegram_id=999888777,
        telegram_username="miniapp_creator",
        display_name="MiniApp Creator",
        bank_code=869,
        payment_method="telebirr",
        account_number="0911998877",
        account_name="MiniApp Creator Name",
        chapa_subaccount_id="manual_999888777",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    creator_id = str(creator.id)

    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        session_cm = AsyncMockSession(db_session)
        mock_session_local.return_value = session_cm

        response = client.get(f"/api/creator/{creator_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "MiniApp Creator"
        assert data["payment_method"] == "telebirr"
        assert data["account_number"] == "0911998877"


@pytest.mark.asyncio
async def test_miniapp_tip_initialize_api(db_session):
    creator = Creator(
        telegram_id=555666777,
        telegram_username="creator_init",
        display_name="Creator Init",
        bank_code=861,
        payment_method="cbe",
        account_number="1000555666777",
        account_name="Creator Init",
        chapa_subaccount_id="manual_555666777",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    payload = {
        "creator_id": str(creator.id),
        "amount": 100.0,
        "note": "MiniApp tip test",
        "tipper_display_name": "MiniApp Tipper",
    }

    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        session_cm = AsyncMockSession(db_session)
        mock_session_local.return_value = session_cm

        response = client.post("/api/tip/initialize", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["amount"] == 100.0
        assert data["payment_method"] == "cbe"
        assert data["account_number"] == "1000555666777"


@pytest.mark.asyncio
async def test_miniapp_tip_claim_sets_claimed_at(db_session):
    from app.db.models import Tip

    creator = Creator(
        telegram_id=333444555,
        telegram_username="creator_claim",
        display_name="Creator Claim",
        bank_code=861,
        payment_method="telebirr",
        account_number="0911666777",
        account_name="Creator Claim",
        chapa_subaccount_id="manual_333444555",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    tip = Tip(
        creator_id=creator.id,
        tipper_display_name="Claim Tipper",
        amount=50.0,
        platform_fee=1.5,
        chapa_tx_ref="claim_tx",
        status="pending",
    )
    db_session.add(tip)
    await db_session.commit()
    await db_session.refresh(tip)

    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        session_cm = AsyncMockSession(db_session)
        mock_session_local.return_value = session_cm

        response = client.post(
            "/api/tip/claim",
            json={"tip_id": str(tip.id), "ref_code": "RECEIPT123"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    await db_session.refresh(tip)
    assert tip.status == "pending_verification"
    assert tip.chapa_ref_id == "RECEIPT123"
    assert tip.claimed_at is not None
