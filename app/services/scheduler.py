from __future__ import annotations

import threading
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.database import Database
from app.security import SecretCipher
from app.services.runner import ExchangeRunner
from app.services.settings import get_settings


class ExchangeScheduler:
    def __init__(
        self,
        db: Database,
        cipher: SecretCipher,
        runner: ExchangeRunner,
        *,
        enabled: bool = True,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.runner = runner
        self.enabled = enabled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop,
            name="pvp-exchange-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(20):
            try:
                self.tick()
            except Exception as exc:
                self.db.set_runtime(
                    "scheduler_last_error",
                    f"{datetime.now().isoformat()} {exc}",
                )

    def tick(self) -> dict[str, Any]:
        settings = get_settings(self.db, self.cipher)
        timezone = ZoneInfo(settings["timezone"])
        now = datetime.now(timezone)
        self.db.set_runtime("scheduler_last_tick", now.isoformat())

        result: dict[str, Any] = {"executed": False}
        if self._due_today(settings, now):
            exchange = self.runner.execute(trigger="schedule")
            result = {
                "executed": exchange.status != "skipped",
                "status": exchange.status,
                "message": exchange.message,
            }

        monitor_due = self._monitor_due(now)
        if settings["auto_exchange"] and monitor_due:
            status = self.runner.check_login(notify=True)
            self.db.set_runtime("last_login_monitor", now.isoformat())
            result["login_state"] = status.state
        return result

    def _due_today(self, settings: dict[str, Any], now: datetime) -> bool:
        if not settings["auto_exchange"]:
            return False
        hour, minute = (
            int(part) for part in str(settings["scheduled_time"]).split(":")
        )
        if now.time() < time(hour=hour, minute=minute):
            return False
        existing = self.db.query_one(
            "SELECT id FROM exchange_logs WHERE run_date = ? LIMIT 1",
            (now.date().isoformat(),),
        )
        return existing is None

    def _monitor_due(self, now: datetime) -> bool:
        last_text = self.db.get_runtime("last_login_monitor")
        if not last_text:
            return True
        try:
            last = datetime.fromisoformat(last_text)
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=now.tzinfo)
        return now - last >= timedelta(minutes=10)


def next_run_time(
    db: Database,
    settings: dict[str, Any],
    now: datetime,
) -> datetime | None:
    if not settings["auto_exchange"]:
        return None
    hour, minute = (
        int(part) for part in str(settings["scheduled_time"]).split(":")
    )
    candidate = now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    existing = db.query_one(
        "SELECT id FROM exchange_logs WHERE run_date = ? LIMIT 1",
        (candidate.date().isoformat(),),
    )
    if candidate <= now or existing:
        candidate += timedelta(days=1)
    return candidate

