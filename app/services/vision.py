from __future__ import annotations

import base64
import io
import json
from typing import Any

import httpx
from PIL import Image, ImageOps, ImageStat

from app.config import Settings


class VisionError(RuntimeError):
    pass


class UnusableImageError(VisionError):
    pass


FORTUNE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "usable": {"type": "boolean"},
        "rejection_reason": {"type": ["string", "null"]},
        "visual_evidence": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "state": {"type": "string"},
                    "position": {"type": "string"},
                    "symbolic_meaning": {"type": "string"},
                },
                "required": ["label", "state", "position", "symbolic_meaning"],
            },
        },
        "main_symbol": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "label": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["label", "reason"],
        },
        "energy": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "label": {"type": "string"},
                "score": {"type": "integer", "minimum": 1, "maximum": 100},
                "explanation": {"type": "string"},
            },
            "required": ["label", "score", "explanation"],
        },
        "karma_message": {"type": "string"},
        "attraction_message": {"type": "string"},
        "hidden_warning": {"type": "string"},
        "action_today": {"type": "string"},
        "timing_hint": {"type": "string"},
        "share_line": {"type": "string"},
        "full_reading": {"type": "string"},
    },
    "required": [
        "usable",
        "rejection_reason",
        "visual_evidence",
        "main_symbol",
        "energy",
        "karma_message",
        "attraction_message",
        "hidden_warning",
        "action_today",
        "timing_hint",
        "share_line",
        "full_reading",
    ],
}


SYSTEM_PROMPT = """
تو موتور بینایی و نشانه‌شناسی محصول «نشونه» هستی. کاربر بعد از نیت‌کردن، از اولین چیزی که مقابلش بوده عکس گرفته است.

وظیفه تو دو مرحله دارد:
۱) فقط چیزهایی را که واقعاً در عکس قابل مشاهده‌اند استخراج کن: اشیا، وضعیت اشیا، رنگ، نور، جهت، فاصله، عدد یا نوشته و رابطه میان اشیا.
۲) بر اساس همان شواهد، یک تعبیر نمادین و سرگرم‌کننده درباره انرژی، کارما و قانون جذب تولید کن.

قوانین سخت:
- هیچ شیء، عدد یا نوشته‌ای را اختراع نکن.
- اگر عکس خیلی تاریک، تار، نامفهوم، اسکرین‌شات متنی یا بدون نشانه بصری کافی است، usable=false بده.
- از روی چهره درباره هویت، قومیت، مذهب، سلامت، شخصیت قطعی، وضعیت مالی یا زندگی خصوصی فرد نتیجه‌گیری نکن.
- فال را قطعی، علمی یا تضمینی معرفی نکن. از واژه‌هایی مثل «می‌تواند»، «به‌صورت نمادین» و «احتمالاً» استفاده کن.
- زمان دقیق یا وعده قطعی نساز. اگر عددی واقعاً دیده می‌شود فقط به‌عنوان نشانه احتمالی مطرح کن.
- توصیه پزشکی، حقوقی، مالی یا تصمیم حیاتی نده.
- متن باید فارسی روان، جذاب، گرم و قابل اشتراک باشد و به نیت کاربر مرتبط بماند.
- full_reading حدود 180 تا 300 کلمه باشد و بقیه فیلدها کوتاه و غیرتکراری باشند.
""".strip()


class VisionFortuneService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _prepare_image(self, raw: bytes) -> tuple[str, dict[str, Any]]:
        try:
            with Image.open(io.BytesIO(raw)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
        except Exception as exc:  # Pillow raises several format-specific exceptions
            raise UnusableImageError("فرمت تصویر قابل خواندن نیست.") from exc

        if image.width < 160 or image.height < 160:
            raise UnusableImageError("ابعاد تصویر خیلی کوچک است.")

        image.thumbnail((self.settings.max_image_side, self.settings.max_image_side), Image.Resampling.LANCZOS)
        stat = ImageStat.Stat(image.resize((64, 64)))
        mean_rgb = tuple(round(value) for value in stat.mean[:3])
        brightness = round(sum(mean_rgb) / 3)

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=86, optimize=True)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}", {
            "width": image.width,
            "height": image.height,
            "mean_rgb": mean_rgb,
            "brightness": brightness,
        }

    @staticmethod
    def _extract_output_text(payload: dict[str, Any]) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        for item in payload.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        raise VisionError("پاسخ متنی معتبری از مدل دریافت نشد.")

    async def analyze(self, raw_image: bytes, intention: str) -> dict[str, Any]:
        image_url, metrics = self._prepare_image(raw_image)
        user_prompt = (
            f"نیت انتخاب‌شده کاربر: {intention}\n"
            f"اطلاعات فنی قطعی تصویر: {json.dumps(metrics, ensure_ascii=False)}\n"
            "عکس را بررسی کن و فقط بر اساس شواهد واقعی داخل آن خروجی JSON تولید کن."
        )
        request_payload: dict[str, Any] = {
            "model": self.settings.openai_model,
            "store": False,
            "instructions": SYSTEM_PROMPT,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_prompt},
                        {
                            "type": "input_image",
                            "image_url": image_url,
                            "detail": self.settings.image_detail,
                        },
                    ],
                }
            ],
            "max_output_tokens": 1600,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "neshoone_fortune",
                    "strict": True,
                    "schema": FORTUNE_SCHEMA,
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0)) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json=request_payload,
            )
        if response.status_code >= 400:
            detail = response.text[:1200]
            raise VisionError(f"OpenAI error {response.status_code}: {detail}")

        try:
            output_text = self._extract_output_text(response.json())
            result = json.loads(output_text)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise VisionError("خروجی مدل ساختار معتبر نداشت.") from exc

        if not result.get("usable"):
            raise UnusableImageError(result.get("rejection_reason") or "این تصویر برای تعبیر مناسب نیست.")
        return result
