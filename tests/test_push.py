from __future__ import annotations

from typing import Any

from app.services.push import send_notifications


class FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


def test_pushplus_and_onebot_payloads(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def fake_post(url, *, json, headers=None, timeout):
        calls.append((url, json, headers or {}))
        if "pushplus" in url:
            return FakeResponse({"code": 200, "msg": "ok"})
        return FakeResponse({"status": "ok", "retcode": 0})

    monkeypatch.setattr("app.services.push.httpx.post", fake_post)
    settings = {
        "onebot_enabled": True,
        "onebot_base_url": "http://127.0.0.1:3000",
        "onebot_access_token": "token",
        "onebot_target_type": "group",
        "onebot_target_id": "9988",
        "pushplus_enabled": True,
        "pushplus_token": "push-token",
    }

    results = send_notifications(settings, "兑换成功", "获得亲密玫瑰*1")

    assert all(item.ok for item in results)
    assert calls[0][0] == "http://127.0.0.1:3000/send_msg"
    assert calls[0][1]["group_id"] == "9988"
    assert calls[0][2]["Authorization"] == "Bearer token"
    assert calls[1][0] == "https://www.pushplus.plus/send"
    assert calls[1][1]["token"] == "push-token"

