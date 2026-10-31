from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


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
    reward_ids: list[int] = Field(default_factory=lambda: [1])
    reward_id: int | None = Field(default=None, ge=1, le=6)

    qq_username: str = Field(default="", max_length=255)
    qq_password: SecretStr = Field(default_factory=lambda: SecretStr(""))
    qq_auto_login: bool = True
    clear_qq_credentials: bool = False

    onebot_enabled: bool = False
    onebot_base_url: str = Field(default="", max_length=2048)
    onebot_access_token: str = Field(default="", max_length=2048)
    clear_onebot_access_token: bool = False
    onebot_target_type: Literal["private", "group"] = "private"
    onebot_target_id: str = Field(default="", max_length=80)

    pushplus_enabled: bool = False
    pushplus_token: str = Field(default="", max_length=2048)
    clear_pushplus_token: bool = False

    @field_validator("account_label", "onebot_target_id", "qq_username")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("reward_ids")
    @classmethod
    def validate_reward_ids(cls, values: list[int]) -> list[int]:
        normalized = list(dict.fromkeys(values))
        if any(value < 1 or value > 6 for value in normalized):
            raise ValueError("奖励编号必须在 1 到 6 之间")
        return normalized

    @field_validator("qq_password")
    @classmethod
    def validate_qq_password(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) > 512:
            raise ValueError("QQ 密码长度不能超过 512 个字符")
        return value

    @field_validator("onebot_base_url")
    @classmethod
    def valid_onebot_url(cls, value: str) -> str:
        return _validate_http_url(value).rstrip("/")

    @model_validator(mode="after")
    def validate_channels(self) -> "SettingsPayload":
        if (
            self.reward_id is not None
            and "reward_ids" not in self.model_fields_set
        ):
            self.reward_ids = [self.reward_id]
        if not self.reward_ids and self.reward_id is not None:
            self.reward_ids = [self.reward_id]
        if not self.reward_ids:
            raise ValueError("请至少选择一项兑换奖励")
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



