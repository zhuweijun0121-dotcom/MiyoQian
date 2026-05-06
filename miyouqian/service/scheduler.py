# -*- coding: utf-8 -*-
"""每日随机波动调度器。"""

from __future__ import annotations

import random
import threading
from datetime import datetime, timedelta
from typing import Any, Callable


LogFn = Callable[[str], None]
RunFn = Callable[[], list[str]]


class DailyScheduler:
    def __init__(self, config: dict[str, Any], run_fn: RunFn, log_fn: LogFn) -> None:
        self.config = config
        self.run_fn = run_fn
        self.log = log_fn
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False
        self._next_run: datetime | None = None
        self._last_run: datetime | None = None
        self._last_error = ""
        self._schedule_signature = self._make_schedule_signature(self._schedule_config())
        self._log_next_run_on_recompute = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="miyouqian-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def reload(self, config: dict[str, Any]) -> None:
        old_signature = self._schedule_signature
        self.config = config
        new_signature = self._make_schedule_signature(self._schedule_config())
        with self._lock:
            self._schedule_signature = new_signature
            if new_signature != old_signature:
                self._next_run = None
                self._log_next_run_on_recompute = new_signature[1] != old_signature[1]
        self._wake.set()

    def run_now(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
        threading.Thread(target=self._run_once, name="miyouqian-manual-run", daemon=True).start()
        return True

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": bool(self._schedule_config().get("enable", True)),
                "running": self._running,
                "next_run": self._next_run.isoformat(timespec="seconds") if self._next_run else "",
                "last_run": self._last_run.isoformat(timespec="seconds") if self._last_run else "",
                "last_error": self._last_error,
                "schedule": self._schedule_config(),
            }

    def _loop(self) -> None:
        if self._schedule_config().get("run_on_start", False):
            self.run_now()
        while not self._stop.is_set():
            schedule = self._schedule_config()
            if not schedule.get("enable", True):
                with self._lock:
                    self._next_run = None
                self._wake.wait(timeout=30)
                self._wake.clear()
                continue
            with self._lock:
                if self._next_run is None or self._next_run <= datetime.now() - timedelta(minutes=5):
                    log_change = self._log_next_run_on_recompute
                    self._log_next_run_on_recompute = False
                    self._next_run = self._compute_next_base_run(schedule, log_change=log_change)
                next_run = self._next_run
            wait_seconds = max((next_run - datetime.now()).total_seconds(), 0)
            if self._wake.wait(timeout=min(wait_seconds, 60)):
                self._wake.clear()
                continue
            if datetime.now() >= next_run:
                with self._lock:
                    if self._running:
                        self._next_run = self._compute_next_base_run(schedule, tomorrow=True, log_change=False)
                        continue
                    due_run = self._next_run
                if not self._wait_jitter(schedule, due_run):
                    continue
                with self._lock:
                    if self._running:
                        self._next_run = self._compute_next_base_run(schedule, tomorrow=True, log_change=False)
                        continue
                    if self._last_run and due_run and self._last_run >= due_run:
                        self._next_run = self._compute_next_base_run(schedule, tomorrow=True, log_change=False)
                        continue
                    self._running = True
                self._run_once()
                with self._lock:
                    self._next_run = self._compute_next_base_run(self._schedule_config(), tomorrow=True, log_change=False)

    def _run_once(self) -> None:
        self.log("开始执行签到任务")
        try:
            for line in self.run_fn():
                self.log(line)
            with self._lock:
                self._last_run = datetime.now()
                self._last_error = ""
            self.log("签到任务执行完成")
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            self.log(f"签到任务失败: {exc}")
        finally:
            with self._lock:
                self._running = False

    def _schedule_config(self) -> dict[str, Any]:
        schedule = self.config.get("schedule", {})
        return schedule if isinstance(schedule, dict) else {}

    def _compute_next_base_run(
        self,
        schedule: dict[str, Any],
        tomorrow: bool = False,
        log_change: bool = False,
    ) -> datetime:
        hour, minute = parse_time(str(schedule.get("time", "09:00")))
        base_day = datetime.now().date()
        if tomorrow:
            base_day = base_day + timedelta(days=1)
        target = datetime.combine(base_day, datetime.min.time()).replace(hour=hour, minute=minute)
        if target <= datetime.now():
            return self._compute_next_base_run(schedule, tomorrow=True, log_change=log_change)
        if log_change:
            self.log(f"下次自动执行时间: {target.strftime('%Y-%m-%d %H:%M:%S')}")
        return target

    def _wait_jitter(self, schedule: dict[str, Any], due_run: datetime | None) -> bool:
        jitter = max(int(schedule.get("jitter_minutes", 45) or 0), 0)
        if not jitter:
            return True
        delay_seconds = random.randint(0, jitter * 60)
        if delay_seconds <= 0:
            return True
        if due_run:
            delay_min_str = f"{delay_seconds // 60}分{delay_seconds % 60}秒" if delay_seconds >= 60 else f"{delay_seconds}秒"
            self.log(f"已到自动执行时间 {due_run.strftime('%Y-%m-%d %H:%M:%S')}，随机延后 {delay_min_str} 后执行")
        with self._lock:
            self._next_run = datetime.now() + timedelta(seconds=delay_seconds)
        if self._stop.wait(timeout=delay_seconds):
            return False
        return True

    def _make_schedule_signature(self, schedule: dict[str, Any]) -> tuple[Any, ...]:
        return (
            bool(schedule.get("enable", True)),
            str(schedule.get("time", "09:00")),
        )


def parse_time(value: str) -> tuple[int, int]:
    parts = value.strip().split(":", 1)
    if len(parts) != 2:
        raise ValueError("schedule.time 必须是 HH:MM 格式")
    hour = int(parts[0])
    minute = int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("schedule.time 超出范围")
    return hour, minute
