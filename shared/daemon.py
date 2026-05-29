"""
shared/daemon.py
간격 기반으로 반복 실행되는 데몬의 공통 베이스 (Template Method).

사용:
    class MyDaemon(Daemon):
        name = "my_daemon"
        interval_sec = 60
        def tick(self):
            ...

    MyDaemon().run()
"""

from __future__ import annotations

import logging
import signal
import time
from abc import ABC, abstractmethod

from shared.logging import setup_logger


class Daemon(ABC):
    name: str = "daemon"
    interval_sec: int = 60
    run_on_start: bool = True

    def __init__(self) -> None:
        self.logger: logging.Logger = setup_logger(self.name)
        self._stopped = False
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._handle_stop)
            except ValueError:
                pass  # 메인 스레드가 아닐 때

    def _handle_stop(self, *_args) -> None:
        self.logger.info(f"종료 신호 수신 → {self.name} 중지")
        self._stopped = True

    def _sleep(self, seconds: int) -> None:
        end = time.time() + seconds
        while not self._stopped and time.time() < end:
            time.sleep(min(1, end - time.time()))

    @abstractmethod
    def tick(self) -> None:
        """1회 실행할 작업. 구현체가 정의."""

    def on_fatal(self, exc: Exception) -> None:
        """tick()이 통째로 실패했을 때 호출되는 후처리 훅 (알림 등). 기본 무동작."""

    def run(self) -> None:
        self.logger.info(f"🚀 {self.name} 시작 | 간격={self.interval_sec}s")
        if self.run_on_start:
            self._safe_tick()
        while not self._stopped:
            self._sleep(self.interval_sec)
            if self._stopped:
                break
            self._safe_tick()
        self.logger.info(f"{self.name} 종료")

    def _safe_tick(self) -> None:
        try:
            self.tick()
        except Exception as e:
            self.logger.critical(f"치명적 오류: {e}", exc_info=True)
            try:
                self.on_fatal(e)
            except Exception:
                pass
