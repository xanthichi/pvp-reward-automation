from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.database import Database
from app.rewards import REWARDS, get_reward
from app.schemas import (
    LoginStartPayload,
    PlatformPayload,
    ProviderTestPayload,
    SettingsPayload,
    VerificationCodePayload,
)
from app.security import SecretCipher
from app.services.browser import BrowserWorker
from app.services.push import send_notifications
from app.services.runner import ExchangeRunner
from app.services.scheduler import ExchangeScheduler, next_run_time
from app.services.settings import (
    get_public_settings,
    get_settings,
    mark_login,
    save_settings,
)


def create_app(
    *,
    data_dir: str | Path | None = None,
    browser: Any | None = None,
    disable_scheduler: bool | None = None,
) -> FastAPI:
    package_root = Path(__file__).resolve().parent.parent
    configured_data_dir = Path(
        data_dir or os.getenv("PVP_DATA_DIR", package_root / "data")
    )
    configured_data_dir.mkdir(parents=True, exist_ok=True)
    static_dir = Path(__file__).parent / "static"
    scheduler_disabled = bool(disable_scheduler)

    db = Database(configured_data_dir / "pvp-reward.db")
    cipher = SecretCipher(configured_data_dir)
    browser_worker = browser or BrowserWorker(
        configured_data_dir,
        headless=os.getenv("PVP_HEADLESS", "1") != "0",
    )
    runner = ExchangeRunner(db, cipher, browser_worker)
    scheduler = ExchangeScheduler(
        db,
        cipher,
        runner,
        enabled=not scheduler_disabled,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        scheduler.start()
        yield
        scheduler.stop()
        close = getattr(browser_worker, "close", None)
        if callable(close):
            close()

    app = FastAPI(
        title="王者奖励兑换",
        version="1.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.db = db
    app.state.cipher = cipher
    app.state.runner = runner
    app.state.browser = browser_worker
    app.state.scheduler = scheduler

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/dashboard")
    def dashboard() -> dict[str, Any]:
        settings = get_settings(db, cipher)
        public = get_public_settings(db, cipher)
        timezone = ZoneInfo(settings["timezone"])
        now = datetime.now(timezone)
        recent_rows = db.query_all(
            "SELECT * FROM exchange_logs ORDER BY id DESC LIMIT 20"
        )
        recent = [_exchange_public(row) for row in recent_rows]
        today_rows = db.query_all(
            """
            SELECT * FROM exchange_logs
            WHERE run_date = ?
            ORDER BY id ASC
            """,
            (now.date().isoformat(),),
        )
        cached_login = {
            "state": db.get_runtime("last_login_state", "unknown"),
            "checked_at": db.get_runtime("last_login_check"),
            "authenticated": db.get_runtime("last_login_state")
            in {"logged_in", "needs_binding"},
            "bound": db.get_runtime("last_login_state") == "logged_in",
        }
        next_run = next_run_time(db, settings, now)
        return {
            "server_time": now.isoformat(),
            "timezone": settings["timezone"],
            "settings": public,
            "rewards": list(REWARDS),
            "today": [_exchange_public(row) for row in today_rows],
            "recent": recent,
            "login": cached_login,
            "next_run": next_run.isoformat() if next_run else None,
            "runtime": {
                "last_login_check": db.get_runtime("last_login_check"),
                "last_login_monitor": db.get_runtime("last_login_monitor"),
                "scheduler_last_tick": db.get_runtime("scheduler_last_tick"),
                "scheduler_last_error": db.get_runtime(
                    "scheduler_last_error"
                ),
            },
        }

    @app.put("/api/settings")
    def update_settings(payload: SettingsPayload) -> dict[str, bool]:
        save_settings(db, cipher, payload)
        return {"ok": True}

    @app.post("/api/login/start")
    def start_login(
        payload: LoginStartPayload = LoginStartPayload(),
    ) -> dict[str, Any]:
        try:
            status = browser_worker.open_login(payload.platform)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"无法打开目标页登录框：{exc}",
            ) from None
        _remember_login(db, status)
        return {
            "ok": True,
            "login": status.public(),
            "screenshot_url": f"/api/login/screenshot?t={datetime.now().timestamp()}",
        }

    @app.get("/api/login/screenshot")
    def login_screenshot() -> Response:
        try:
            image = browser_worker.screenshot()
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"登录画面读取失败：{exc}",
            ) from None
        return Response(
            content=image,
            media_type="image/png",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.post("/api/login/switch")
    def switch_login(payload: PlatformPayload) -> dict[str, Any]:
        try:
            status = browser_worker.switch_platform(payload.platform)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"切换登录方式失败：{exc}",
            ) from None
        _remember_login(db, status)
        return {"ok": True, "login": status.public()}

    @app.get("/api/login/status")
    def login_status() -> dict[str, Any]:
        try:
            status = browser_worker.get_active_status()
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"登录状态读取失败：{exc}",
            ) from None
        _remember_login(db, status)
        return {"ok": True, "login": status.public()}

    @app.post("/api/login/check")
    def check_login() -> dict[str, Any]:
        status = runner.check_login(notify=False)
        return {"ok": status.state != "error", "login": status.public()}

    @app.post("/api/login/password")
    def password_login() -> dict[str, Any]:
        status = runner.login_with_credentials(notify_failure=True)
        if status.authenticated:
            _remember_login(db, status)
        return {
            "ok": status.authenticated,
            "login": status.public(),
        }

    @app.post("/api/login/verification/send")
    def send_login_verification() -> dict[str, Any]:
        status = runner.request_login_verification()
        return {
            "ok": status.state == "verification_required",
            "login": status.public(),
        }

    @app.post("/api/login/verification/submit")
    def submit_login_verification(
        payload: VerificationCodePayload,
    ) -> dict[str, Any]:
        status = runner.submit_login_verification(payload.code)
        if status.authenticated:
            _remember_login(db, status)
        return {
            "ok": status.authenticated,
            "login": status.public(),
        }

    @app.post("/api/login/close")
    def close_login() -> dict[str, bool]:
        browser_worker.close_session()
        return {"ok": True}

    @app.post("/api/browser/visible")
    def open_visible_browser(
        payload: LoginStartPayload = LoginStartPayload(),
    ) -> dict[str, Any]:
        try:
            status = browser_worker.open_visible(payload.platform)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"完整浏览器窗口打开失败：{exc}",
            ) from None
        _remember_login(db, status)
        return {"ok": True, "login": status.public()}

    @app.post("/api/exchange")
    def exchange_now() -> dict[str, Any]:
        results = runner.execute_batch(trigger="manual")
        statuses = [item.status for item in results]
        if not results:
            summary = "今日所选奖励已经处理"
        elif all(status == "success" for status in statuses):
            summary = f"{len(results)} 项奖励兑换完成"
        else:
            summary = (
                f"{sum(status == 'success' for status in statuses)}"
                f"/{len(results)} 项成功"
            )
        return {
            "ok": not results or all(
                status == "success" for status in statuses
            ),
            "summary": summary,
            "exchanges": [item.public() for item in results],
            "exchange": results[0].public() if results else None,
        }

    @app.get("/api/exchanges/{log_id}/screenshot")
    def exchange_screenshot(log_id: int) -> FileResponse:
        path = runner.screenshot_path_for_log(log_id)
        if path is None:
            raise HTTPException(status_code=404, detail="截图不存在")
        return FileResponse(path)

    @app.post("/api/push/test")
    def test_push(payload: ProviderTestPayload) -> dict[str, Any]:
        settings = get_settings(db, cipher)
        reward = get_reward(int(settings["reward_id"]))
        results = send_notifications(
            settings,
            "王者奖励兑换 · 推送测试",
            "\n".join(
                [
                    "这是一条推送连通性测试。",
                    f"当前奖励：{reward['name']}",
                    f"计划时间：{settings['scheduled_time']}",
                ]
            ),
            provider=payload.provider,
        )
        return {
            "ok": any(item.ok for item in results),
            "results": [
                {
                    "provider": item.provider,
                    "ok": item.ok,
                    "detail": item.detail,
                }
                for item in results
            ],
        }

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


def _remember_login(db: Database, status: Any) -> None:
    if status.authenticated:
        mark_login(
            db,
            nickname=status.nickname,
            checked_at=datetime.now().astimezone().isoformat(),
        )
        db.set_runtime("last_login_state", status.state)
        db.set_runtime("last_login_check", status.checked_at)
        db.set_runtime("login_expired_notified", "0")
    else:
        db.set_runtime("last_login_state", status.state)
        db.set_runtime("last_login_check", status.checked_at)


def _exchange_public(row: Any) -> dict[str, Any]:
    try:
        channels = json.loads(str(row["channels"] or "[]"))
    except json.JSONDecodeError:
        channels = []
    return {
        "id": int(row["id"]),
        "status": str(row["status"]),
        "message": str(row["message"]),
        "reward_id": int(row["reward_id"]),
        "reward_name": str(row["reward_name"]),
        "trigger": str(row["trigger"]),
        "package_name": str(row["package_name"]),
        "detail": str(row["detail"]),
        "screenshot_url": (
            f"/api/exchanges/{row['id']}/screenshot"
            if row["screenshot_path"]
            else ""
        ),
        "created_at": str(row["created_at"]),
        "channels": channels,
    }


app = create_app()

