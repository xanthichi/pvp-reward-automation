from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.database import Database
from app.schemas import SettingsPayload
from app.security import SecretCipher
from app.services.browser import BrowserResult, LoginStatus
from app.services.runner import ExchangeRunner
from app.services.scheduler import ExchangeScheduler
from app.services.settings import save_settings


def test_scheduler_executes_once_per_day(
    tmp_path: Path,
    fake_browser,
) -> None:
    db = Database(tmp_path / "test.db")
    cipher = SecretCipher(tmp_path)
    save_settings(
        db,
        cipher,
        SettingsPayload(
            auto_exchange=True,
            scheduled_time="00:00",
            reward_ids=[2, 4],
        ),
    )
    runner = ExchangeRunner(db, cipher, fake_browser)
    scheduler = ExchangeScheduler(
        db,
        cipher,
        runner,
        enabled=False,
    )

    first = scheduler.tick()
    second = scheduler.tick()

    assert first["executed"] is True
    assert second["executed"] is False
    assert fake_browser.execute_calls == [2, 4]


def test_login_expiry_notification_is_sent_once(
    tmp_path: Path,
    fake_browser,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "test.db")
    cipher = SecretCipher(tmp_path)
    save_settings(
        db,
        cipher,
        SettingsPayload(
            account_label="主账号",
            pushplus_enabled=True,
            pushplus_token="token",
        ),
    )
    db.execute(
        "UPDATE settings SET last_login_at = ? WHERE id = 1",
        ("2026-10-29T10:00:00+08:00",),
    )
    fake_browser.login_status = LoginStatus(
        state="login_required",
        authenticated=False,
        message="登录已过期",
        checked_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    )

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.runner.send_notifications",
        lambda settings, title, content: sent.append((title, content)) or [],
    )
    runner = ExchangeRunner(db, cipher, fake_browser)

    runner.check_login(notify=True)
    runner.check_login(notify=True)

    assert len(sent) == 1
    assert "登录已过期" in sent[0][0]
    assert "重新登录" in sent[0][1]


def test_login_expiry_stops_remaining_rewards_for_scheduled_run(
    tmp_path: Path,
    fake_browser,
) -> None:
    db = Database(tmp_path / "test.db")
    cipher = SecretCipher(tmp_path)
    save_settings(
        db,
        cipher,
        SettingsPayload(
            auto_exchange=True,
            scheduled_time="00:00",
            reward_ids=[1, 2, 3],
        ),
    )
    fake_browser.results = [
        BrowserResult(
            status="login_expired",
            message="登录已过期",
            login_status=LoginStatus(
                state="login_required",
                authenticated=False,
                message="登录已过期",
            ),
        )
    ]
    runner = ExchangeRunner(db, cipher, fake_browser)
    scheduler = ExchangeScheduler(db, cipher, runner, enabled=False)

    first = scheduler.tick()
    second = scheduler.tick()

    assert first["count"] == 1
    assert second["executed"] is False
    assert fake_browser.execute_calls == [1]

