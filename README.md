# نشونه

ربات تلگرامی فال تصویری مبتنی بر اشیای واقعی داخل عکس، نشانه‌شناسی، انرژی، کارما و قانون جذب.

> این محصول صرفاً برای سرگرمی و خودشناسی نمادین است و پیش‌بینی علمی یا قطعی ارائه نمی‌کند.

## تجربه کاربر

1. کاربر موضوع نیت را انتخاب می‌کند.
2. از اولین چیزی که مقابلش قرار دارد عکس می‌فرستد.
3. موتور Vision اشیا، وضعیت آن‌ها، نور، رنگ و رابطه بین نشانه‌ها را استخراج می‌کند.
4. یک تعبیر فارسی شامل نشانه اصلی، انرژی، پیام کارمایی، قانون جذب، هشدار و اقدام امروز تولید می‌شود.
5. اولین فال رایگان است؛ هر فال بعدی ۱۰٬۰۰۰ تومان از کیف پول کم می‌کند.
6. کیف پول با لینک پرداخت زیبال شارژ می‌شود.

## امکانات MVP

- ربات تلگرام مبتنی بر webhook
- شش نیت: عشق، پول، کار، دوراهی، انرژی امروز و پیام جهان
- اولین فال رایگان برای هر Telegram User ID
- کیف پول تومانی و دفتر کامل تراکنش‌ها
- بسته‌های شارژ ۵۰، ۱۰۰ و ۲۰۰ هزار تومان
- ساخت لینک پرداخت و Verify سمت سرور با زیبال
- جلوگیری از شارژ دوباره در callback تکراری
- رزرو هزینه قبل از پردازش و بازگشت خودکار در خطا
- کوچک‌سازی عکس قبل از ارسال به OpenAI
- خروجی JSON ساختاریافته و تعبیر فارسی
- تاریخچه فال‌های تکمیل‌شده
- SQLite با WAL برای نسخه اول
- Docker Compose و HTTPS خودکار با Caddy
- استقرار خودکار از GitHub Actions

## معماری

```text
Telegram
   ↓ webhook
FastAPI + aiogram
   ├── OpenAI Responses API (Vision)
   ├── SQLite wallet / fortunes / payments
   └── Zibal request + verify

Caddy → HTTPS → FastAPI
```

## متغیرهای محیطی

```env
BOT_TOKEN=
OPENAI_API_KEY=
ZIBAL_MERCHANT=
BASE_URL=https://neshoone.smarbiz.sbs
DOMAIN=neshoone.smarbiz.sbs
DATABASE_PATH=/data/neshoone.db
OPENAI_MODEL=gpt-4.1-mini
FORTUNE_PRICE_TOMAN=10000
IMAGE_DETAIL=high
MAX_IMAGE_SIDE=1280
```

نام Secretهای فعلی GitHub که workflow استفاده می‌کند:

- `BOTTOKEN`
- `OPENAIAPIKEY`
- `ZIBALMERCHANT`
- `HOST`
- `PASS`
- `SSH_USER` اختیاری است و در صورت نبودن، `root` استفاده می‌شود.

## آدرس‌های مهم

- سایت و API: `https://neshoone.smarbiz.sbs`
- سلامت سرویس: `https://neshoone.smarbiz.sbs/health`
- Telegram webhook: `https://neshoone.smarbiz.sbs/telegram/webhook`
- Zibal callback: `https://neshoone.smarbiz.sbs/payments/zibal/callback`
- حریم خصوصی: `https://neshoone.smarbiz.sbs/privacy`

## اجرای محلی

```bash
cp .env.example .env
# مقادیر .env را وارد کنید
docker compose up -d --build
```

یا بدون Docker:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

برای webhook واقعی، `BASE_URL` باید HTTPS عمومی باشد.

## استقرار

بعد از merge روی `main`، workflow فایل‌ها را به سرور موجود در Secret `HOST` منتقل می‌کند، `.env` را از Secretها می‌سازد و سرویس را در `/opt/neshoone` با Docker Compose بالا می‌آورد.

دامنه `neshoone.smarbiz.sbs` باید با A Record به IP همان `HOST` اشاره کند و پورت‌های 80 و 443 روی سرور باز باشند. Caddy گواهی HTTPS را خودکار دریافت می‌کند.

این پروژه هیچ وابستگی‌ای به سرویس Hamoon Cloud ندارد؛ فقط الگوی اتصال و Verify زیبال را پیاده می‌کند.

## تست

```bash
pytest -q
```

تست‌ها این موارد را پوشش می‌دهند:

- اولین فال رایگان
- عدم دریافت فال دوم بدون موجودی
- شارژ کیف پول
- idempotency پرداخت
- عدم شارژ در اختلاف مبلغ
- بازپرداخت خودکار هزینه فال ناموفق

## نکات امنیتی

- Secretها هرگز داخل ریپو ذخیره نمی‌شوند.
- callback به‌تنهایی معتبر نیست و هر پرداخت از API زیبال Verify می‌شود.
- مبلغ Verifyشده باید دقیقاً با مبلغ سفارش برابر باشد.
- هر پرداخت فقط یک‌بار می‌تواند کیف پول را شارژ کند.
- webhook تلگرام با Secret Token بررسی می‌شود.
- مدل اجازه ندارد از روی چهره ویژگی‌های حساس یا ادعاهای قطعی تولید کند.
