from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


class ZibalError(RuntimeError):
    def __init__(self, message: str, result: int | None = None) -> None:
        super().__init__(message)
        self.result = result


class ZibalService:
    REQUEST_URL = "https://gateway.zibal.ir/v1/request"
    VERIFY_URL = "https://gateway.zibal.ir/v1/verify"
    START_URL = "https://gateway.zibal.ir/start/{track_id}"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def request_payment(self, payment_id: str, amount_rial: int) -> dict[str, Any]:
        payload = {
            "merchant": self.settings.zibal_merchant.get_secret_value(),
            "amount": amount_rial,
            "callbackUrl": self.settings.zibal_callback_url,
            "orderId": payment_id,
            "description": "شارژ کیف پول ربات نشونه",
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(self.REQUEST_URL, json=payload)
        if response.status_code >= 400:
            raise ZibalError(f"خطای ارتباط با زیبال: HTTP {response.status_code}")
        data = response.json()
        result = int(data.get("result", 0))
        track_id = data.get("trackId")
        if result != 100 or not track_id:
            raise ZibalError(data.get("message") or "زیبال لینک پرداخت نساخت.", result=result)
        track_id = str(track_id)
        return {
            "result": result,
            "track_id": track_id,
            "payment_url": self.START_URL.format(track_id=track_id),
        }

    async def verify_payment(self, track_id: str) -> dict[str, Any]:
        normalized_track_id: int | str = int(track_id) if track_id.isdigit() else track_id
        payload = {
            "merchant": self.settings.zibal_merchant.get_secret_value(),
            "trackId": normalized_track_id,
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(self.VERIFY_URL, json=payload)
        if response.status_code >= 400:
            raise ZibalError(f"خطای وریفای زیبال: HTTP {response.status_code}")
        data = response.json()
        result = int(data.get("result", 0))
        if result not in (100, 201):
            raise ZibalError(data.get("message") or "پرداخت توسط زیبال تأیید نشد.", result=result)
        amount = data.get("amount")
        if amount is None:
            raise ZibalError("زیبال مبلغ تأییدشده را برنگرداند.", result=result)
        return {
            "result": result,
            "amount_rial": int(amount),
            "ref_number": str(data.get("refNumber") or data.get("refId") or "") or None,
            "card_number": str(data.get("cardNumber") or "") or None,
            "paid_at": data.get("paidAt"),
        }
