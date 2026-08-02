from __future__ import annotations

import hashlib
import hmac
import io
import json
import time
from html import escape
from typing import Any
from urllib.parse import quote

import aiosqlite
from aiogram import Bot
from aiogram.types import User
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse

from app.config import Settings


class AdminService:
    def __init__(self, database_path: str, settings: Settings) -> None:
        self.database_path = database_path
        self.settings = settings
        self._signing_key = hashlib.sha256(
            f"neshoone-admin:{settings.bot_token.get_secret_value()}".encode()
        ).digest()

    async def init(self) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    telegram_id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS admin_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fortune_id TEXT NOT NULL,
                    admin_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    telegram_message_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(fortune_id, admin_id, kind),
                    FOREIGN KEY(fortune_id) REFERENCES fortunes(id),
                    FOREIGN KEY(admin_id) REFERENCES admins(telegram_id)
                );

                CREATE INDEX IF NOT EXISTS idx_admin_notifications_fortune
                    ON admin_notifications(fortune_id, created_at DESC);
                """
            )
            await db.commit()

    async def claim_or_check(self, telegram_id: int) -> tuple[bool, bool]:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute("SELECT telegram_id FROM admins WHERE telegram_id=?", (telegram_id,))
            ).fetchone()
            if existing:
                await db.commit()
                return True, False
            count_row = await (await db.execute("SELECT COUNT(*) AS count FROM admins")).fetchone()
            if int(count_row["count"]) == 0:
                await db.execute("INSERT INTO admins(telegram_id) VALUES(?)", (telegram_id,))
                await db.commit()
                return True, True
            await db.rollback()
            return False, False

    async def is_admin(self, telegram_id: int) -> bool:
        async with aiosqlite.connect(self.database_path) as db:
            row = await (
                await db.execute("SELECT 1 FROM admins WHERE telegram_id=?", (telegram_id,))
            ).fetchone()
            return row is not None

    async def admin_ids(self) -> list[int]:
        async with aiosqlite.connect(self.database_path) as db:
            rows = await (await db.execute("SELECT telegram_id FROM admins ORDER BY created_at")).fetchall()
            return [int(row[0]) for row in rows]

    def create_token(self, telegram_id: int, ttl_seconds: int = 7 * 24 * 3600) -> str:
        expires = int(time.time()) + ttl_seconds
        payload = f"{telegram_id}.{expires}"
        signature = hmac.new(self._signing_key, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    async def validate_token(self, token: str) -> int:
        try:
            user_raw, expires_raw, signature = token.split(".", 2)
            user_id = int(user_raw)
            expires = int(expires_raw)
        except (ValueError, TypeError):
            raise HTTPException(status_code=401, detail="Invalid admin token")
        payload = f"{user_id}.{expires}"
        expected = hmac.new(self._signing_key, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or expires < int(time.time()):
            raise HTTPException(status_code=401, detail="Expired or invalid admin token")
        if not await self.is_admin(user_id):
            raise HTTPException(status_code=403, detail="Not an admin")
        return user_id

    def panel_url(self, telegram_id: int) -> str:
        token = self.create_token(telegram_id)
        return f"{self.settings.base_url.rstrip('/')}/admin?token={quote(token)}"

    async def _record_notification(
        self, fortune_id: str, admin_id: int, kind: str, message_id: int | None
    ) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO admin_notifications(fortune_id,admin_id,kind,telegram_message_id)
                VALUES(?,?,?,?)
                ON CONFLICT(fortune_id,admin_id,kind) DO UPDATE SET
                    telegram_message_id=excluded.telegram_message_id,
                    created_at=CURRENT_TIMESTAMP
                """,
                (fortune_id, admin_id, kind, message_id),
            )
            await db.commit()

    async def forward_photo(
        self,
        bot: Bot,
        fortune_id: str,
        user: User | None,
        intention: str,
        telegram_file_id: str,
        is_free: bool,
        charged_amount_toman: int,
    ) -> None:
        username = f"@{user.username}" if user and user.username else "بدون یوزرنیم"
        first_name = user.first_name if user else "نامشخص"
        user_id = user.id if user else 0
        payment_label = "رایگان" if is_free else f"{charged_amount_toman:,} تومان"
        caption = (
            "🆕 <b>فال جدید ثبت شد</b>\n\n"
            f"شناسه: <code>{escape(fortune_id)}</code>\n"
            f"کاربر: <b>{escape(first_name)}</b> — {escape(username)}\n"
            f"Telegram ID: <code>{user_id}</code>\n"
            f"نیت: <b>{escape(intention)}</b>\n"
            f"نوع: <b>{payment_label}</b>\n\n"
            "فایل روی سرور ذخیره نشده؛ این تصویر مستقیماً با file_id تلگرام ارسال شده است."
        )
        for admin_id in await self.admin_ids():
            try:
                sent = await bot.send_photo(
                    chat_id=admin_id,
                    photo=telegram_file_id,
                    caption=caption,
                    parse_mode="HTML",
                )
                await self._record_notification(fortune_id, admin_id, "photo", sent.message_id)
            except Exception:
                # Admin delivery failure must never block the user's fortune.
                continue

    async def notify_result(
        self,
        bot: Bot,
        fortune_id: str,
        status: str,
        result_text: str | None = None,
        error: str | None = None,
    ) -> None:
        if status == "completed":
            body = result_text or "نتیجه بدون متن ثبت شد."
            text = f"✅ <b>فال تکمیل شد</b>\nشناسه: <code>{escape(fortune_id)}</code>\n\n{body}"
        else:
            text = (
                "❌ <b>پردازش فال ناموفق بود</b>\n"
                f"شناسه: <code>{escape(fortune_id)}</code>\n"
                f"خطا: <code>{escape(error or 'unknown')}</code>"
            )
        for admin_id in await self.admin_ids():
            try:
                chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)] or [text]
                last_message_id: int | None = None
                for chunk in chunks:
                    sent = await bot.send_message(admin_id, chunk, parse_mode="HTML")
                    last_message_id = sent.message_id
                await self._record_notification(fortune_id, admin_id, "result", last_message_id)
            except Exception:
                continue

    async def _fetchone(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(query, params)).fetchone()
            return dict(row) if row else None

    async def _fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, params)).fetchall()
            return [dict(row) for row in rows]

    async def stats(self) -> dict[str, int]:
        row = await self._fetchone(
            """
            SELECT
              (SELECT COUNT(*) FROM users) AS users,
              (SELECT COUNT(*) FROM fortunes) AS fortunes,
              (SELECT COUNT(*) FROM fortunes WHERE status='completed') AS completed,
              (SELECT COUNT(*) FROM fortunes WHERE status='failed') AS failed,
              COALESCE((SELECT SUM(amount_toman) FROM payments WHERE status='paid'),0) AS revenue,
              COALESCE((SELECT SUM(balance_toman) FROM users),0) AS wallet_balance
            """
        )
        return {key: int(value or 0) for key, value in (row or {}).items()}

    async def latest_fortunes(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT f.*,u.username,u.first_name,u.balance_toman
            FROM fortunes f JOIN users u ON u.telegram_id=f.user_id
            ORDER BY f.created_at DESC LIMIT ?
            """,
            (limit,),
        )

    async def fortune_detail(self, fortune_id: str) -> dict[str, Any] | None:
        return await self._fetchone(
            """
            SELECT f.*,u.username,u.first_name,u.balance_toman,u.free_fortune_used
            FROM fortunes f JOIN users u ON u.telegram_id=f.user_id
            WHERE f.id=?
            """,
            (fortune_id,),
        )

    async def latest_payments(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT p.*,u.username,u.first_name
            FROM payments p JOIN users u ON u.telegram_id=p.user_id
            ORDER BY p.created_at DESC LIMIT ?
            """,
            (limit,),
        )

    async def latest_users(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT u.*,
              (SELECT COUNT(*) FROM fortunes f WHERE f.user_id=u.telegram_id) AS fortune_count,
              (SELECT COUNT(*) FROM payments p WHERE p.user_id=u.telegram_id AND p.status='paid') AS paid_count
            FROM users u ORDER BY u.created_at DESC LIMIT ?
            """,
            (limit,),
        )

    @staticmethod
    def _layout(title: str, token: str, body: str) -> str:
        nav = (
            f'<a href="/admin?token={quote(token)}">فال‌ها</a>'
            f'<a href="/admin/payments?token={quote(token)}">پرداخت‌ها</a>'
            f'<a href="/admin/users?token={quote(token)}">کاربران</a>'
        )
        return f"""<!doctype html><html lang="fa" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} | پنل نشونه</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d0918;color:#f4efff;font-family:Tahoma,Arial,sans-serif}}
header{{position:sticky;top:0;background:#151024ee;backdrop-filter:blur(12px);padding:16px 4%;display:flex;gap:20px;align-items:center;border-bottom:1px solid #ffffff18;z-index:5}}
header strong{{margin-left:auto;color:#cdbaff}}header a{{color:#ddd1f5;text-decoration:none}}
main{{width:min(1180px,94%);margin:28px auto 70px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:24px}}
.card{{background:#171126;border:1px solid #ffffff16;border-radius:18px;padding:18px}}.metric b{{display:block;font-size:25px;color:#c4a9ff;margin-top:8px}}
table{{width:100%;border-collapse:collapse;background:#171126;border-radius:18px;overflow:hidden}}th,td{{padding:13px;text-align:right;border-bottom:1px solid #ffffff10;vertical-align:top}}th{{color:#bfa7ed;background:#201735}}tr:hover td{{background:#ffffff05}}
a{{color:#b99aff}}.badge{{display:inline-block;padding:5px 9px;border-radius:999px;background:#ffffff12;font-size:12px}}.ok{{color:#71e6a5}}.bad{{color:#ff8c9d}}
pre{{white-space:pre-wrap;word-break:break-word;background:#0b0713;padding:15px;border-radius:12px;line-height:1.8}}img{{max-width:100%;border-radius:16px}}@media(max-width:700px){{table{{display:block;overflow:auto}}header{{flex-wrap:wrap}}}}
</style></head><body><header><strong>نشونه — پنل مدیریت</strong>{nav}</header><main><h1>{escape(title)}</h1>{body}</main></body></html>"""

    def register_routes(self, app: FastAPI, bot: Bot) -> None:
        @app.get("/admin", response_class=HTMLResponse)
        async def dashboard(token: str = Query("")) -> HTMLResponse:
            await self.validate_token(token)
            stats = await self.stats()
            fortunes = await self.latest_fortunes()
            cards = "".join(
                f'<div class="card metric">{label}<b>{value:,}</b></div>'
                for label, value in [
                    ("کاربر", stats["users"]), ("کل فال", stats["fortunes"]),
                    ("موفق", stats["completed"]), ("ناموفق", stats["failed"]),
                    ("فروش تومان", stats["revenue"]), ("موجودی کیف‌ها", stats["wallet_balance"]),
                ]
            )
            rows = "".join(
                "<tr>"
                f'<td><a href="/admin/fortune/{escape(f["id"])}?token={quote(token)}">{escape(f["id"][:8])}</a></td>'
                f'<td>{escape(f.get("first_name") or "-")}<br><small>@{escape(f.get("username") or "-")}</small></td>'
                f'<td>{escape(f["intention"])}</td><td><span class="badge">{escape(f["status"])}</span></td>'
                f'<td>{"رایگان" if f["is_free"] else f"{int(f["charged_amount_toman"]):,}"}</td>'
                f'<td>{escape(f["created_at"][:19])}</td></tr>'
                for f in fortunes
            )
            body = f'<div class="grid">{cards}</div><table><tr><th>شناسه</th><th>کاربر</th><th>نیت</th><th>وضعیت</th><th>هزینه</th><th>زمان</th></tr>{rows}</table>'
            return HTMLResponse(self._layout("آخرین فال‌ها", token, body))

        @app.get("/admin/fortune/{fortune_id}", response_class=HTMLResponse)
        async def fortune_page(fortune_id: str, token: str = Query("")) -> HTMLResponse:
            await self.validate_token(token)
            item = await self.fortune_detail(fortune_id)
            if not item:
                raise HTTPException(status_code=404, detail="Fortune not found")
            raw = ""
            if item.get("result_json"):
                try:
                    raw = json.dumps(json.loads(item["result_json"]), ensure_ascii=False, indent=2)
                except Exception:
                    raw = item["result_json"]
            image_url = f'/admin/fortune/{quote(fortune_id)}/image?token={quote(token)}'
            body = f"""
<div class="grid"><div class="card"><b>کاربر</b><p>{escape(item.get('first_name') or '-')} — @{escape(item.get('username') or '-')}</p><code>{item['user_id']}</code></div>
<div class="card"><b>نیت</b><p>{escape(item['intention'])}</p></div><div class="card"><b>وضعیت</b><p>{escape(item['status'])}</p></div>
<div class="card"><b>هزینه</b><p>{'رایگان' if item['is_free'] else f"{int(item['charged_amount_toman']):,} تومان"}</p></div></div>
<div class="card"><h2>تصویر</h2><img src="{image_url}" loading="lazy"><p><small>تصویر هنگام مشاهده از Telegram CDN دریافت می‌شود و روی سرور ذخیره نشده است.</small></p></div>
<div class="card"><h2>نتیجه کاربر</h2><pre>{escape(item.get('result_text') or '-')}</pre></div>
<div class="card"><h2>خروجی خام AI</h2><pre>{escape(raw or '-')}</pre></div>
<div class="card"><h2>خطا</h2><pre>{escape(item.get('error_message') or '-')}</pre></div>"""
            return HTMLResponse(self._layout(f"جزئیات فال {fortune_id[:8]}", token, body))

        @app.get("/admin/fortune/{fortune_id}/image")
        async def fortune_image(fortune_id: str, token: str = Query("")) -> StreamingResponse:
            await self.validate_token(token)
            item = await self.fortune_detail(fortune_id)
            if not item:
                raise HTTPException(status_code=404, detail="Fortune not found")
            tg_file = await bot.get_file(item["telegram_file_id"])
            buffer = io.BytesIO()
            await bot.download_file(tg_file.file_path, destination=buffer)
            buffer.seek(0)
            return StreamingResponse(buffer, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})

        @app.get("/admin/payments", response_class=HTMLResponse)
        async def payments_page(token: str = Query("")) -> HTMLResponse:
            await self.validate_token(token)
            items = await self.latest_payments()
            rows = "".join(
                f'<tr><td>{escape(p["id"][:8])}</td><td>{escape(p.get("first_name") or "-")}<br>@{escape(p.get("username") or "-")}</td><td>{int(p["amount_toman"]):,}</td><td>{escape(p["status"])}</td><td>{escape(str(p.get("zibal_result") or "-"))}</td><td>{escape(str(p.get("track_id") or "-"))}</td><td>{escape(p["created_at"][:19])}</td></tr>'
                for p in items
            )
            body = f'<table><tr><th>سفارش</th><th>کاربر</th><th>مبلغ</th><th>وضعیت</th><th>کد زیبال</th><th>Track ID</th><th>زمان</th></tr>{rows}</table>'
            return HTMLResponse(self._layout("پرداخت‌ها", token, body))

        @app.get("/admin/users", response_class=HTMLResponse)
        async def users_page(token: str = Query("")) -> HTMLResponse:
            await self.validate_token(token)
            items = await self.latest_users()
            rows = "".join(
                f'<tr><td>{u["telegram_id"]}</td><td>{escape(u.get("first_name") or "-")}<br>@{escape(u.get("username") or "-")}</td><td>{int(u["balance_toman"]):,}</td><td>{int(u["fortune_count"])}</td><td>{int(u["paid_count"])}</td><td>{escape(u["created_at"][:19])}</td></tr>'
                for u in items
            )
            body = f'<table><tr><th>Telegram ID</th><th>کاربر</th><th>موجودی</th><th>فال‌ها</th><th>پرداخت موفق</th><th>عضویت</th></tr>{rows}</table>'
            return HTMLResponse(self._layout("کاربران", token, body))
