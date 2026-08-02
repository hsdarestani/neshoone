from __future__ import annotations

import pytest

from app.admin import AdminService
from app.config import Settings
from app.db import Database


@pytest.mark.asyncio
async def test_first_admin_claim_and_signed_token(tmp_path):
    database_path = str(tmp_path / "neshoone.db")
    db = Database(database_path)
    await db.init()

    settings = Settings(
        BOT_TOKEN="test-bot-token",
        OPENAI_API_KEY="test-openai-key",
        ZIBAL_MERCHANT="test-merchant",
        DATABASE_PATH=database_path,
    )
    admin = AdminService(database_path, settings)
    await admin.init()

    allowed, claimed = await admin.claim_or_check(123456)
    assert allowed is True
    assert claimed is True

    allowed_again, claimed_again = await admin.claim_or_check(123456)
    assert allowed_again is True
    assert claimed_again is False

    second_allowed, second_claimed = await admin.claim_or_check(999999)
    assert second_allowed is False
    assert second_claimed is False

    token = admin.create_token(123456)
    assert await admin.validate_token(token) == 123456
