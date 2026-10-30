from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services.browser import BrowserResult, LoginStatus


class FakeBrowser:
    def __init__(self, data_dir: Path) -> None:
        self.artifact_root = data_dir / "artifacts"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.execute_calls: list[int] = []
        self.close_count = 0
        self.result = BrowserResult(
            status="success",
            message="兑换成功",
            package_name="亲密玫瑰*1",
            login_status=LoginStatus(
                state="logged_in",
                authenticated=True,
                bound=True,
                nickname="测试召唤师",
                checked_at="2026-10-30T10:00:00+08:00",
            ),
        )
        self.login_status = LoginStatus(
            state="logged_in",
            authenticated=True,
            bound=True,
            nickname="测试召唤师",
            checked_at="2026-10-30T10:00:00+08:00",
        )
        self.results: list[BrowserResult] = []

    def execute_reward(self, reward_id: int) -> BrowserResult:
        self.execute_calls.append(reward_id)
        if self.results:
            return self.results.pop(0)
        return self.result

    def check_login(self) -> LoginStatus:
        return self.login_status

    def open_login(self, platform: str = "qq") -> LoginStatus:
        return self.login_status

    def switch_platform(self, platform: str) -> LoginStatus:
        return self.login_status

    def get_active_status(self) -> LoginStatus:
        return self.login_status

    def screenshot(self) -> bytes:
        return b"\x89PNG\r\n\x1a\nfake"

    def close_session(self) -> None:
        return None

    def open_visible(self, platform: str = "qq") -> LoginStatus:
        return self.login_status

    def close(self) -> None:
        self.close_count += 1


@pytest.fixture
def fake_browser(tmp_path: Path) -> FakeBrowser:
    return FakeBrowser(tmp_path)

