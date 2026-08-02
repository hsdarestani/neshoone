from __future__ import annotations

import hashlib
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    bot_token: SecretStr = Field(alias="BOT_TOKEN")
    openai_api_key: SecretStr = Field(alias="OPENAI_API_KEY")
    zibal_merchant: SecretStr = Field(alias="ZIBAL_MERCHANT")

    base_url: str = Field(default="https://neshoone.smarbiz.sbs", alias="BASE_URL")
    domain: str = Field(default="neshoone.smarbiz.sbs", alias="DOMAIN")
    database_path: str = Field(default="/data/neshoone.db", alias="DATABASE_PATH")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    fortune_price_toman: int = Field(default=10_000, alias="FORTUNE_PRICE_TOMAN")
    image_detail: str = Field(default="high", alias="IMAGE_DETAIL")
    max_image_side: int = Field(default=1280, alias="MAX_IMAGE_SIDE")

    telegram_webhook_path: str = "/telegram/webhook"
    zibal_callback_path: str = "/payments/zibal/callback"

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.telegram_webhook_path}"

    @property
    def zibal_callback_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.zibal_callback_path}"

    @property
    def telegram_webhook_secret(self) -> str:
        token = self.bot_token.get_secret_value()
        return hashlib.sha256(f"neshoone:{token}".encode()).hexdigest()[:48]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
