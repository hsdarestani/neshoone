from __future__ import annotations

import pytest

from app.db import Database


@pytest.mark.asyncio
async def test_free_fortune_then_paid_refund(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.init()
    await db.upsert_user(101, "tester", "Test")

    free = await db.reserve_fortune(101, "انرژی امروز", "file-1", 10_000)
    assert free is not None
    assert free["is_free"] is True
    assert free["balance_after"] == 0
    await db.complete_fortune(free["id"], {"usable": True}, "result")

    no_credit = await db.reserve_fortune(101, "عشق", "file-2", 10_000)
    assert no_credit is None

    payment = await db.create_payment(101, 50_000)
    await db.attach_payment_track(payment["id"], "123456", 100)
    credit = await db.credit_verified_payment(
        payment_id=payment["id"],
        verified_amount_rial=500_000,
        zibal_result=100,
        ref_number="ref-1",
        card_number="****1234",
    )
    assert credit["ok"] is True
    assert credit["balance"] == 50_000

    duplicate = await db.credit_verified_payment(
        payment_id=payment["id"],
        verified_amount_rial=500_000,
        zibal_result=201,
        ref_number="ref-1",
        card_number="****1234",
    )
    assert duplicate["duplicate"] is True
    assert duplicate["balance"] == 50_000

    paid = await db.reserve_fortune(101, "عشق", "file-3", 10_000)
    assert paid is not None
    assert paid["is_free"] is False
    assert paid["balance_after"] == 40_000

    await db.fail_and_refund_fortune(paid["id"], "provider_error")
    user = await db.get_user(101)
    assert user is not None
    assert user["balance_toman"] == 50_000


@pytest.mark.asyncio
async def test_payment_amount_mismatch_does_not_credit(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.init()
    await db.upsert_user(202, None, "Another")
    payment = await db.create_payment(202, 100_000)
    await db.attach_payment_track(payment["id"], "987654", 100)

    result = await db.credit_verified_payment(
        payment_id=payment["id"],
        verified_amount_rial=999,
        zibal_result=100,
        ref_number=None,
        card_number=None,
    )
    assert result == {"ok": False, "reason": "amount_mismatch"}
    user = await db.get_user(202)
    assert user is not None
    assert user["balance_toman"] == 0
