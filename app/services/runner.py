from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.database import Database
from app.rewards import get_reward
from app.security import SecretCipher
from app.services.browser import BrowserResult, BrowserWorker, LoginStatus
from app.services.push import PushResult, send_notifications
from app.services.settings import (
    get_settings,
    mark_login,
)


@dataclass
class ExchangeResult:
    status: str
    message: str
    reward_id: int
    reward_name: str
    trigger: str
    package_name: str = ""
    detail: str = ""
    screenshot_path: str = ""
    log_id: int = 0
    created_at: str = ""
    channels: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.log_id,
            "status": self.status,
            "message": self.message,
            "reward_id": self.reward_id,
            "reward_name": self.reward_name,
            "trigger": self.trigger,
            "package_name": self.package_name,
            "detail": self.detail,
            "screenshot_url": (
                f"/api/exchanges/{self.log_id}/screenshot"
                if self.log_id and self.screenshot_path
                else ""
            ),
            "created_at": self.created_at,
            "channels": self.channels,
        }


class ExchangeRunner:
    def __init__(
        self,
        db: Database,
        cipher: SecretCipher,
        browser: BrowserWorker,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.browser = browser
        self.artifact_root = browser.artifact_root
        self._exchange_lock = threading.Lock()
        self._login_lock = threading.Lock()

    def execute(
        self,
        *,
        trigger: str,
        reward_id: int | None = None,
        notify: bool = True,
    ) -> ExchangeResult:
        results = self.execute_batch(
            trigger=trigger,
            reward_ids=[reward_id] if reward_id is not None else None,
            notify=notify,
        )
        if results:
            return results[0]
        settings = get_settings(self.db, self.cipher)
        selected_id = int(reward_id or settings["reward_ids"][0])
        row = self.db.query_one(
            """
            SELECT * FROM exchange_logs
            WHERE run_date = ? AND reward_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (datetime.now(ZoneInfo(settings["timezone"])).date().isoformat(), selected_id),
        )
        if row:
            return self._result_from_row(row)
        reward = get_reward(selected_id)
        return ExchangeResult(
            status="skipped",
            message="该奖励今日已经处理",
            reward_id=selected_id,
            reward_name=str(reward["name"]),
            trigger=trigger,
        )

    def execute_batch(
        self,
        *,
        trigger: str,
        reward_ids: list[int] | None = None,
        notify: bool = True,
    ) -> list[ExchangeResult]:
        with self._exchange_lock:
            settings = get_settings(self.db, self.cipher)
            timezone = ZoneInfo(settings["timezone"])
            now = datetime.now(timezone)
            run_date = now.date().isoformat()
            selected_ids = list(dict.fromkeys(
                reward_ids if reward_ids is not None else settings["reward_ids"]
            ))
            results: list[ExchangeResult] = []
            abort_daily_run = False

            for selected_id in selected_ids:
                existing = self._today_logs(run_date, selected_id)
                if existing and trigger == "schedule":
                    continue
                if existing and any(
                    row["status"] == "success" for row in existing
                ):
                    continue

                reward = get_reward(selected_id)
                try:
                    browser_result = self.browser.execute_reward(selected_id)
                except Exception as exc:
                    browser_result = BrowserResult(
                        status="failed",
                        message=f"浏览器执行异常：{exc}",
                        detail=json.dumps(
                            {"error": str(exc)},
                            ensure_ascii=False,
                        ),
                    )

                if (
                    browser_result.login_status
                    and browser_result.login_status.authenticated
                ):
                    mark_login(
                        self.db,
                        nickname=browser_result.login_status.nickname,
                        checked_at=now.isoformat(),
                    )
                    self.db.set_runtime(
                        "last_login_state",
                        browser_result.login_status.state,
                    )
                    self.db.set_runtime("login_expired_notified", "0")
                elif browser_result.status == "login_expired":
                    self.db.set_runtime("last_login_state", "login_required")

                created_at = now.isoformat()
                log_id = self.db.execute(
                    """
                    INSERT INTO exchange_logs (
                        run_date, reward_id, reward_name, trigger, status,
                        message, package_name, detail, screenshot_path,
                        channels, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)
                    """,
                    (
                        run_date,
                        selected_id,
                        str(reward["name"]),
                        trigger,
                        browser_result.status,
                        browser_result.message,
                        browser_result.package_name,
                        browser_result.detail[:20_000],
                        browser_result.screenshot_path,
                        created_at,
                    ),
                )
                results.append(
                    ExchangeResult(
                        status=browser_result.status,
                        message=browser_result.message,
                        reward_id=selected_id,
                        reward_name=str(reward["name"]),
                        trigger=trigger,
                        package_name=browser_result.package_name,
                        detail=browser_result.detail,
                        screenshot_path=browser_result.screenshot_path,
                        log_id=log_id,
                        created_at=created_at,
                    )
                )
                if browser_result.status in {
                    "login_expired",
                    "needs_binding",
                }:
                    abort_daily_run = True
                    break

            if abort_daily_run:
                self.db.set_runtime("daily_abort_date", run_date)
            elif results:
                self.db.set_runtime("daily_abort_date", "")

            if results and notify:
                title, content = _batch_notification(settings, results, now)
                channels = send_notifications(settings, title, content)
                channel_payload = [
                    {
                        "provider": item.provider,
                        "ok": item.ok,
                        "detail": item.detail,
                    }
                    for item in channels
                ]
                for result in results:
                    result.channels = channel_payload
                    self.db.execute(
                        "UPDATE exchange_logs SET channels = ? WHERE id = ?",
                        (
                            json.dumps(channel_payload, ensure_ascii=False),
                            result.log_id,
                        ),
                    )
                if any(
                    result.status == "login_expired" for result in results
                ):
                    self.db.set_runtime("login_expired_notified", "1")
            return results

    def check_login(self, *, notify: bool = True) -> LoginStatus:
        with self._login_lock:
            settings = get_settings(self.db, self.cipher)
            timezone = ZoneInfo(settings["timezone"])
            now = datetime.now(timezone)
            try:
                status = self.browser.check_login()
            except Exception as exc:
                status = LoginStatus(
                    state="error",
                    message=f"登录状态检查失败：{exc}",
                    checked_at=now.isoformat(),
                )
                self.db.set_runtime("last_login_check_error", status.message)
                return status

            self.db.set_runtime(
                "last_login_check",
                now.isoformat(),
            )
            self.db.set_runtime("last_login_state", status.state)
            if status.authenticated:
                mark_login(
                    self.db,
                    nickname=status.nickname,
                    checked_at=now.isoformat(),
                )
                self.db.set_runtime("login_expired_notified", "0")
                return status

            should_notify = (
                notify
                and bool(settings["last_login_at"])
                and self.db.get_runtime("login_expired_notified", "0") != "1"
            )
            if should_notify:
                title = "王者奖励兑换 · 登录已过期"
                content = "\n".join(
                    [
                        f"账号：{settings['account_label'] or '默认账号'}",
                        "状态：登录态已失效，今日自动兑换无法执行。",
                        f"检查时间：{now.strftime('%Y-%m-%d %H:%M:%S')}",
                        "",
                        "请打开管理台，点击“重新登录”并完成扫码。",
                    ]
                )
                send_notifications(settings, title, content)
                self.db.set_runtime("login_expired_notified", "1")
            return status

    def _today_logs(
        self,
        run_date: str,
        reward_id: int | None = None,
    ) -> list[Any]:
        if reward_id is None:
            return self.db.query_all(
                """
                SELECT * FROM exchange_logs
                WHERE run_date = ?
                ORDER BY id ASC
                """,
                (run_date,),
            )
        return self.db.query_all(
            """
            SELECT * FROM exchange_logs
            WHERE run_date = ? AND reward_id = ?
            ORDER BY id ASC
            """,
            (run_date, reward_id),
        )

    def _result_from_row(self, row: Any) -> ExchangeResult:
        try:
            channels = json.loads(str(row["channels"] or "[]"))
        except json.JSONDecodeError:
            channels = []
        return ExchangeResult(
            status=str(row["status"]),
            message=str(row["message"]),
            reward_id=int(row["reward_id"]),
            reward_name=str(row["reward_name"]),
            trigger=str(row["trigger"]),
            package_name=str(row["package_name"]),
            detail=str(row["detail"]),
            screenshot_path=str(row["screenshot_path"]),
            log_id=int(row["id"]),
            created_at=str(row["created_at"]),
            channels=channels,
        )

    def screenshot_path_for_log(self, log_id: int) -> Path | None:
        row = self.db.query_one(
            "SELECT screenshot_path FROM exchange_logs WHERE id = ?",
            (log_id,),
        )
        if not row or not row["screenshot_path"]:
            return None
        path = Path(str(row["screenshot_path"])).resolve()
        root = self.artifact_root.resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path


def _batch_notification(
    settings: dict[str, Any],
    results: list[ExchangeResult],
    now: datetime,
) -> tuple[str, str]:
    account = settings["account_label"] or "默认账号"
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    successful = sum(item.status == "success" for item in results)
    if successful == len(results):
        title = f"兑换完成 · {successful} 项"
    elif any(item.status == "login_expired" for item in results):
        title = "王者奖励兑换 · 登录已过期"
    elif any(item.status == "needs_binding" for item in results):
        title = "兑换失败 · 尚未绑定正式服"
    else:
        title = f"兑换结果 · {successful}/{len(results)} 项成功"

    lines = [
        f"账号：{account}",
        f"时间：{stamp}",
        "",
    ]
    for item in results:
        package = f"，获得 {item.package_name}" if item.package_name else ""
        lines.append(
            f"- {item.reward_name}：{item.message}{package}"
        )
    if any(item.status == "login_expired" for item in results):
        lines.extend(
            [
                "",
                "登录态已失效，请打开管理台重新扫码。",
            ]
        )
    return title, "\n".join(lines)

