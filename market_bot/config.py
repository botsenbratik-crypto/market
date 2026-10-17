"""Settings from .env."""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr = Field(alias="BOT_TOKEN")
    default_quote: str = Field(default="USDT", alias="DEFAULT_QUOTE")
    cmc_api_key: str | None = Field(default=None, alias="CMC_API_KEY")

    bybit_api_key: SecretStr | None = Field(default=None, alias="BYBIT_API_KEY")
    bybit_api_secret: SecretStr | None = Field(default=None, alias="BYBIT_API_SECRET")
    okx_api_key: SecretStr | None = Field(default=None, alias="OKX_API_KEY")
    okx_api_secret: SecretStr | None = Field(default=None, alias="OKX_API_SECRET")
    okx_api_passphrase: SecretStr | None = Field(default=None, alias="OKX_API_PASSPHRASE")
    mexc_api_key: SecretStr | None = Field(default=None, alias="MEXC_API_KEY")
    mexc_api_secret: SecretStr | None = Field(default=None, alias="MEXC_API_SECRET")
    bingx_api_key: SecretStr | None = Field(default=None, alias="BINGX_API_KEY")
    bingx_api_secret: SecretStr | None = Field(default=None, alias="BINGX_API_SECRET")
    kraken_api_key: SecretStr | None = Field(default=None, alias="KRAKEN_API_KEY")
    kraken_api_secret: SecretStr | None = Field(default=None, alias="KRAKEN_API_SECRET")

    cache_ttl_sec: float = Field(default=60.0, alias="CACHE_TTL_SEC")
    fetch_timeout_sec: float = Field(default=1.5, alias="FETCH_TIMEOUT_SEC")
    wallet_timeout_sec: float = Field(default=2.5, alias="WALLET_TIMEOUT_SEC")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("default_quote")
    @classmethod
    def _upper_quote(cls, v: str) -> str:
        return v.strip().upper() or "USDT"

    def exchange_creds(self, exchange: str) -> dict[str, str] | None:
        mapping = {
            "bybit": (self.bybit_api_key, self.bybit_api_secret, None),
            "okx": (self.okx_api_key, self.okx_api_secret, self.okx_api_passphrase),
            "mexc": (self.mexc_api_key, self.mexc_api_secret, None),
            "bingx": (self.bingx_api_key, self.bingx_api_secret, None),
            "kraken": (self.kraken_api_key, self.kraken_api_secret, None),
        }
        row = mapping.get(exchange)
        if not row:
            return None
        key, secret, phrase = row
        if not key or not secret:
            return None
        out = {"key": key.get_secret_value(), "secret": secret.get_secret_value()}
        if phrase:
            out["passphrase"] = phrase.get_secret_value()
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(env_path):
        return Settings(_env_file=env_path)
    return Settings(_env_file=".env")
