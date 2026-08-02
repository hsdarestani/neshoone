from __future__ import annotations

from html import escape
from typing import Any


INTENTIONS: dict[str, str] = {
    "love": "عشق و رابطه",
    "money": "پول و فراوانی",
    "work": "کار و آینده",
    "choice": "تصمیم دو راهی",
    "today": "انرژی امروز",
    "universe": "پیام جهان برای من",
}


def format_toman(amount: int) -> str:
    return f"{amount:,}".replace(",", "٬")


def format_fortune(result: dict[str, Any], intention: str) -> str:
    evidence_lines = []
    for item in result.get("visual_evidence", [])[:6]:
        evidence_lines.append(
            f"• <b>{escape(str(item.get('label', 'نشانه')))}</b> — "
            f"{escape(str(item.get('state', '')))}؛ "
            f"{escape(str(item.get('symbolic_meaning', '')))}"
        )
    evidence = "\n".join(evidence_lines)
    main_symbol = result.get("main_symbol", {})
    energy = result.get("energy", {})
    text = f"""
🔮 <b>نشونه تو برای «{escape(intention)}»</b>

🧿 <b>نشانه اصلی: {escape(str(main_symbol.get('label', 'نامشخص')))}</b>
{escape(str(main_symbol.get('reason', '')))}

👁 <b>چیزهایی که واقعاً در تصویر دیده شد</b>
{evidence}

✨ <b>انرژی تصویر: {escape(str(energy.get('label', 'نامشخص')))} — {escape(str(energy.get('score', '')))} از ۱۰۰</b>
{escape(str(energy.get('explanation', '')))}

♻️ <b>پیام کارمایی</b>
{escape(str(result.get('karma_message', '')))}

🧲 <b>پیام جذب</b>
{escape(str(result.get('attraction_message', '')))}

⚠️ <b>هشدار پنهان تصویر</b>
{escape(str(result.get('hidden_warning', '')))}

🌱 <b>کاری که امروز انجام بده</b>
{escape(str(result.get('action_today', '')))}

⏳ <b>نشانه زمانی</b>
{escape(str(result.get('timing_hint', '')))}

📖 <b>تعبیر کامل</b>
{escape(str(result.get('full_reading', '')))}

💫 <i>{escape(str(result.get('share_line', '')))}</i>

<blockquote>این تعبیر برای سرگرمی و خودشناسی نمادین است؛ نه پیش‌بینی علمی یا قطعی.</blockquote>
""".strip()
    return text[:4000]
