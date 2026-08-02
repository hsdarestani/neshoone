from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._write_lock = asyncio.Lock()

    async def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    balance_toman INTEGER NOT NULL DEFAULT 0 CHECK(balance_toman >= 0),
                    free_fortune_used INTEGER NOT NULL DEFAULT 0 CHECK(free_fortune_used IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS wallet_transactions (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    amount_toman INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reference_type TEXT,
                    reference_id TEXT,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
                );

                CREATE TABLE IF NOT EXISTS payments (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    amount_toman INTEGER NOT NULL,
                    amount_rial INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    track_id TEXT UNIQUE,
                    zibal_result INTEGER,
                    ref_number TEXT,
                    card_number TEXT,
                    created_at TEXT NOT NULL,
                    verified_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
                );

                CREATE TABLE IF NOT EXISTS fortunes (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    intention TEXT NOT NULL,
                    telegram_file_id TEXT NOT NULL,
                    is_free INTEGER NOT NULL CHECK(is_free IN (0,1)),
                    charged_amount_toman INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    result_text TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(telegram_id)
                );

                CREATE INDEX IF NOT EXISTS idx_wallet_user_created
                    ON wallet_transactions(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_payment_user_created
                    ON payments(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_fortune_user_created
                    ON fortunes(user_id, created_at DESC);
                """
            )
            await db.commit()

    async def upsert_user(self, telegram_id: int, username: str | None, first_name: str | None) -> None:
        now = utcnow()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO users(telegram_id, username, first_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    updated_at=excluded.updated_at
                """,
                (telegram_id, username, first_name, now, now),
            )
            await db.commit()

    async def get_user(self, telegram_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))).fetchone()
            return dict(row) if row else None

    async def reserve_fortune(
        self,
        user_id: int,
        intention: str,
        telegram_file_id: str,
        price_toman: int,
    ) -> dict[str, Any] | None:
        fortune_id = uuid.uuid4().hex
        now = utcnow()
        async with self._write_lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                user = await (await db.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,))).fetchone()
                if not user:
                    await db.rollback()
                    return None

                is_free = int(user["free_fortune_used"]) == 0
                charge = 0 if is_free else price_toman
                if not is_free and int(user["balance_toman"]) < price_toman:
                    await db.rollback()
                    return None

                if is_free:
                    await db.execute(
                        "UPDATE users SET free_fortune_used=1, updated_at=? WHERE telegram_id=?",
                        (now, user_id),
                    )
                    balance_after = int(user["balance_toman"])
                else:
                    balance_after = int(user["balance_toman"]) - price_toman
                    await db.execute(
                        "UPDATE users SET balance_toman=?, updated_at=? WHERE telegram_id=?",
                        (balance_after, now, user_id),
                    )
                    await db.execute(
                        """
                        INSERT INTO wallet_transactions(
                            id,user_id,kind,amount_toman,balance_after,status,
                            reference_type,reference_id,description,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            uuid.uuid4().hex,
                            user_id,
                            "fortune_debit",
                            -price_toman,
                            balance_after,
                            "completed",
                            "fortune",
                            fortune_id,
                            "هزینه دریافت فال تصویری",
                            now,
                        ),
                    )

                await db.execute(
                    """
                    INSERT INTO fortunes(
                        id,user_id,intention,telegram_file_id,is_free,
                        charged_amount_toman,status,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (fortune_id, user_id, intention, telegram_file_id, int(is_free), charge, "processing", now),
                )
                await db.commit()
                return {
                    "id": fortune_id,
                    "is_free": is_free,
                    "charged_amount_toman": charge,
                    "balance_after": balance_after,
                }

    async def complete_fortune(self, fortune_id: str, result: dict[str, Any], result_text: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE fortunes
                SET status='completed', result_json=?, result_text=?, completed_at=?
                WHERE id=? AND status='processing'
                """,
                (json.dumps(result, ensure_ascii=False), result_text, utcnow(), fortune_id),
            )
            await db.commit()

    async def fail_and_refund_fortune(self, fortune_id: str, reason: str) -> None:
        now = utcnow()
        async with self._write_lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                fortune = await (
                    await db.execute("SELECT * FROM fortunes WHERE id=?", (fortune_id,))
                ).fetchone()
                if not fortune or fortune["status"] != "processing":
                    await db.rollback()
                    return

                user = await (
                    await db.execute("SELECT * FROM users WHERE telegram_id=?", (fortune["user_id"],))
                ).fetchone()
                if not user:
                    await db.rollback()
                    return

                if int(fortune["is_free"]) == 1:
                    await db.execute(
                        "UPDATE users SET free_fortune_used=0, updated_at=? WHERE telegram_id=?",
                        (now, fortune["user_id"]),
                    )
                else:
                    amount = int(fortune["charged_amount_toman"])
                    new_balance = int(user["balance_toman"]) + amount
                    await db.execute(
                        "UPDATE users SET balance_toman=?, updated_at=? WHERE telegram_id=?",
                        (new_balance, now, fortune["user_id"]),
                    )
                    await db.execute(
                        """
                        INSERT INTO wallet_transactions(
                            id,user_id,kind,amount_toman,balance_after,status,
                            reference_type,reference_id,description,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            uuid.uuid4().hex,
                            fortune["user_id"],
                            "fortune_refund",
                            amount,
                            new_balance,
                            "completed",
                            "fortune",
                            fortune_id,
                            "بازگشت هزینه به دلیل خطای پردازش",
                            now,
                        ),
                    )

                await db.execute(
                    "UPDATE fortunes SET status='failed', error_message=?, completed_at=? WHERE id=?",
                    (reason[:1000], now, fortune_id),
                )
                await db.commit()

    async def recent_fortunes(self, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT id,intention,is_free,charged_amount_toman,status,result_text,created_at
                    FROM fortunes WHERE user_id=? AND status='completed'
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (user_id, limit),
                )
            ).fetchall()
            return [dict(row) for row in rows]

    async def create_payment(self, user_id: int, amount_toman: int) -> dict[str, Any]:
        payment_id = uuid.uuid4().hex
        payment = {
            "id": payment_id,
            "user_id": user_id,
            "amount_toman": amount_toman,
            "amount_rial": amount_toman * 10,
            "status": "pending",
            "created_at": utcnow(),
        }
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO payments(id,user_id,amount_toman,amount_rial,status,created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    payment["id"], payment["user_id"], payment["amount_toman"],
                    payment["amount_rial"], payment["status"], payment["created_at"],
                ),
            )
            await db.commit()
        return payment

    async def attach_payment_track(self, payment_id: str, track_id: str, zibal_result: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE payments SET track_id=?, zibal_result=? WHERE id=? AND status='pending'",
                (track_id, zibal_result, payment_id),
            )
            await db.commit()

    async def mark_payment_failed(self, payment_id: str, zibal_result: int | None = None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE payments SET status='failed', zibal_result=COALESCE(?,zibal_result) WHERE id=? AND status='pending'",
                (zibal_result, payment_id),
            )
            await db.commit()

    async def get_payment(self, track_id: str | None, order_id: str | None) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if track_id:
                row = await (
                    await db.execute("SELECT * FROM payments WHERE track_id=?", (track_id,))
                ).fetchone()
                if row:
                    return dict(row)
            if order_id:
                row = await (
                    await db.execute("SELECT * FROM payments WHERE id=?", (order_id,))
                ).fetchone()
                if row:
                    return dict(row)
            return None

    async def credit_verified_payment(
        self,
        payment_id: str,
        verified_amount_rial: int,
        zibal_result: int,
        ref_number: str | None,
        card_number: str | None,
    ) -> dict[str, Any]:
        now = utcnow()
        async with self._write_lock:
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("BEGIN IMMEDIATE")
                payment = await (
                    await db.execute("SELECT * FROM payments WHERE id=?", (payment_id,))
                ).fetchone()
                if not payment:
                    await db.rollback()
                    return {"ok": False, "reason": "payment_not_found"}
                if payment["status"] == "paid":
                    user = await (
                        await db.execute("SELECT balance_toman FROM users WHERE telegram_id=?", (payment["user_id"],))
                    ).fetchone()
                    await db.rollback()
                    return {"ok": True, "duplicate": True, "balance": int(user["balance_toman"]) if user else 0}
                if payment["status"] != "pending":
                    await db.rollback()
                    return {"ok": False, "reason": "invalid_status"}
                if int(payment["amount_rial"]) != int(verified_amount_rial):
                    await db.rollback()
                    return {"ok": False, "reason": "amount_mismatch"}

                user = await (
                    await db.execute("SELECT * FROM users WHERE telegram_id=?", (payment["user_id"],))
                ).fetchone()
                if not user:
                    await db.rollback()
                    return {"ok": False, "reason": "user_not_found"}

                new_balance = int(user["balance_toman"]) + int(payment["amount_toman"])
                await db.execute(
                    "UPDATE users SET balance_toman=?, updated_at=? WHERE telegram_id=?",
                    (new_balance, now, payment["user_id"]),
                )
                await db.execute(
                    """
                    UPDATE payments SET status='paid', zibal_result=?, ref_number=?, card_number=?, verified_at=?
                    WHERE id=?
                    """,
                    (zibal_result, ref_number, card_number, now, payment_id),
                )
                await db.execute(
                    """
                    INSERT INTO wallet_transactions(
                        id,user_id,kind,amount_toman,balance_after,status,
                        reference_type,reference_id,description,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        uuid.uuid4().hex,
                        payment["user_id"],
                        "deposit",
                        int(payment["amount_toman"]),
                        new_balance,
                        "completed",
                        "payment",
                        payment_id,
                        "شارژ کیف پول از طریق زیبال",
                        now,
                    ),
                )
                await db.commit()
                return {
                    "ok": True,
                    "duplicate": False,
                    "balance": new_balance,
                    "user_id": int(payment["user_id"]),
                    "amount_toman": int(payment["amount_toman"]),
                }
