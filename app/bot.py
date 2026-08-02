from __future__ import annotations

import io
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.admin import AdminService
from app.config import Settings
from app.db import Database
from app.fortune import INTENTIONS, format_fortune, format_toman
from app.services.vision import UnusableImageError, VisionError, VisionFortuneService
from app.services.zibal import ZibalError, ZibalService

logger = logging.getLogger(__name__)

BTN_FORTUNE = "🔮 گرفتن نشونه"
BTN_WALLET = "💳 کیف پول"
BTN_HISTORY = "🗂 فال‌های من"
BTN_HELP = "❓ راهنما"
PAYMENT_PACKAGES = (50_000, 100_000, 200_000)


class FortuneState(StatesGroup):
    waiting_photo = State()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_FORTUNE)],
            [KeyboardButton(text=BTN_WALLET), KeyboardButton(text=BTN_HISTORY)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="نیت کن و نشونه‌ات را بگیر…",
    )


def intention_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="❤️ عشق و رابطه", callback_data="intent:love"),
            InlineKeyboardButton(text="💰 پول و فراوانی", callback_data="intent:money"),
        ],
        [
            InlineKeyboardButton(text="💼 کار و آینده", callback_data="intent:work"),
            InlineKeyboardButton(text="🛤 تصمیم دو راهی", callback_data="intent:choice"),
        ],
        [
            InlineKeyboardButton(text="☀️ انرژی امروز", callback_data="intent:today"),
            InlineKeyboardButton(text="🌌 پیام جهان", callback_data="intent:universe"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wallet_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"شارژ {format_toman(amount)} تومان", callback_data=f"pay:{amount}")]
        for amount in PAYMENT_PACKAGES
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_error_for_user(exc: ZibalError) -> str:
    if exc.result == 106:
        return "آدرس بازگشت درگاه زیبال هنوز تأیید نشده است. تنظیمات درگاه در حال بررسی است."
    if exc.result == 115:
        return "IP سرور در تنظیمات درگاه زیبال ثبت نشده است."
    if exc.result is not None:
        return f"زیبال درخواست را رد کرد؛ کد خطا: {exc.result}"
    return "ارتباط با زیبال برقرار نشد. کمی بعد دوباره امتحان کن."


async def ensure_user(db: Database, message_or_callback: Message | CallbackQuery) -> int:
    user = message_or_callback.from_user
    if user is None:
        raise RuntimeError("Telegram user is missing")
    await db.upsert_user(user.id, user.username, user.first_name)
    return user.id


def build_dispatcher(
    db: Database,
    vision: VisionFortuneService,
    zibal: ZibalService,
    settings: Settings,
    admin: AdminService,
) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    router = Router()

    async def send_wallet(target: Message, user_id: int) -> None:
        user = await db.get_user(user_id)
        if not user:
            return
        balance = int(user["balance_toman"])
        free_status = "هنوز استفاده نشده ✅" if not user["free_fortune_used"] else "استفاده شده"
        possible = balance // settings.fortune_price_toman
        await target.answer(
            "💳 <b>کیف پول نشونه</b>\n\n"
            f"موجودی: <b>{format_toman(balance)} تومان</b>\n"
            f"فال قابل دریافت با موجودی: <b>{possible}</b>\n"
            f"فال اول رایگان: <b>{free_status}</b>\n\n"
            f"هزینه هر فال بعدی: <b>{format_toman(settings.fortune_price_toman)} تومان</b>",
            reply_markup=wallet_keyboard(),
            parse_mode="HTML",
        )

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await ensure_user(db, message)
        await message.answer(
            "✨ <b>به نشونه خوش اومدی</b>\n\n"
            "نیت کن، بدون برنامه‌ریزی از اولین چیزی که مقابلته عکس بگیر، "
            "تا نشانه‌های پنهان تصویرت را از نگاه انرژی، کارما و قانون جذب ببینی.\n\n"
            "🎁 اولین فال کاملاً رایگانه. هر فال بعدی ۱۰ هزار تومان.\n\n"
            "🔐 عکس و نتیجه برای کنترل کیفیت در پنل مدیریت ثبت می‌شوند؛ فایل عکس روی سرور ذخیره نمی‌شود.",
            reply_markup=main_keyboard(),
            parse_mode="HTML",
        )

    @router.message(Command("admin"))
    async def admin_access(message: Message) -> None:
        user_id = await ensure_user(db, message)
        allowed, claimed = await admin.claim_or_check(user_id)
        if not allowed:
            await message.answer("این دستور فقط برای ادمین اصلی ربات فعال است.")
            return
        prefix = "✅ شما به‌عنوان اولین ادمین ثبت شدی.\n\n" if claimed else ""
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ ورود به پنل مدیریت", url=admin.panel_url(user_id))]
            ]
        )
        await message.answer(
            prefix
            + "از دکمه زیر وارد پنل شو. لینک امضاشده است و بعد از مدتی منقضی می‌شود؛ هر زمان لازم شد دوباره /admin بزن.",
            reply_markup=keyboard,
        )

    @router.message(Command("wallet"))
    @router.message(F.text == BTN_WALLET)
    async def wallet(message: Message) -> None:
        user_id = await ensure_user(db, message)
        await send_wallet(message, user_id)

    @router.message(Command("fortune"))
    @router.message(F.text == BTN_FORTUNE)
    async def choose_intention(message: Message, state: FSMContext) -> None:
        await ensure_user(db, message)
        await state.clear()
        await message.answer("برای چه موضوعی نیت کردی؟", reply_markup=intention_keyboard())

    @router.callback_query(F.data.startswith("intent:"))
    async def intention_selected(callback: CallbackQuery, state: FSMContext) -> None:
        await ensure_user(db, callback)
        key = callback.data.split(":", 1)[1]
        intention = INTENTIONS.get(key)
        if not intention:
            await callback.answer("این نیت معتبر نیست.", show_alert=True)
            return
        await state.set_state(FortuneState.waiting_photo)
        await state.update_data(intention=intention)
        if callback.message:
            await callback.message.edit_text(
                f"🧘 <b>نیت: {intention}</b>\n\n"
                "چند ثانیه چشم‌هات رو ببند و به نیتت فکر کن. بعد بدون انتخاب قبلی، "
                "از اولین چیزی که مقابلته یک عکس روشن و واضح بفرست.\n\n"
                "اشیای عادی هم مهم‌اند؛ در، پنجره، کفش، ساعت، گیاه، مسیر، نور یا هر چیزی که وارد کادر شده.",
                parse_mode="HTML",
            )
        await callback.answer()

    @router.message(FortuneState.waiting_photo, ~F.photo)
    async def require_photo(message: Message) -> None:
        await message.answer("لطفاً عکس را به‌صورت Photo بفرست، نه فایل یا متن.")

    @router.message(FortuneState.waiting_photo, F.photo)
    async def receive_photo(message: Message, state: FSMContext, bot: Bot) -> None:
        user_id = await ensure_user(db, message)
        data = await state.get_data()
        intention = data.get("intention", INTENTIONS["universe"])
        photo = message.photo[-1]

        reservation = await db.reserve_fortune(
            user_id=user_id,
            intention=intention,
            telegram_file_id=photo.file_id,
            price_toman=settings.fortune_price_toman,
        )
        if not reservation:
            await state.clear()
            await message.answer(
                "موجودی کیف پولت برای یک فال کافی نیست. کیف پولت را شارژ کن و دوباره عکس را بفرست.",
                reply_markup=wallet_keyboard(),
            )
            return

        await admin.forward_photo(
            bot=bot,
            fortune_id=reservation["id"],
            user=message.from_user,
            intention=intention,
            telegram_file_id=photo.file_id,
            is_free=bool(reservation["is_free"]),
            charged_amount_toman=int(reservation["charged_amount_toman"]),
        )

        await state.clear()
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        processing = await message.answer(
            "🔍 دارم اشیای واقعی، نور، رنگ و رابطه نشانه‌های داخل عکس را بررسی می‌کنم…"
        )

        try:
            file = await bot.get_file(photo.file_id)
            image_buffer = io.BytesIO()
            await bot.download_file(file.file_path, destination=image_buffer)
            result = await vision.analyze(image_buffer.getvalue(), intention)
            formatted = format_fortune(result, intention)
            await db.complete_fortune(reservation["id"], result, formatted)
            await admin.notify_result(bot, reservation["id"], "completed", result_text=formatted)
            await processing.edit_text("✨ نشونه‌ات پیدا شد.")
            await message.answer(formatted, parse_mode="HTML", reply_markup=main_keyboard())
            balance = reservation["balance_after"]
            suffix = "این فال رایگان بود 🎁" if reservation["is_free"] else f"موجودی جدید: {format_toman(balance)} تومان"
            await message.answer(suffix)
        except UnusableImageError as exc:
            await db.fail_and_refund_fortune(reservation["id"], str(exc))
            await admin.notify_result(bot, reservation["id"], "failed", error=str(exc))
            await processing.edit_text(
                f"این عکس برای تعبیر کافی نبود: {exc}\n\n"
                "اعتبارت برگشت. یک عکس روشن‌تر با چند شیء مشخص بفرست."
            )
        except VisionError:
            logger.exception("Vision provider failed")
            await db.fail_and_refund_fortune(reservation["id"], "vision_provider_error")
            await admin.notify_result(bot, reservation["id"], "failed", error="vision_provider_error")
            await processing.edit_text(
                "پردازش تصویر این بار کامل نشد و اعتبارت خودکار برگشت. چند لحظه بعد دوباره امتحان کن."
            )
        except Exception:
            logger.exception("Unexpected fortune processing error")
            await db.fail_and_refund_fortune(reservation["id"], "unexpected_error")
            await admin.notify_result(bot, reservation["id"], "failed", error="unexpected_error")
            await processing.edit_text(
                "یک خطای موقت پیش آمد و اعتبارت خودکار برگشت. دوباره امتحان کن."
            )

    @router.callback_query(F.data.startswith("pay:"))
    async def create_payment(callback: CallbackQuery) -> None:
        user_id = await ensure_user(db, callback)
        try:
            amount = int(callback.data.split(":", 1)[1])
        except (TypeError, ValueError):
            await callback.answer("مبلغ نامعتبر است.", show_alert=True)
            return
        if amount not in PAYMENT_PACKAGES:
            await callback.answer("این بسته معتبر نیست.", show_alert=True)
            return

        payment = await db.create_payment(user_id, amount)
        try:
            gateway = await zibal.request_payment(payment["id"], payment["amount_rial"])
            await db.attach_payment_track(payment["id"], gateway["track_id"], gateway["result"])
        except ZibalError as exc:
            await db.mark_payment_failed(payment["id"], exc.result)
            logger.exception("Could not create Zibal payment")
            await callback.answer(payment_error_for_user(exc), show_alert=True)
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💳 پرداخت امن با زیبال", url=gateway["payment_url"])],
                [InlineKeyboardButton(text="ساخت لینک جدید", callback_data=f"pay:{amount}")],
            ]
        )
        if callback.message:
            await callback.message.answer(
                f"مبلغ شارژ: <b>{format_toman(amount)} تومان</b>\n\n"
                "بعد از پرداخت، موجودی کیف پول به‌صورت خودکار اضافه می‌شود.",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        await callback.answer()

    @router.message(Command("history"))
    @router.message(F.text == BTN_HISTORY)
    async def history(message: Message) -> None:
        user_id = await ensure_user(db, message)
        fortunes = await db.recent_fortunes(user_id)
        if not fortunes:
            await message.answer("هنوز فال تکمیل‌شده‌ای نداری.")
            return
        lines = ["🗂 <b>آخرین فال‌های تو</b>"]
        for index, fortune in enumerate(fortunes, start=1):
            free_label = "رایگان" if fortune["is_free"] else f"{format_toman(fortune['charged_amount_toman'])} تومان"
            lines.append(f"\n{index}. <b>{fortune['intention']}</b> — {free_label}")
        await message.answer("\n".join(lines), parse_mode="HTML")

    @router.message(Command("help"))
    @router.message(F.text == BTN_HELP)
    async def help_message(message: Message) -> None:
        await ensure_user(db, message)
        await message.answer(
            "❓ <b>چطور نشونه بگیرم؟</b>\n\n"
            "۱. موضوع نیتت را انتخاب کن.\n"
            "۲. چند ثانیه فقط به نیتت فکر کن.\n"
            "۳. بدون صحنه‌سازی از اولین چیزی که روبه‌روته عکس بگیر.\n"
            "۴. عکس باید روشن باشد و چند شیء قابل تشخیص داشته باشد.\n\n"
            "نتیجه بر اساس اشیای واقعی تصویر ساخته می‌شود، اما تعبیر انرژی، کارما و جذب صرفاً سرگرمی و خودشناسی نمادین است. "
            "عکس و نتیجه برای کنترل کیفیت در پنل مدیریت ثبت می‌شوند؛ فایل عکس روی سرور ذخیره نمی‌شود.",
            parse_mode="HTML",
        )

    @router.message()
    async def fallback(message: Message) -> None:
        await ensure_user(db, message)
        await message.answer("از دکمه‌های منو استفاده کن 👇", reply_markup=main_keyboard())

    dp.include_router(router)
    return dp
