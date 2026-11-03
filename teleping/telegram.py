"""Explicit Telegram delivery, without logging or state-management side effects."""

# Response type failures are external API failures, not caller TypeErrors.
# ruff: noqa: TRY004

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Iterator

import httpx


def _chunks(text: str) -> Iterator[str]:
    """Keep Unicode characters intact within Telegram's UTF-16 size limit."""
    start = units = 0
    for index, character in enumerate(text):
        size = 2 if ord(character) > 0xFFFF else 1
        if units + size > 3500:
            yield text[start:index]
            start, units = index, 0
        units += size
    if start < len(text):
        yield text[start:]


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: int,
        *,
        client: httpx.Client | None = None,
        sleep: Callable = time.sleep,
    ):
        self._base = f"https://api.telegram.org/bot{token}/"
        self.chat_id = chat_id
        self.client = client or httpx.Client(timeout=20, follow_redirects=False, trust_env=False)
        self.sleep = sleep
        self._last_send_at: float | None = None

    def _pace_send(self) -> None:
        if self._last_send_at is not None:
            delay = 3.2 - (time.monotonic() - self._last_send_at)
            if delay > 0:
                self.sleep(delay)
        self._last_send_at = time.monotonic()

    def call(self, method: str, payload: dict | None = None) -> object:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", method):
            raise RuntimeError("telegram_api_failed") from None
        total_wait = 0.0
        for attempt in range(3):
            if method == "sendMessage":
                self._pace_send()
            try:
                response = self.client.post(self._base + method, json=payload or {})
            except (httpx.HTTPError, httpx.InvalidURL):
                raise RuntimeError("telegram_unavailable") from None
            try:
                data = response.json()
            except ValueError:
                raise RuntimeError("telegram_api_failed") from None
            if not isinstance(data, dict):
                raise RuntimeError("telegram_api_failed") from None
            if response.status_code == 429 or data.get("error_code") == 429:
                parameters = data.get("parameters")
                delay = parameters.get("retry_after") if isinstance(parameters, dict) else None
                if (
                    attempt == 2
                    or isinstance(delay, bool)
                    or not isinstance(delay, (int, float))
                    or not math.isfinite(delay)
                    or delay < 0
                    or total_wait + delay > 60
                ):
                    raise RuntimeError("telegram_rate_limited") from None
                total_wait += delay
                self.sleep(delay)
                continue
            if response.status_code >= 400 or data.get("ok") is not True or "result" not in data:
                raise RuntimeError("telegram_api_failed") from None
            return data["result"]
        raise RuntimeError("telegram_rate_limited") from None

    def get_updates(self, offset: int) -> list:
        value = self.call(
            "getUpdates", {"offset": offset, "timeout": 0, "limit": 100, "allowed_updates": ["message"]}
        )
        if not isinstance(value, list):
            raise RuntimeError("telegram_api_failed") from None
        return value

    def administrator_ids(self) -> set[int]:
        value = self.call("getChatAdministrators", {"chat_id": self.chat_id})
        if not isinstance(value, list):
            raise RuntimeError("telegram_api_failed") from None
        result: set[int] = set()
        for member in value:
            if not isinstance(member, dict) or not isinstance(member.get("status"), str):
                raise RuntimeError("telegram_api_failed") from None
            user = member.get("user")
            if not isinstance(user, dict):
                raise RuntimeError("telegram_api_failed") from None
            user_id = user.get("id")
            is_bot = user.get("is_bot")
            if type(user_id) is not int or user_id <= 0 or type(is_bot) is not bool:
                raise RuntimeError("telegram_api_failed") from None
            if member["status"] in {"creator", "administrator"} and not is_bot:
                result.add(user_id)
        return result

    def webhook_url(self) -> str:
        value = self.call("getWebhookInfo")
        if not isinstance(value, dict) or not isinstance(value.get("url"), str):
            raise RuntimeError("telegram_api_failed") from None
        return value["url"]

    def register_menu(self, commands: list[dict]) -> None:
        self.call("setMyCommands", {"commands": commands, "scope": {"type": "default"}})
        if self.chat_id != 0:
            self.call(
                "setMyCommands",
                {"commands": commands, "scope": {"type": "chat", "chat_id": self.chat_id}},
            )
        self.call("setChatMenuButton", {"menu_button": {"type": "commands"}})

    def bot_username(self) -> str:
        value = self.call("getMe")
        if not isinstance(value, dict) or not isinstance(value.get("username"), str):
            raise RuntimeError("telegram_api_failed") from None
        return value["username"]

    def send(self, text: str) -> None:
        for chunk in _chunks(text):
            self.call(
                "sendMessage",
                {"chat_id": self.chat_id, "text": chunk, "link_preview_options": {"is_disabled": True}},
            )
