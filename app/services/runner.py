from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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


logger = logging.getLogger(__name__)


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
                    browser_result.status == "login_expired"
                    and _auto_login_enabled(settings)
                ):
                    logger.info(
                        "Session expired before reward %s; trying credential login",
                        selected_id,
                    )
                    auto_status = self._perform_auto_login(settings)
                    logger.info(
                        "Credential login result: %s %s",
                        auto_status.state,
                        auto_status.message,
                    )
                    if auto_status.authenticated:
                        self._record_login_success(auto_status, now)
                        try:
                            browser_result = self.browser.execute_reward(
                                selected_id
                            )
                        except Exception as exc:
                            browser_result = BrowserResult(
                                status="failed",
                                message=f"浏览器执行异常：{exc}",
                                detail=json.dumps(
                                    {"error": str(exc)},
                                    ensure_ascii=False,
                                ),
                            )
                    else:
                        self._remember_auto_login_failure(
                            auto_status,
                            now,
                        )
                        browser_result = BrowserResult(
                            status="auto_login_failed",
                            message=auto_status.message,
                            detail=json.dumps(
                                {
                                    "auto_login": auto_status.raw,
                                },
                                ensure_ascii=False,
                            ),
                            login_status=auto_status,
                        )

                if (
                    browser_result.login_status
                    and browser_result.login_status.authenticated
                ):
                    self._record_login_success(
                        browser_result.login_status,
                        now,
                    )
                elif browser_result.status in {
                    "login_expired",
                    "auto_login_failed",
                }:
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
                    "auto_login_failed",
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
                    result.status in {
                        "login_expired",
                        "auto_login_failed",
                    }
                    for result in results
                ):
                    self.db.set_runtime("login_expired_notified", "1")
                auto_failures = [
                    result
                    for result in results
                    if result.status == "auto_login_failed"
                ]
                if auto_failures:
                    self.db.set_runtime(
                        "auto_login_failed_notified",
                        f"{run_date}|{auto_failures[0].message}",
                    )
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
                self._record_login_success(status, now)
                return status

            if _auto_login_enabled(settings):
                if self._auto_login_on_cooldown(now):
                    logger.info("Credential login skipped during cooldown")
                    return status
                logger.info("Login expired; trying QQ credential login")
                auto_status = self._perform_auto_login(settings)
                logger.info(
                    "Credential login result: %s %s",
                    auto_status.state,
                    auto_status.message,
                )
                if auto_status.authenticated:
                    self._record_login_success(auto_status, now)
                    self.db.set_runtime(
                        "last_auto_login",
                        now.isoformat(),
                    )
                    return auto_status
                self._remember_auto_login_failure(auto_status, now)
                if notify:
                    self._notify_auto_login_failure(
                        settings,
                        auto_status,
                        now,
                    )
                self.db.set_runtime(
                    "last_login_state",
                    "login_failed",
                )
                return auto_status

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

    def login_with_credentials(
        self,
        *,
        notify_failure: bool = True,
    ) -> LoginStatus:
        with self._login_lock:
            settings = get_settings(self.db, self.cipher)
            now = datetime.now(ZoneInfo(settings["timezone"]))
            if not _auto_login_enabled(settings):
                return LoginStatus(
                    state="login_failed",
                    message="请先保存 QQ 账号和密码并启用自动登录",
                    checked_at=now.isoformat(),
                )
            status = self._perform_auto_login(settings)
            logger.info(
                "Manual credential login result: %s %s",
                status.state,
                status.message,
            )
            self.db.set_runtime(
                "last_login_check",
                now.isoformat(),
            )
            if status.authenticated:
                self._record_login_success(status, now)
                self.db.set_runtime("last_auto_login", now.isoformat())
            else:
                self.db.set_runtime("last_login_state", "login_failed")
                self._remember_auto_login_failure(status, now)
                if notify_failure:
                    self._notify_auto_login_failure(settings, status, now)
            return status

    def request_login_verification(self) -> LoginStatus:
        with self._login_lock:
            settings = get_settings(self.db, self.cipher)
            now = datetime.now(ZoneInfo(settings["timezone"]))
            status = self.browser.request_login_verification()
            self.db.set_runtime(
                "last_login_check",
                now.isoformat(),
            )
            self.db.set_runtime("last_login_state", status.state)
            return status

    def submit_login_verification(self, code: str) -> LoginStatus:
        with self._login_lock:
            settings = get_settings(self.db, self.cipher)
            now = datetime.now(ZoneInfo(settings["timezone"]))
            status = self.browser.submit_login_verification(code)
            self.db.set_runtime(
                "last_login_check",
                now.isoformat(),
            )
            if status.authenticated:
                self._record_login_success(status, now)
            else:
                self.db.set_runtime("last_login_state", status.state)
            return status

    def _record_login_success(
        self,
        status: LoginStatus,
        now: datetime,
    ) -> None:
        mark_login(
            self.db,
            nickname=status.nickname,
            checked_at=now.isoformat(),
        )
        self.db.set_runtime("last_login_state", status.state)
        self.db.set_runtime("login_expired_notified", "0")
        self.db.set_runtime("auto_login_failed_notified", "")
        self.db.set_runtime("auto_login_cooldown_until", "")

    def _perform_auto_login(
        self,
        settings: dict[str, Any],
    ) -> LoginStatus:
        now = datetime.now(ZoneInfo(settings["timezone"]))
        try:
            status = self.browser.login_with_credentials(
                settings["qq_username"],
                settings["qq_password"],
            )
            if status.verification_required:
                logger.info(
                    "QQ security verification requires interactive window"
                )
                try:
                    interactive = self.browser.open_visible(
                        "qq",
                        settings["qq_username"],
                        settings["qq_password"],
                        submit=True,
                    )
                    if interactive.authenticated:
                        return interactive
                    return LoginStatus(
                        state="verification_required",
                        authenticated=False,
                        message=(
                            "登录窗口已打开，请完成图片验证码和手机号验证"
                        ),
                        checked_at=now.isoformat(),
                        verification_required=True,
                        verification_hint=(
                            "请在 Chromium 窗口中完成腾讯安全验证"
                        ),
                    )
                except Exception:
                    logger.exception(
                        "Unable to open interactive verification window"
                    )
                    return LoginStatus(
                        state="verification_required",
                        authenticated=False,
                        message=(
                            "QQ 需要人工安全验证，但完整窗口打开失败"
                        ),
                        checked_at=now.isoformat(),
                        verification_required=True,
                        verification_hint=(
                            "请在有桌面的本机启动程序后完成验证"
                        ),
                    )
            return status
        except Exception as exc:
            logger.exception("QQ credential login failed with exception")
            return LoginStatus(
                state="login_failed",
                message="QQ 自动登录执行失败，请打开完整窗口重试",
                checked_at=now.isoformat(),
                raw={"error_type": type(exc).__name__},
            )

    def _remember_auto_login_failure(
        self,
        status: LoginStatus,
        now: datetime,
    ) -> None:
        self.db.set_runtime(
            "auto_login_failure",
            json.dumps(
                {
                    "date": now.date().isoformat(),
                    "message": status.message,
                },
                ensure_ascii=False,
            ),
        )
        self.db.set_runtime(
            "auto_login_cooldown_until",
            (now + timedelta(minutes=30)).isoformat(),
        )

    def _auto_login_on_cooldown(self, now: datetime) -> bool:
        value = self.db.get_runtime("auto_login_cooldown_until")
        if not value:
            return False
        try:
            until = datetime.fromisoformat(value)
        except ValueError:
            return False
        if until.tzinfo is None:
            until = until.replace(tzinfo=now.tzinfo)
        return until > now

    def _notify_auto_login_failure(
        self,
        settings: dict[str, Any],
        status: LoginStatus,
        now: datetime,
    ) -> None:
        signature = f"{now.date().isoformat()}|{status.message}"
        if (
            self.db.get_runtime("auto_login_failed_notified")
            == signature
        ):
            return
        account = settings["account_label"] or "默认账号"
        title = (
            "需要人工完成 QQ 登录验证"
            if status.verification_required
            else "QQ 自动登录失败 · 王者奖励兑换"
        )
        content = "\n".join(
            [
                f"账号：{account}",
                f"QQ：{SecretCipher.mask(settings['qq_username'])}",
                f"结果：{status.message}",
                f"时间：{now.strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                (
                    "程序已打开或尝试打开可交互 Chromium 窗口。"
                    if status.verification_required
                    else "自动登录遇到验证码或安全校验时不会绕过。"
                ),
                "请在窗口中完成图片验证码、手机验证码或其他腾讯安全验证。",
            ]
        )
        send_notifications(settings, title, content)
        self.db.set_runtime("auto_login_failed_notified", signature)
        self.db.set_runtime("login_expired_notified", "1")

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
    elif any(item.status == "auto_login_failed" for item in results):
        title = "QQ 自动登录失败 · 王者奖励兑换"
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
    if any(item.status == "auto_login_failed" for item in results):
        lines.extend(
            [
                "",
                "自动登录遇到验证码或安全校验时不会自动绕过。",
                "请打开管理台扫码登录或使用完整窗口处理。",
            ]
        )
    elif any(item.status == "login_expired" for item in results):
        lines.extend(
            [
                "",
                "登录态已失效，请打开管理台重新扫码。",
            ]
        )
    return title, "\n".join(lines)


def _auto_login_enabled(settings: dict[str, Any]) -> bool:
    return bool(
        settings.get("qq_auto_login")
        and settings.get("qq_username")
        and settings.get("qq_password")
    )

