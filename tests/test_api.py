from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.settings import get_settings


def test_dashboard_and_settings_are_stored_without_plaintext_secrets(
    tmp_path: Path,
    fake_browser,
) -> None:
    app = create_app(
        data_dir=tmp_path,
        browser=fake_browser,
        disable_scheduler=True,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        body = dashboard.json()
        assert len(body["rewards"]) == 6
        assert body["settings"]["scheduled_time"] == "10:05"

        response = client.put(
            "/api/settings",
            json={
                "account_label": "体验服主号",
                "scheduled_time": "09:30",
                "auto_exchange": True,
                "reward_ids": [2, 4, 5],
                "qq_username": "123456789",
                "qq_password": "qq-test-password",
                "qq_auto_login": True,
                "onebot_enabled": True,
                "onebot_base_url": "http://127.0.0.1:3000",
                "onebot_access_token": "onebot-secret",
                "onebot_target_type": "private",
                "onebot_target_id": "123456",
                "pushplus_enabled": True,
                "pushplus_token": "push-secret",
            },
        )
        assert response.status_code == 200

        public = client.get("/api/dashboard").json()["settings"]
        assert public["account_label"] == "体验服主号"
        assert public["reward_ids"] == [2, 4, 5]
        assert public["reward_id"] == 2
        assert public["qq"]["credentials_set"] is True
        assert public["qq"]["account_hint"].endswith("6789")
        assert "qq-test-password" not in str(public)
        assert public["pushplus"]["token_set"] is True
        assert public["onebot"]["access_token_set"] is True

    settings = get_settings(app.state.db, app.state.cipher)
    assert settings["pushplus_token"] == "push-secret"
    assert settings["onebot_access_token"] == "onebot-secret"
    assert settings["qq_username"] == "123456789"
    assert settings["qq_password"] == "qq-test-password"
    raw = Path(app.state.db.path).read_bytes()
    assert b"push-secret" not in raw
    assert b"onebot-secret" not in raw
    assert b"qq-test-password" not in raw
    assert b"123456789" not in raw


def test_exchange_is_successful_and_not_repeated_same_day(
    tmp_path: Path,
    fake_browser,
) -> None:
    app = create_app(
        data_dir=tmp_path,
        browser=fake_browser,
        disable_scheduler=True,
    )
    with TestClient(app) as client:
        saved = client.put(
            "/api/settings",
            json={"reward_ids": [2, 4]},
        )
        assert saved.status_code == 200
        first = client.post("/api/exchange")
        second = client.post("/api/exchange")

    assert first.status_code == 200
    assert len(first.json()["exchanges"]) == 2
    assert all(
        item["status"] == "success"
        for item in first.json()["exchanges"]
    )
    assert second.status_code == 200
    assert second.json()["exchanges"] == []
    assert fake_browser.execute_calls == [2, 4]


def test_login_and_settings_validation(
    tmp_path: Path,
    fake_browser,
) -> None:
    app = create_app(
        data_dir=tmp_path,
        browser=fake_browser,
        disable_scheduler=True,
    )
    with TestClient(app) as client:
        invalid_time = client.put(
            "/api/settings",
            json={"scheduled_time": "24:00"},
        )
        assert invalid_time.status_code == 422

        invalid_onebot = client.put(
            "/api/settings",
            json={
                "onebot_enabled": True,
                "onebot_base_url": "",
                "onebot_target_id": "",
            },
        )
        assert invalid_onebot.status_code == 422

        login = client.post("/api/login/check")
        assert login.status_code == 200
        assert login.json()["login"]["state"] == "logged_in"


def test_password_save_requires_local_or_https(
    tmp_path: Path,
    fake_browser,
) -> None:
    app = create_app(
        data_dir=tmp_path,
        browser=fake_browser,
        disable_scheduler=True,
    )
    with TestClient(app) as client:
        response = client.put(
            "/api/settings",
            json={
                "qq_username": "123456789",
                "qq_password": "secret",
            },
        )
    assert response.status_code == 400

