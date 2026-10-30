from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class PushResult:
    provider: str
    ok: bool
    detail: str


def send_notifications(
    settings: dict[str, Any],
    title: str,
    content: str,
    *,
    provider: str | None = None,
) -> list[PushResult]:
    results: list[PushResult] = []
    if settings.get("onebot_enabled") and provider in {None, "onebot"}:
        results.append(_send_onebot(settings, title, content))
    if settings.get("pushplus_enabled") and provider in {None, "pushplus"}:
        results.append(_send_pushplus(settings, title, content))
    if provider and not results:
        results.append(
            PushResult(provider, False, f"{provider} 尚未启用或配置不完整")
        )
    return results


def _send_onebot(
    settings: dict[str, Any],
    title: str,
    content: str,
) -> PushResult:
    base_url = str(settings.get("onebot_base_url") or "").rstrip("/")
    target_type = str(settings.get("onebot_target_type") or "private")
    target_id = str(settings.get("onebot_target_id") or "")
    if not base_url or not target_id:
        return PushResult("onebot", False, "OneBot 服务地址或接收目标未配置")

    payload: dict[str, Any] = {
        "message_type": target_type,
        "message": f"{title}\n{content}",
    }
    if target_type == "group":
        payload["group_id"] = target_id
    else:
        payload["user_id"] = target_id

    headers: dict[str, str] = {}
    token = str(settings.get("onebot_access_token") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    endpoint = (
        base_url
        if base_url.endswith("/send_msg")
        else f"{base_url}/send_msg"
    )
    try:
        response = httpx.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
        retcode = int(body.get("retcode", 0))
        failed = body.get("status") == "failed" or retcode != 0
        if failed:
            detail = body.get("message") or body.get("wording") or str(body)
            return PushResult("onebot", False, f"OneBot 返回异常：{detail}")
        return PushResult("onebot", True, "OneBot 已发送")
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return PushResult("onebot", False, f"OneBot 发送失败：{exc}")


def _send_pushplus(
    settings: dict[str, Any],
    title: str,
    content: str,
) -> PushResult:
    token = str(settings.get("pushplus_token") or "")
    if not token:
        return PushResult("pushplus", False, "PushPlus Token 未配置")
    try:
        response = httpx.post(
            "https://www.pushplus.plus/send",
            json={
                "token": token,
                "title": title,
                "content": content,
                "template": "markdown",
            },
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
        if int(body.get("code", -1)) != 200:
            return PushResult(
                "pushplus",
                False,
                f"PushPlus 返回异常：{body.get('msg') or body}",
            )
        return PushResult("pushplus", True, "PushPlus 已发送")
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return PushResult("pushplus", False, f"PushPlus 发送失败：{exc}")

