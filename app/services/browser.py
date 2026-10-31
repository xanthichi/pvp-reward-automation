from __future__ import annotations

import json
import logging
import os
import queue
import re
import socket
import threading
import time
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


logger = logging.getLogger(__name__)

_PROFILE_LOCK_TARGET = re.compile(r"^(?P<host>.+)-(?P<pid>\d+)$")


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
    verification_required: bool = False
    verification_hint: str = ""
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
            "verification_required": self.verification_required,
            "verification_hint": self.verification_hint,
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

        self._clear_stale_profile_locks()
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

    def _clear_stale_profile_locks(self) -> None:
        """Remove Chromium singleton files left by a previous container."""
        if os.name != "posix":
            return

        lock_path = self.profile_root / "SingletonLock"
        if not lock_path.is_symlink() and not lock_path.exists():
            return

        try:
            target = os.readlink(lock_path)
        except OSError as exc:
            logger.warning("Unable to inspect Chromium profile lock: %s", exc)
            return

        reason = _profile_lock_stale_reason(
            target,
            hostname=socket.gethostname(),
            pid_exists=_pid_exists,
            pid_is_browser=lambda pid: _pid_is_browser_for_profile(
                pid,
                self.profile_root,
            ),
        )
        if not reason:
            raise RuntimeError(
                "浏览器配置正在被另一个 Chromium 进程使用，请先停止重复启动的容器"
            )

        logger.warning(
            "Removing stale Chromium profile lock (%s): %s",
            reason,
            target,
        )
        for name in (
            "SingletonLock",
            "SingletonCookie",
            "SingletonSocket",
        ):
            path = self.profile_root / name
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    "Unable to remove stale profile lock %s: %s",
                    path,
                    exc,
                )

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

    def open_visible(
        self,
        platform: str = "qq",
        username: str = "",
        password: str = "",
        submit: bool = False,
    ) -> LoginStatus:
        return self._call(
            self._open_visible,
            platform,
            username,
            password,
            submit,
            timeout=90,
        )

    def _open_visible(
        self,
        platform: str,
        username: str,
        password: str,
        submit: bool,
    ) -> LoginStatus:
        self._close_session()
        page = None
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                page = self._ensure_page_with_mode(visible=True)
                break
            except (PlaywrightError, RuntimeError) as exc:
                last_error = exc
                logger.warning(
                    "Visible Chromium launch attempt %s failed: %s",
                    attempt + 1,
                    exc,
                )
                self._context = None
                self._page = None
                time.sleep(0.8)
        if page is None:
            raise RuntimeError(
                f"无法启动可交互 Chromium：{last_error}"
            ) from last_error
        self._load_target(page)
        status = self._read_status(page)
        if status.authenticated:
            return status
        if page.locator("#dologin").count():
            page.locator("#dologin").click()
            page.wait_for_timeout(2_000)
        if platform == "wechat":
            self._switch_platform(page, "wechat")
        elif username and password:
            frame = self._wait_password_login_frame(page)
            if frame is not None:
                frame.locator("#u").fill(
                    str(username),
                    timeout=10_000,
                )
                frame.locator("#p").fill(
                    str(password),
                    timeout=10_000,
                )
                logger.info(
                    "Visible QQ password form opened with credentials prefilled"
                )
                if submit:
                    frame.wait_for_function(
                        """
                        () => {
                          const button = document.querySelector(
                            '#login_button'
                          );
                          return button && !button.disabled;
                        }
                        """,
                        timeout=15_000,
                    )
                    clicked = frame.evaluate(
                        """
                        () => {
                          const button = document.querySelector(
                            '#login_button'
                          );
                          if (!button || button.disabled) return false;
                          button.click();
                          return true;
                        }
                        """
                    )
                    logger.info(
                        "Visible password form submitted: %s",
                        clicked,
                    )
                    page.wait_for_timeout(2_500)
        page.bring_to_front()
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

    def login_with_credentials(
        self,
        username: str,
        password: str,
    ) -> LoginStatus:
        logger.info("QQ credential login started")
        status = self._call(
            self._login_with_credentials,
            username,
            password,
            timeout=120,
        )
        logger.info(
            "QQ credential login finished: state=%s message=%s",
            status.state,
            status.message,
        )
        return status

    def request_login_verification(self) -> LoginStatus:
        status = self._call(
            self._request_login_verification,
            timeout=45,
        )
        logger.info("QQ verification request: %s", status.message)
        return status

    def submit_login_verification(self, code: str) -> LoginStatus:
        status = self._call(
            self._submit_login_verification,
            code,
            timeout=60,
        )
        logger.info("QQ verification submit: state=%s", status.state)
        return status

    def _login_with_credentials(
        self,
        username: str,
        password: str,
    ) -> LoginStatus:
        if not username or not password:
            raise ValueError("QQ 账号或密码未配置")

        page = self._ensure_page()
        self._load_target(page)
        status = self._read_status(page)
        if status.authenticated:
            return status

        self._open_login("qq")
        frame = self._wait_password_login_frame(page)
        if frame is None:
            return LoginStatus(
                state="login_failed",
                message="未找到 QQ 密码登录表单",
                checked_at=datetime.now().astimezone().isoformat(),
            )

        try:
            frame.wait_for_function(
                """
                () => {
                  const qlogin = document.querySelector('#qlogin');
                  const web = document.querySelector('#web_qr_login');
                  if (!web) return false;
                  const webRect = web.getBoundingClientRect();
                  const qRect = qlogin
                    ? qlogin.getBoundingClientRect()
                    : { width: 0 };
                  return webRect.width > 0 && qRect.width === 0;
                }
                """,
                timeout=10_000,
            )
            frame.locator("#login_button").wait_for(
                state="visible",
                timeout=15_000,
            )
            frame.wait_for_function(
                """
                () => {
                  const button = document.querySelector('#login_button');
                  if (!button || button.disabled) return false;
                  const style = getComputedStyle(button);
                  const rect = button.getBoundingClientRect();
                  return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && rect.width > 0 && rect.height > 0;
                }
                """,
                timeout=15_000,
            )
        except PlaywrightTimeoutError:
            return self._credential_failure(
                page,
                "QQ 密码登录表单未就绪，请打开完整窗口重试",
                "表单未就绪",
            )

        logger.info("Submitting QQ password login form")
        frame.locator("#u").fill(str(username), timeout=10_000)
        frame.locator("#p").fill(str(password), timeout=10_000)
        page.wait_for_timeout(500)

        if self._element_visible(frame, "#verifyArea"):
            return self._verification_status(
                page,
                "需要输入图片验证码",
                "请输入当前页面显示的图片验证码",
            )

        try:
            clicked = frame.evaluate(
                """
                () => {
                  const button = document.querySelector('#login_button');
                  if (!button || button.disabled) return false;
                  button.click();
                  return true;
                }
                """
            )
            if not clicked:
                raise RuntimeError("QQ 登录按钮不可用")
        except (PlaywrightError, RuntimeError) as exc:
            return self._credential_failure(
                page,
                f"无法提交 QQ 登录表单：{exc}",
                "提交失败",
            )

        last_message = ""
        for _ in range(30):
            page.wait_for_timeout(1_000)
            try:
                status = self._read_status(page)
                if status.authenticated:
                    return status
            except PlaywrightError:
                pass

            current = self._password_login_frame(page, raise_on_missing=False)
            if current is not None:
                if self._element_visible(current, "#verifyArea"):
                    return self._verification_status(
                        page,
                        "QQ 要求图片验证码",
                        "请输入当前页面显示的图片验证码",
                    )
                if self._mark_verification_input(current):
                    return self._verification_status(
                        page,
                        "QQ 要求输入手机或安全验证码",
                        "请查收手机验证码，或查看当前验证页面",
                    )
                error_text = self._login_error(current)
                if error_text:
                    last_message = error_text
                    if _credential_error_terminal(error_text):
                        return self._credential_failure(
                            page,
                            _credential_error_message(error_text),
                            error_text,
                        )
                challenge = self._login_challenge(current)
                if challenge:
                    return self._verification_status(
                        page,
                        challenge,
                        "请打开完整窗口完成腾讯安全验证",
                    )

        return self._credential_failure(
            page,
            last_message or "QQ 自动登录超时，可能需要验证码或人工核验",
            last_message or "超时",
        )

    def _password_login_frame(
        self,
        page: Any,
        *,
        raise_on_missing: bool = True,
    ) -> Any | None:
        for frame in page.frames:
            if "ptlogin2.qq.com" not in frame.url:
                continue
            try:
                if frame.locator("#u").count() and frame.locator("#p").count():
                    if not self._element_visible(frame, "#u"):
                        logged = frame.evaluate(
                            """
                            () => {
                              const switcher = document.querySelector(
                                '#switcher_plogin'
                              );
                              if (!switcher) return false;
                              switcher.click();
                              return true;
                            }
                            """
                        )
                        logger.info(
                            "QQ login switched to password mode: %s",
                            logged,
                        )
                        page_object = getattr(frame, "page", None)
                        for _ in range(40):
                            if self._password_view_active(frame):
                                break
                            if page_object is not None:
                                page_object.wait_for_timeout(200)
                    if self._password_view_active(frame):
                        return frame
            except PlaywrightError:
                logger.exception("Failed to inspect QQ password login frame")
                continue
        if not raise_on_missing:
            return None
        return None

    def _wait_password_login_frame(
        self,
        page: Any,
        *,
        timeout_seconds: int = 15,
    ) -> Any | None:
        for _ in range(timeout_seconds * 2):
            frame = self._password_login_frame(
                page,
                raise_on_missing=False,
            )
            if frame is not None:
                return frame
            page.wait_for_timeout(500)
        return None

    def _request_login_verification(self) -> LoginStatus:
        page = self._ensure_page()
        frame = self._wait_verification_frame(page)
        if frame is None:
            return LoginStatus(
                state="verification_required",
                message="当前页面未找到验证码发送按钮，请查看截图后手动处理",
                checked_at=datetime.now().astimezone().isoformat(),
                verification_required=True,
                verification_hint="验证码页面可能已过期",
            )
        sent = frame.evaluate(
            """
            () => {
              const visible = (element) => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              const pattern = /发送|获取|重新发送/;
              const target = [...document.querySelectorAll('a,button')]
                .find((element) => {
                  const text = (element.textContent || '').trim();
                  return visible(element)
                    && pattern.test(text)
                    && /验证码|短信/.test(text);
                });
              if (!target) return '';
              target.click();
              return (target.textContent || '').trim();
            }
            """
        )
        page.wait_for_timeout(800)
        return LoginStatus(
            state="verification_required",
            message=(
                "已触发验证码发送，请查收手机短信"
                if sent
                else "未找到发送按钮，请查收已有验证码或打开完整窗口"
            ),
            checked_at=datetime.now().astimezone().isoformat(),
            verification_required=True,
            verification_hint="输入收到的验证码后点击提交",
            raw={"send_button": bool(sent)},
        )

    def _submit_login_verification(self, code: str) -> LoginStatus:
        cleaned = str(code).strip()
        if not cleaned:
            raise ValueError("请输入验证码")
        page = self._ensure_page()
        frame = self._wait_verification_frame(page)
        if frame is None:
            return LoginStatus(
                state="verification_required",
                message="验证码输入框已失效，请重新执行密码登录",
                checked_at=datetime.now().astimezone().isoformat(),
                verification_required=True,
            )

        selector = '[data-pvp-verification-input="1"]'
        frame.locator(selector).fill(cleaned, timeout=10_000)
        page.wait_for_timeout(300)
        frame.evaluate(
            """
            () => {
              const visible = (element) => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== 'none'
                  && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              const imageVerify = document.querySelector('#verifyArea');
              if (imageVerify && visible(imageVerify)) {
                const button = document.querySelector('#login_button');
                if (button) button.click();
                return true;
              }
              const target = [...document.querySelectorAll(
                'button,a,input[type="submit"]'
              )].find((element) => {
                const text = (
                  element.textContent || element.value || ''
                ).trim();
                return visible(element)
                  && /^(验证|确定|下一步|提交|登录|继续|完成)$/.test(text);
              });
              if (target) {
                target.click();
                return true;
              }
              const input = document.querySelector(
                '[data-pvp-verification-input="1"]'
              );
              input.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'Enter', code: 'Enter', bubbles: true
              }));
              return false;
            }
            """
        )

        last_message = ""
        for _ in range(20):
            page.wait_for_timeout(1_000)
            try:
                status = self._read_status(page)
                if status.authenticated:
                    return status
            except PlaywrightError:
                pass
            current = self._wait_verification_frame(
                page,
                timeout_seconds=1,
            )
            if current is not None:
                error = self._verification_error(current)
                if error:
                    last_message = error

        return LoginStatus(
            state="verification_required",
            message=last_message or "验证码验证尚未完成，请检查后重试",
            checked_at=datetime.now().astimezone().isoformat(),
            verification_required=True,
            verification_hint="验证码错误或已过期时可重新发送",
        )

    def _wait_verification_frame(
        self,
        page: Any,
        *,
        timeout_seconds: int = 10,
    ) -> Any | None:
        for _ in range(timeout_seconds * 2):
            for frame in page.frames:
                if self._mark_verification_input(frame):
                    return frame
            page.wait_for_timeout(500)
        return None

    @staticmethod
    def _mark_verification_input(frame: Any) -> bool:
        try:
            return bool(
                frame.evaluate(
                    """
                    () => {
                      const previous = document.querySelector(
                        '[data-pvp-verification-input="1"]'
                      );
                      if (previous) {
                        const rect = previous.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) return true;
                        previous.removeAttribute(
                          'data-pvp-verification-input'
                        );
                      }
                      const visible = (element) => {
                        const style = getComputedStyle(element);
                        const rect = element.getBoundingClientRect();
                        return style.display !== 'none'
                          && style.visibility !== 'hidden'
                          && rect.width > 0 && rect.height > 0;
                      };
                      const inputs = [...document.querySelectorAll('input')]
                        .filter((element) => {
                          if (!visible(element)) return false;
                          if (['u', 'p'].includes(element.id)) return false;
                          if (!['text', 'tel', 'number'].includes(element.type)) {
                            return false;
                          }
                          const marker = [
                            element.id,
                            element.name,
                            element.placeholder
                          ].join(' ');
                          return element.id === 'verifycode'
                            || /验证码|短信|verify|sms|otp|auth|code/i.test(
                              marker
                            );
                        });
                      if (!inputs.length) return false;
                      inputs[0].setAttribute(
                        'data-pvp-verification-input',
                        '1'
                      );
                      return true;
                    }
                    """
                )
            )
        except PlaywrightError:
            return False

    @staticmethod
    def _verification_error(frame: Any) -> str:
        try:
            return frame.evaluate(
                """
                () => {
                  const error = document.querySelector('#err_m');
                  if (error) {
                    const text = error.textContent.trim();
                    if (text) return text;
                  }
                  const body = document.body.innerText || '';
                  const match = body.match(
                    /(验证码[^。\\n]{0,40}(?:错误|失效|不正确|过期)[^。\\n]{0,20})/
                  );
                  return match ? match[1] : '';
                }
                """
            )
        except PlaywrightError:
            return ""

    @staticmethod
    def _element_visible(frame: Any, selector: str) -> bool:
        try:
            return bool(
                frame.evaluate(
                    """
                    (selector) => {
                      const element = document.querySelector(selector);
                      if (!element) return false;
                      const style = getComputedStyle(element);
                      const rect = element.getBoundingClientRect();
                      return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0 && rect.height > 0;
                    }
                    """,
                    selector,
                )
            )
        except PlaywrightError:
            return False

    @staticmethod
    def _password_view_active(frame: Any) -> bool:
        try:
            return bool(
                frame.evaluate(
                    """
                    () => {
                      const u = document.querySelector('#u');
                      const web = document.querySelector('#web_qr_login');
                      const qlogin = document.querySelector('#qlogin');
                      const uRect = u ? u.getBoundingClientRect() : null;
                      const webRect = web ? web.getBoundingClientRect() : null;
                      const qRect = qlogin
                        ? qlogin.getBoundingClientRect()
                        : null;
                      return Boolean(
                        uRect && uRect.width > 0
                        && webRect && webRect.width > 0
                        && (!qRect || qRect.width === 0)
                      );
                    }
                    """
                )
            )
        except PlaywrightError:
            return False

    @staticmethod
    def _login_error(frame: Any) -> str:
        try:
            return frame.evaluate(
                """
                () => {
                  const error = document.querySelector('#err_m');
                  const wrapper = document.querySelector('#error_tips');
                  if (!error || !wrapper) return '';
                  const style = getComputedStyle(wrapper);
                  const rect = wrapper.getBoundingClientRect();
                  if (style.display === 'none' || rect.width === 0) return '';
                  return error.textContent.trim();
                }
                """
            )
        except PlaywrightError:
            return ""

    @staticmethod
    def _login_challenge(frame: Any) -> str:
        try:
            text = frame.locator("body").inner_text(timeout=2_000)
        except PlaywrightError:
            return ""
        if "登录环境异常" in text:
            return "QQ 登录环境异常，需要人工扫码或安全验证"
        if "设备锁" in text and "扫码" in text:
            return "QQ 设备锁要求扫码验证"
        if "短信验证" in text or "安全验证" in text:
            return "QQ 要求安全验证，自动登录已停止"
        return ""

    def _credential_failure(
        self,
        page: Any,
        message: str,
        kind: str,
    ) -> LoginStatus:
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            path = self.artifact_root / f"{stamp}-login-failed.png"
            page.screenshot(path=str(path), full_page=False)
        except PlaywrightError:
            pass
        return LoginStatus(
            state="login_failed",
            message=message,
            checked_at=datetime.now().astimezone().isoformat(),
            raw={"failure_kind": kind},
        )

    def _verification_status(
        self,
        page: Any,
        message: str,
        hint: str,
    ) -> LoginStatus:
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            path = self.artifact_root / f"{stamp}-verification.png"
            page.screenshot(path=str(path), full_page=False)
        except PlaywrightError:
            pass
        return LoginStatus(
            state="verification_required",
            message=message,
            checked_at=datetime.now().astimezone().isoformat(),
            verification_required=True,
            verification_hint=hint,
            raw={"verification_required": True},
        )

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
        time.sleep(0.4)

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


def _profile_lock_stale_reason(
    target: str,
    *,
    hostname: str,
    pid_exists: Callable[[int], bool],
    pid_is_browser: Callable[[int], bool],
) -> str:
    match = _PROFILE_LOCK_TARGET.match(target)
    if not match:
        return ""

    lock_host = match.group("host")
    if lock_host != hostname:
        return "profile belongs to a previous container"

    pid = int(match.group("pid"))
    if not pid_exists(pid):
        return "browser process exited"
    if not pid_is_browser(pid):
        return "PID was reused by another process"
    return ""


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_is_browser_for_profile(pid: int, profile_root: Path) -> bool:
    command_line = Path(f"/proc/{pid}/cmdline")
    try:
        raw = command_line.read_bytes()
    except FileNotFoundError:
        return True
    except OSError:
        return True

    arguments = raw.replace(b"\0", b" ").decode("utf-8", errors="ignore")
    return "chrom" in arguments.lower() and str(profile_root) in arguments


def _mentions_already(message: str) -> bool:
    return any(
        token in message
        for token in ("已经兑换", "已兑换", "兑换过了", "今日已", "明天再来")
    )


def _credential_error_terminal(message: str) -> bool:
    return any(
        token in message
        for token in (
            "密码错误",
            "账号不存在",
            "账号或密码",
            "频繁",
            "冻结",
            "验证码",
            "安全验证",
            "环境异常",
        )
    )


def _credential_error_message(message: str) -> str:
    if "密码错误" in message or "账号或密码" in message:
        return "QQ 账号或密码不正确"
    if "频繁" in message:
        return "QQ 登录过于频繁，请稍后再试"
    if "验证" in message:
        return f"QQ 要求安全验证：{message}"
    return message

