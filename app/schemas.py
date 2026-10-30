from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


def _validate_http_url(value: str, *, allow_empty: bool = True) -> str:
    value = value.strip()
    if not value and allow_empty:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请输入完整的 http:// 或 https:// 地址")
    return value


class SettingsPayload(BaseModel):
    account_label: str = Field(default="", max_length=80)
    scheduled_time: str = Field(
        default="10:05",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    auto_exchange: bool = False
    reward_id: int = Field(default=1, ge=1, le=6)

    onebot_enabled: bool = False
    onebot_base_url: str = Field(default="", max_length=2048)
    onebot_access_token: str = Field(default="", max_length=2048)
    clear_onebot_access_token: bool = False
    onebot_target_type: Literal["private", "group"] = "private"
    onebot_target_id: str = Field(default="", max_length=80)

    pushplus_enabled: bool = False
    pushplus_token: str = Field(default="", max_length=2048)
    clear_pushplus_token: bool = False

    @field_validator("account_label", "onebot_target_id")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("onebot_base_url")
    @classmethod
    def valid_onebot_url(cls, value: str) -> str:
        return _validate_http_url(value).rstrip("/")

    @model_validator(mode="after")
    def validate_channels(self) -> "SettingsPayload":
        if self.onebot_enabled:
            if not self.onebot_base_url or not self.onebot_target_id:
                raise ValueError("启用 OneBot 前请填写服务地址和接收目标")
        return self


class LoginStartPayload(BaseModel):
    platform: Literal["qq", "wechat"] = "qq"


class PlatformPayload(BaseModel):
    platform: Literal["qq", "wechat"]


class ProviderTestPayload(BaseModel):
    provider: Literal["onebot", "pushplus"]

