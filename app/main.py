from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from html import escape
from typing import Any

from aiogram import Bot
from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.bot import build_dispatcher, main_keyboard
from app.config import get_settings
from app.db import Database
from app.fortune import format_toman
from app.services.vision import VisionFortuneService
from app.services.zibal import ZibalError, ZibalService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()
db = Database(settings.database_path)
bot = Bot(settings.bot_token.get_secret_value())
vision = VisionFortuneService(settings)
zibal = ZibalService(settings)
dp = build_dispatcher(db, vision, zibal, settings)


def page(title: str, message: str, success: bool = True) -> str:
    icon = "✓" if success else "!"
    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(title)} | نشونه</title>
  <style>
    *{{box-sizing:border-box}} body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#100b1e;color:#f7f1ff;font-family:Tahoma,Arial,sans-serif;padding:24px}}
    .card{{width:min(520px,100%);padding:34px;border:1px solid #ffffff22;border-radius:28px;background:linear-gradient(145deg,#211637,#160f28);box-shadow:0 24px 80px #0008;text-align:center}}
    .icon{{width:72px;height:72px;border-radius:50%;display:grid;place-items:center;margin:0 auto 20px;font-size:38px;background:{'#6fdfaa22' if success else '#ff7b8d22'};border:1px solid {'#6fdfaa66' if success else '#ff7b8d66'}}}
    h1{{font-size:25px;margin:0 0 14px}} p{{line-height:2;color:#d9cfea;margin:0 0 24px}} .brand{{color:#bfa5ff;font-weight:bold;letter-spacing:.5px}}
    a{{display:inline-block;padding:13px 22px;border-radius:15px;background:#8e68ff;color:white;text-decoration:none;font-weight:bold}}
  </style>
</head>
<body><main class="card"><div class="icon">{icon}</div><div class="brand">نشونه</div><h1>{escape(title)}</h1><p>{escape(message)}</p><a href="https://t.me/">بازگشت به تلگرام</a></main></body>
</html>"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.init()
    try:
        await bot.set_webhook(
            url=settings.telegram_webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=False,
        )
        logger.info("Telegram webhook configured: %s", settings.telegram_webhook_url)
    except Exception:
        logger.exception("Could not configure Telegram webhook during startup")
    yield
    await bot.session.close()


app = FastAPI(title="Neshoone", version="1.0.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return page(
        "نیت کن؛ نشونه‌ات را ببین",
        "ربات فال تصویری مبتنی بر اشیای واقعی داخل عکس، انرژی، کارما و قانون جذب. اولین فال رایگان است.",
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/privacy", response_class=HTMLResponse)
async def privacy() -> str:
    return page(
        "حریم خصوصی",
        "تصویر فقط برای تولید نتیجه پردازش می‌شود. نتیجه‌ها جنبه سرگرمی و خودشناسی نمادین دارند و مبنای تصمیم پزشکی، حقوقی یا مالی نیستند.",
    )


@app.post(settings.telegram_webhook_path)
async def telegram_webhook(request: Request) -> JSONResponse:
    supplied_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if supplied_secret != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram secret")
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})


async def callback_params(request: Request) -> dict[str, Any]:
    params: dict[str, Any] = dict(request.query_params)
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            if isinstance(body, dict):
                params.update(body)
        else:
            try:
                params.update(dict(await request.form()))
            except Exception:
                logger.warning("Could not parse Zibal callback form", exc_info=True)
    return params


def is_success(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "ok", "100"}


@app.api_route(settings.zibal_callback_path, methods=["GET", "POST"], response_class=HTMLResponse)
async def zibal_callback(request: Request) -> HTMLResponse:
    params = await callback_params(request)
    track_id = str(params.get("trackId") or params.get("track_id") or "").strip()
    order_id = str(params.get("orderId") or params.get("order_id") or "").strip()
    success_value = params.get("success", params.get("status", ""))

    payment = await db.get_payment(track_id or None, order_id or None)
    if not payment:
        return HTMLResponse(page("پرداخت پیدا نشد", "شناسه این پرداخت در سیستم وجود ندارد.", False), status_code=404)

    if not track_id and payment.get("track_id"):
        track_id = str(payment["track_id"])
    if not track_id:
        return HTMLResponse(page("پرداخت نامعتبر", "کد پیگیری زیبال دریافت نشد.", False), status_code=400)

    if success_value != "" and not is_success(success_value):
        await db.mark_payment_failed(payment["id"])
        return HTMLResponse(page("پرداخت انجام نشد", "مبلغی به کیف پول اضافه نشد.", False))

    try:
        verified = await zibal.verify_payment(track_id)
    except ZibalError as exc:
        logger.exception("Zibal verification failed for %s", payment["id"])
        return HTMLResponse(page("تأیید پرداخت ناموفق بود", str(exc), False), status_code=400)

    credit = await db.credit_verified_payment(
        payment_id=payment["id"],
        verified_amount_rial=verified["amount_rial"],
        zibal_result=verified["result"],
        ref_number=verified["ref_number"],
        card_number=verified["card_number"],
    )
    if not credit.get("ok"):
        logger.error("Payment credit failed: %s", credit)
        return HTMLResponse(page("خطا در شارژ کیف پول", "پرداخت ثبت شد اما مبلغ سفارش تطبیق نداشت. با پشتیبانی تماس بگیر.", False), status_code=409)

    if not credit.get("duplicate"):
        try:
            await bot.send_message(
                chat_id=credit["user_id"],
                text=(
                    "✅ <b>کیف پولت شارژ شد</b>\n\n"
                    f"مبلغ: <b>{format_toman(credit['amount_toman'])} تومان</b>\n"
                    f"موجودی جدید: <b>{format_toman(credit['balance'])} تومان</b>\n\n"
                    "حالا می‌تونی نشونه جدیدت رو بگیری."
                ),
                parse_mode="HTML",
                reply_markup=main_keyboard(),
            )
        except Exception:
            logger.exception("Payment succeeded but Telegram notification failed")

    return HTMLResponse(
        page(
            "کیف پول شارژ شد",
            f"موجودی جدید شما {format_toman(int(credit['balance']))} تومان است. به تلگرام برگردید و نشونه جدیدتان را بگیرید.",
            True,
        )
    )
