from decimal import Decimal
import pytest
from sqlalchemy import select

from app.db.models import Creator, Tip


@pytest.mark.asyncio
async def test_create_creator_and_tip(db_session):
    creator = Creator(
        telegram_id=123456789,
        telegram_username="testcreator",
        display_name="Test Creator",
        bank_code=861,
        account_number="1000123456789",
        account_name="Test Creator Name",
        chapa_subaccount_id="ACCT_sub_123456",
    )
    db_session.add(creator)
    await db_session.commit()
    await db_session.refresh(creator)

    creator_id = creator.id
    assert creator_id is not None
    assert creator.telegram_id == 123456789

    tip = Tip(
        creator_id=creator_id,
        tipper_telegram_id=987654321,
        tipper_display_name="Amanuel",
        amount=Decimal("50.00"),
        platform_fee=Decimal("2.00"),
        chapa_tx_ref="tipa_tx_test_123",
        status="pending",
        note="Keep up the awesome tech content! 🔥",
    )
    db_session.add(tip)
    await db_session.commit()
    await db_session.refresh(tip)

    assert tip.id is not None
    assert tip.creator_id == creator_id
    assert tip.status == "pending"
    assert tip.note == "Keep up the awesome tech content! 🔥"

    # Query back creator from DB
    stmt = select(Creator).where(Creator.id == creator_id)
    res = await db_session.execute(stmt)
    c_fetched = res.scalar_one()
    assert c_fetched.telegram_id == 123456789

    # Query back tips from DB
    stmt_tips = select(Tip).where(Tip.creator_id == creator_id)
    tips_res = await db_session.execute(stmt_tips)
    tips = tips_res.scalars().all()
    assert len(tips) == 1
    assert tips[0].amount == Decimal("50.00")
    assert tips[0].note == "Keep up the awesome tech content! 🔥"
