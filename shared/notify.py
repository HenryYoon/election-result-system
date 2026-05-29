"""
shared/notify.py
Slack Incoming Webhook 알림.

- SLACK_WEBHOOK_URL 환경변수가 비어 있으면 모든 호출이 무동작(no-op).
- 알림 전송 실패가 데몬 본 작업을 절대 중단시키지 않도록 예외를 삼킨다.

사용:
    from shared.notify import make_notifier
    slack = make_notifier(prefix="[crawler_d2] ")
    slack.send("13시 수집 완료")
"""

from __future__ import annotations

import logging

import requests

from shared.config import settings


logger = logging.getLogger(__name__)


class SlackNotifier:
    def __init__(self, webhook_url: str, prefix: str = "") -> None:
        self.webhook_url = webhook_url
        self.prefix = prefix

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send(self, text: str) -> None:
        if not self.webhook_url:
            return
        try:
            requests.post(
                self.webhook_url,
                json={"text": f"{self.prefix}{text}"},
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"슬랙 알림 실패: {e}")


def make_notifier(prefix: str = "") -> SlackNotifier:
    return SlackNotifier(settings.slack_webhook_url, prefix)
