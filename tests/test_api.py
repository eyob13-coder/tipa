import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.db.models import Creator
from conftest import make_init_data

client = TestClient(app)

INIT_DATA = make_init_data(settings.bot_token, user_id=777)
INIT_HEADERS = {"X-Telegram-Init-Data": INIT_DATA}


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
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    creator_id = str(creator.id)

    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        session_cm = AsyncMockSession(db_session)
        mock_session_local.return_value = session_cm

        response = client.get(f"/api/creator/{creator_id}", headers=INIT_HEADERS)
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

        response = client.post("/api/tip/initialize", json=payload, headers=INIT_HEADERS)
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
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    tip = Tip(
        creator_id=creator.id,
        tipper_display_name="Claim Tipper",
        amount=50.0,
        platform_fee=1.5,
        tx_ref="claim_tx",
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
            headers=INIT_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    await db_session.refresh(tip)
    assert tip.status == "pending_verification"
    assert tip.ref_id == "RECEIPT123"
    assert tip.claimed_at is not None


@pytest.mark.asyncio
async def test_miniapp_api_rejects_missing_init_data(db_session):
    if not settings.bot_token:
        pytest.skip("BOT_TOKEN unset: initData validation is disabled")

    creator = Creator(
        telegram_id=333444556,
        telegram_username="creator_noauth",
        display_name="Creator NoAuth",
        bank_code=861,
        payment_method="telebirr",
        account_number="0911666777",
        account_name="Creator NoAuth",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        session_cm = AsyncMockSession(db_session)
        mock_session_local.return_value = session_cm

        response = client.get(f"/api/creator/{creator.id}", headers={})
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_miniapp_api_rejects_tampered_init_data(db_session):
    if not settings.bot_token:
        pytest.skip("BOT_TOKEN unset: initData validation is disabled")

    creator = Creator(
        telegram_id=333444557,
        telegram_username="creator_tamper",
        display_name="Creator Tamper",
        bank_code=861,
        payment_method="cbe",
        account_number="1000555666777",
        account_name="Creator Tamper",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        session_cm = AsyncMockSession(db_session)
        mock_session_local.return_value = session_cm

        tampered = INIT_DATA.replace("auth_date", "auth_date2")
        response = client.get(
            f"/api/creator/{creator.id}",
            headers={"X-Telegram-Init-Data": tampered},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_miniapp_creator_csv_export(db_session):
    if not settings.bot_token:
        pytest.skip("BOT_TOKEN unset: initData validation is disabled")

    from app.db.models import Tip

    creator = Creator(
        telegram_id=777,
        telegram_username="creator_export",
        display_name="Creator Export",
        bank_code=861,
        payment_method="telebirr",
        account_number="0911888777",
        account_name="Creator Export",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    tip = Tip(
        creator_id=creator.id,
        tipper_display_name="Export Tipper",
        amount=50.0,
        platform_fee=1.5,
        tx_ref="export_tx",
        status="success",
        verification_method="check_et",
        ref_id="EXP123",
    )
    db_session.add(tip)
    await db_session.commit()

    headers = {"X-Telegram-Init-Data": make_init_data(settings.bot_token, user_id=777)}
    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        session_cm = AsyncMockSession(db_session)
        mock_session_local.return_value = session_cm

        response = client.get(f"/api/creator/{creator.id}/export", headers=headers)
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        body = response.text
        assert "date,tipper_name,amount_etb,note,verification_method,tx_ref" in body
        assert "Export Tipper" in body
        assert "check_et" in body


@pytest.mark.asyncio
async def test_miniapp_creator_csv_export_forbids_other_user(db_session):
    if not settings.bot_token:
        pytest.skip("BOT_TOKEN unset: initData validation is disabled")

    creator = Creator(
        telegram_id=999,
        telegram_username="creator_export_other",
        display_name="Creator Export Other",
        bank_code=861,
        payment_method="telebirr",
        account_number="0911777666",
        account_name="Creator Export Other",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    headers = {"X-Telegram-Init-Data": make_init_data(settings.bot_token, user_id=888)}
    with patch("app.api.routes.AsyncSessionLocal") as mock_session_local:
        session_cm = AsyncMockSession(db_session)
        mock_session_local.return_value = session_cm

        response = client.get(f"/api/creator/{creator.id}/export", headers=headers)
        assert response.status_code == 403
