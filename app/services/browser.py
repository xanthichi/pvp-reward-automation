from __future__ import annotations

import json
import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from app.rewards import TARGET_PAGE, get_reward


@dataclass
class LoginStatus:
    state: str
    authenticated: bool = False
    bound: bool = False
    nickname: str = ""
    partition: str = ""
    experience: str = ""
    message: str = ""
    checked_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "authenticated": self.authenticated,
            "bound": self.bound,
            "nickname": self.nickname,
            "partition": self.partition,
            "experience": self.experience,
            "message": self.message,
            "checked_at": self.checked_at,
        }


@dataclass
class BrowserResult:
    status: str
    message: str
    package_name: str = ""
    detail: str = ""
    screenshot_path: str = ""
    login_status: LoginStatus | None = None


class BrowserWorker:
    """Serializes all Playwright work on one thread and keeps the profile hot."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        headless: bool = True,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.profile_root = self.data_dir / "browser-profile"
        self.artifact_root = self.data_dir / "artifacts"
        self.profile_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.headless = headless

        self._queue: queue.Queue[
            tuple[
                Future[Any] | None,
                Callable[..., Any] | None,
                tuple[Any, ...],
                dict[str, Any],
            ]
        ] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="pvp-browser-worker",
            daemon=True,
        )
        self._thread.start()
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None

    def _run(self) -> None:
        try:
            with sync_playwright() as playwright:
                self._playwright = playwright
                while True:
                    future, method, args, kwargs = self._queue.get()
                    if method is None:
                        assert future is not None
                        future.set_result(None)
                        break
                    assert future is not None
                    try:
                        future.set_result(method(*args, **kwargs))
                    except BaseException as exc:
                        future.set_exception(exc)
        finally:
            self._shutdown_context()

    def _call(
        self,
        method: Callable[..., Any],
        *args: Any,
        timeout: float = 120,
        **kwargs: Any,
    ) -> Any:
        future: Future[Any] = Future()
        self._queue.put((future, method, args, kwargs))
        return future.result(timeout=timeout)

    def _ensure_context(self, *, visible: bool | None = None) -> Any:
        if self._context is not None:
            try:
                _ = self._context.pages
                return self._context
            except PlaywrightError:
                self._context = None
                self._page = None

        headless = self.headless if visible is None else not visible
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_root),
            headless=headless,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1365, "height": 900},
            args=["--disable-dev-shm-usage"],
        )
        self._context.set_default_timeout(20_000)
        return self._context

    def _ensure_page(self) -> Any:
        context = self._ensure_context()
        if self._page is not None and not self._page.is_closed():
            return self._page
        pages = context.pages
        self._page = pages[0] if pages else context.new_page()
        return self._page

    def _load_target(self, page: Any) -> None:
        page.goto(TARGET_PAGE, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(4_500)

    def _read_status(self, page: Any) -> LoginStatus:
        data = page.evaluate(
            """
            () => {
              const visible = (selector) => {
                const element = document.querySelector(selector);
                if (!element) return false;
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              const text = (selector) => {
                const element = document.querySelector(selector);
                return element ? element.textContent.trim() : '';
              };
              const authenticated = window.is_login === 1 && visible('#logined');
              const bound = authenticated
                && (window.is_bind === 1 || visible('#reBind'));
              return {
                authenticated,
                bound,
                login_form_visible: visible('#unlogin')
                  || Boolean(document.querySelector('.qcwx-login')),
                nickname: text('#login_qq_span'),
                partition: text('.my_put_check_text'),
                experience: text('.expscore'),
                is_login: Number(window.is_login || 0),
                is_bind: Number(window.is_bind || 0),
                ready_state: document.readyState
              };
            }
            """
        )
        now = datetime.now().astimezone().isoformat()
        if data.get("authenticated"):
            return LoginStatus(
                state="logged_in" if data.get("bound") else "needs_binding",
                authenticated=True,
                bound=bool(data.get("bound")),
                nickname=str(data.get("nickname") or ""),
                partition=str(data.get("partition") or ""),
                experience=str(data.get("experience") or ""),
                message=(
                    "登录有效"
                    if data.get("bound")
                    else "已登录，但尚未绑定正式服账号"
                ),
                checked_at=now,
                raw=data,
            )
        return LoginStatus(
            state="login_required",
            authenticated=False,
            message="登录已过期或尚未登录",
            checked_at=now,
            raw=data,
        )

    def open_login(self, platform: str = "qq") -> LoginStatus:
        return self._call(self._open_login, platform, timeout=90)

    def _open_login(self, platform: str) -> LoginStatus:
        page = self._ensure_page()
        if not page.url.startswith("https://pvp.qq.com/"):
            self._load_target(page)
        status = self._read_status(page)
        if status.authenticated:
            return status

        overlay = page.locator(".qcwx-login")
        if overlay.count() == 0 or not overlay.first.is_visible():
            if page.locator("#dologin").count():
                page.locator("#dologin").click()
                page.wait_for_timeout(2_500)

        if platform == "wechat":
            self._switch_platform(page, "wechat")
        return self._read_status(page)

    def switch_platform(self, platform: str) -> LoginStatus:
        return self._call(self._switch_login, platform, timeout=45)

    def _switch_login(self, platform: str) -> LoginStatus:
        page = self._ensure_page()
        if not page.url.startswith("https://pvp.qq.com/"):
            self._load_target(page)
        self._switch_platform(page, platform)
        return self._read_status(page)

    @staticmethod
    def _switch_platform(page: Any, platform: str) -> bool:
        label = "微信账号登录" if platform == "wechat" else "QQ账号登录"
        try:
            locator = page.locator(".qcwx-login").get_by_text(
                label,
                exact=True,
            ).first
            locator.click(timeout=5_000)
            page.wait_for_timeout(600)
            return True
        except PlaywrightError:
            return False

    def screenshot(self) -> bytes:
        return self._call(self._screenshot, timeout=90)

    def _screenshot(self) -> bytes:
        page = self._ensure_page()
        if not page.url.startswith("https://pvp.qq.com/"):
            self._open_login("qq")
        return page.screenshot(type="png", full_page=False)

    def open_visible(self, platform: str = "qq") -> LoginStatus:
        return self._call(
            self._open_visible,
            platform,
            timeout=90,
        )

    def _open_visible(self, platform: str) -> LoginStatus:
        self._close_session()
        page = self._ensure_page_with_mode(visible=True)
        self._load_target(page)
        status = self._read_status(page)
        if status.authenticated:
            return status
        if page.locator("#dologin").count():
            page.locator("#dologin").click()
            page.wait_for_timeout(2_000)
        if platform == "wechat":
            self._switch_platform(page, "wechat")
        return self._read_status(page)

    def _ensure_page_with_mode(self, *, visible: bool) -> Any:
        context = self._ensure_context(visible=visible)
        if self._page is not None and not self._page.is_closed():
            return self._page
        pages = context.pages
        self._page = pages[0] if pages else context.new_page()
        return self._page

    def get_active_status(self) -> LoginStatus:
        return self._call(self._active_status, timeout=45)

    def _active_status(self) -> LoginStatus:
        page = self._ensure_page()
        if not page.url.startswith("https://pvp.qq.com/"):
            self._load_target(page)
        return self._read_status(page)

    def check_login(self) -> LoginStatus:
        return self._call(self._check_login, timeout=90)

    def _check_login(self) -> LoginStatus:
        page = self._ensure_page()
        self._load_target(page)
        page.wait_for_timeout(1_500)
        return self._read_status(page)

    def close_session(self) -> None:
        self._call(self._close_session, timeout=30)

    def _close_session(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except PlaywrightError:
                pass
        self._context = None
        self._page = None

    def execute_reward(self, reward_id: int) -> BrowserResult:
        return self._call(
            self._execute_reward,
            int(reward_id),
            timeout=110,
        )

    def _execute_reward(self, reward_id: int) -> BrowserResult:
        reward = get_reward(reward_id)
        page = self._ensure_page()
        dialogs: list[str] = []
        screenshot_path = ""

        def on_dialog(dialog: Any) -> None:
            dialogs.append(dialog.message)
            try:
                dialog.accept()
            except PlaywrightError:
                pass

        page.on("dialog", on_dialog)
        try:
            self._load_target(page)
            page.wait_for_timeout(1_500)
            login_status = self._read_status(page)
            if not login_status.authenticated:
                screenshot_path = self._save_artifact(page, reward_id, "login")
                return BrowserResult(
                    status="login_expired",
                    message="登录已过期，请重新扫码登录",
                    detail=json.dumps(
                        {"dialogs": dialogs, "login": login_status.raw},
                        ensure_ascii=False,
                    ),
                    screenshot_path=screenshot_path,
                    login_status=login_status,
                )
            if not login_status.bound:
                screenshot_path = self._save_artifact(page, reward_id, "bind")
                return BrowserResult(
                    status="needs_binding",
                    message="账号尚未绑定正式服，请先在目标页完成绑定",
                    detail=json.dumps(
                        {"dialogs": dialogs, "login": login_status.raw},
                        ensure_ascii=False,
                    ),
                    screenshot_path=screenshot_path,
                    login_status=login_status,
                )

            page.wait_for_function(
                "() => typeof window.amsCfg_407551 !== 'undefined'",
                timeout=15_000,
            )
            installed = page.evaluate(
                """
                () => {
                  window.__pvpAutoResult = null;
                  let installed = 0;
                  const capture = (kind) => (payload) => {
                    window.__pvpAutoResult = {
                      kind,
                      ret: Number(payload && payload.ret || 0),
                      message: String(payload && payload.sMsg || ''),
                      packageName: String(payload && payload.sPackageName || ''),
                      hasPackage: Boolean(payload && payload.sPackageName)
                    };
                  };
                  for (let id = 407551; id <= 407556; id += 1) {
                    const config = window['amsCfg_' + id];
                    if (!config) continue;
                    config.onGetGiftSuccessEvent = capture('success');
                    config.onGetGiftFailureEvent = capture('failure');
                    installed += 1;
                  }
                  return installed;
                }
                """
            )
            if not installed:
                raise RuntimeError("目标页兑换脚本尚未就绪")

            button = page.locator(
                f'.myexchange[value="{int(reward_id)}"]'
            ).first
            button.scroll_into_view_if_needed()
            button.click()
            try:
                page.wait_for_function(
                    "() => window.__pvpAutoResult !== null",
                    timeout=35_000,
                )
            except PlaywrightTimeoutError:
                page.wait_for_timeout(1_000)

            outcome = page.evaluate("() => window.__pvpAutoResult")
            screenshot_path = self._save_artifact(
                page,
                reward_id,
                "result",
            )
            if not outcome:
                message = dialogs[-1] if dialogs else ""
                if _mentions_login(message):
                    status = "login_expired"
                    message = message or "登录已过期，请重新扫码登录"
                elif "绑定" in message:
                    status = "needs_binding"
                    message = message or "请先完成正式服账号绑定"
                else:
                    status = "failed"
                    message = message or "目标页未返回兑换结果"
                return BrowserResult(
                    status=status,
                    message=message,
                    detail=json.dumps(
                        {"dialogs": dialogs, "outcome": None},
                        ensure_ascii=False,
                    ),
                    screenshot_path=screenshot_path,
                    login_status=login_status,
                )

            package_name = str(outcome.get("packageName") or "")
            message = str(outcome.get("message") or "")
            kind = str(outcome.get("kind") or "")
            safe_detail = json.dumps(
                {"outcome": outcome, "dialogs": dialogs},
                ensure_ascii=False,
            )
            if kind == "success" or package_name:
                return BrowserResult(
                    status="success",
                    message=message or (
                        f"兑换成功：{package_name}"
                        if package_name
                        else "兑换成功"
                    ),
                    package_name=package_name,
                    detail=safe_detail,
                    screenshot_path=screenshot_path,
                    login_status=login_status,
                )

            if _mentions_already(message):
                return BrowserResult(
                    status="success",
                    message=message or "今日奖励已经兑换",
                    detail=safe_detail,
                    screenshot_path=screenshot_path,
                    login_status=login_status,
                )
            if _mentions_login(message):
                return BrowserResult(
                    status="login_expired",
                    message=message or "登录已过期，请重新扫码登录",
                    detail=safe_detail,
                    screenshot_path=screenshot_path,
                    login_status=login_status,
                )
            return BrowserResult(
                status="failed",
                message=message or "目标页拒绝了兑换请求",
                detail=safe_detail,
                screenshot_path=screenshot_path,
                login_status=login_status,
            )
        except (PlaywrightError, RuntimeError, ValueError) as exc:
            screenshot_path = self._save_artifact_safe(page, reward_id)
            message = (
                "等待目标页响应超时"
                if isinstance(exc, PlaywrightTimeoutError)
                else f"浏览器执行失败：{exc}"
            )
            return BrowserResult(
                status="failed",
                message=message,
                detail=json.dumps(
                    {"error": str(exc), "dialogs": dialogs},
                    ensure_ascii=False,
                ),
                screenshot_path=screenshot_path,
            )
        finally:
            try:
                page.remove_listener("dialog", on_dialog)
            except PlaywrightError:
                pass

    def _save_artifact(
        self,
        page: Any,
        reward_id: int,
        phase: str,
    ) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.artifact_root / (
            f"{stamp}-reward-{int(reward_id)}-{phase}.png"
        )
        page.screenshot(path=str(path), full_page=False)
        return str(path)

    def _save_artifact_safe(self, page: Any, reward_id: int) -> str:
        try:
            return self._save_artifact(page, reward_id, "error")
        except PlaywrightError:
            return ""

    def close(self) -> None:
        future: Future[Any] = Future()
        self._queue.put((future, None, (), {}))
        try:
            future.result(timeout=10)
        except Exception:
            pass
        self._thread.join(timeout=10)

    def _shutdown_context(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except PlaywrightError:
                pass


def _mentions_login(message: str) -> bool:
    text = message.lower()
    return any(
        token in text
        for token in ("登录", "过期", "login", "未授权", "重新扫码")
    )


def _mentions_already(message: str) -> bool:
    return any(
        token in message
        for token in ("已经兑换", "已兑换", "兑换过了", "今日已", "明天再来")
    )

