"""
shared/logging.py
모든 데몬에서 동일한 포맷으로 콘솔 + 파일 로깅 초기화.
"""

import logging
from pathlib import Path

from shared.config import settings


_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _make_handlers(log_path: Path) -> list[logging.Handler]:
    formatter = logging.Formatter(_FORMAT, _DATEFMT)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    return [file_handler, stream_handler]


def setup_logger(name: str, log_file: str | None = None) -> logging.Logger:
    """name 로거 + root 로거를 같은 핸들러로 구성.

    데몬 코드 외 모듈이 logging.getLogger(__name__)으로
    얻는 로거도 root → 동일 출력으로 전파되도록 한다.
    """
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_dir / (log_file or f"{name}.log")

    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        for h in _make_handlers(log_path):
            root.addHandler(h)

    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.propagate = True  # root로 흘려보냄
    return logger
