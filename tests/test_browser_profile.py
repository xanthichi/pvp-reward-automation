from __future__ import annotations

from app.services.browser import _profile_lock_stale_reason


def test_profile_lock_from_previous_container_is_stale() -> None:
    reason = _profile_lock_stale_reason(
        "21c4ab1486fb-12531",
        hostname="current-container",
        pid_exists=lambda _: True,
        pid_is_browser=lambda _: True,
    )

    assert reason == "profile belongs to a previous container"


def test_profile_lock_with_live_browser_is_kept() -> None:
    reason = _profile_lock_stale_reason(
        "current-container-12531",
        hostname="current-container",
        pid_exists=lambda _: True,
        pid_is_browser=lambda _: True,
    )

    assert reason == ""


def test_profile_lock_is_cleared_when_pid_was_reused() -> None:
    reason = _profile_lock_stale_reason(
        "current-container-12531",
        hostname="current-container",
        pid_exists=lambda _: True,
        pid_is_browser=lambda _: False,
    )

    assert reason == "PID was reused by another process"
