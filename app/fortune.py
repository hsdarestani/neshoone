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


def _safe(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return escape(text)


def format_fortune(result: dict[str, Any], intention: str) -> str:
    evidence_lines = []
    for item in result.get("visual_evidence", [])[:6]:
        evidence_lines.append(
            f"• <b>{_safe(item.get('label', 'نشانه'), 80)}</b> — "
            f"{_safe(item.get('state', ''), 100)}؛ "
            f"{_safe(item.get('symbolic_meaning', ''), 180)}"
        )
    evidence = "\n".join(evidence_lines)
    main_symbol = result.get("main_symbol", {})
    energy = result.get("energy", {})
    return f"""
🔮 <b>نشونه تو برای «{_safe(intention, 80)}»</b>

🧿 <b>نشانه اصلی: {_safe(main_symbol.get('label', 'نامشخص'), 100)}</b>
{_safe(main_symbol.get('reason', ''), 350)}

👁 <b>چیزهایی که واقعاً در تصویر دیده شد</b>
{evidence}

✨ <b>انرژی تصویر: {_safe(energy.get('label', 'نامشخص'), 80)} — {_safe(energy.get('score', ''), 8)} از ۱۰۰</b>
{_safe(energy.get('explanation', ''), 350)}

♻️ <b>پیام کارمایی</b>
{_safe(result.get('karma_message', ''), 420)}

🧲 <b>پیام جذب</b>
{_safe(result.get('attraction_message', ''), 420)}

⚠️ <b>هشدار پنهان تصویر</b>
{_safe(result.get('hidden_warning', ''), 320)}

🌱 <b>کاری که امروز انجام بده</b>
{_safe(result.get('action_today', ''), 280)}

⏳ <b>نشانه زمانی</b>
{_safe(result.get('timing_hint', ''), 220)}

📖 <b>تعبیر کامل</b>
{_safe(result.get('full_reading', ''), 1350)}

💫 <i>{_safe(result.get('share_line', ''), 220)}</i>

<blockquote>این تعبیر برای سرگرمی و خودشناسی نمادین است؛ نه پیش‌بینی علمی یا قطعی.</blockquote>
""".strip()
