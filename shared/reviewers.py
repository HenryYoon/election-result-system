"""
shared/reviewers.py
검수자 명부(파일 기반)와 라운드로빈 배정.

파일:
  data/reviewers.txt          한 줄에 검수자 ID 하나 (중복은 무시)
  data/state/reviewer_counter 라운드로빈 카운터 (재시작 후에도 순환 유지)
"""

from __future__ import annotations

import threading
from pathlib import Path

from shared.config import settings


REVIEWERS_FILE = settings.data_dir / "reviewers.txt"
COUNTER_FILE = settings.data_dir / "state" / "reviewer_counter"


def list_reviewers() -> list[str]:
    if not REVIEWERS_FILE.exists():
        return []
    seen: list[str] = []
    for line in REVIEWERS_FILE.read_text(encoding="utf-8").splitlines():
        rid = line.strip()
        if rid and rid not in seen:
            seen.append(rid)
    return seen


def register_reviewer(reviewer_id: str) -> None:
    """검수자 ID를 명부에 추가 (중복 무시, 파일 없으면 생성)."""
    reviewer_id = reviewer_id.strip()
    if not reviewer_id:
        return
    REVIEWERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = set(list_reviewers())
    if reviewer_id in existing:
        return
    with open(REVIEWERS_FILE, "a", encoding="utf-8") as f:
        f.write(reviewer_id + "\n")


class ReviewerAssigner:
    """파일 명부 기반 라운드로빈 배정. 카운터 영속화."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _load_counter(self) -> int:
        if not COUNTER_FILE.exists():
            return 0
        try:
            return int(COUNTER_FILE.read_text().strip())
        except Exception:
            return 0

    def _save_counter(self, value: int) -> None:
        COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
        COUNTER_FILE.write_text(str(value))

    def assign(self) -> str:
        reviewers = list_reviewers()
        if not reviewers:
            return ""
        with self._lock:
            counter = self._load_counter()
            assignee = reviewers[counter % len(reviewers)]
            self._save_counter(counter + 1)
            return assignee
