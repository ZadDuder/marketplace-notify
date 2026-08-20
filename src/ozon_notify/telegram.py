from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Any

import aiohttp

from .config import AccountConfig, MVideoConfig, Settings
from .utils import truncate

logger = logging.getLogger(__name__)


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, status: int, response: dict[str, Any]) -> None:
        self.method = method
        self.status = status
        self.error_code = response.get("error_code")
        self.description = str(response.get("description") or "unknown error")
        parameters = response.get("parameters")
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        self.retry_after = int(retry_after) if isinstance(retry_after, (int, float)) else None
        super().__init__(
            f"Telegram {method} failed: status={status}, "
            f"error_code={self.error_code}, description={self.description}"
        )


class TelegramClient:
    def __init__(
        self,
        token: str,
        default_chat_id: str | None = None,
        proxy_url: str | None = None,
        relay_url: str | None = None,
        relay_secret: str | None = None,
        min_send_interval: float = 1.05,
    ) -> None:
        self.token = token
        self.default_chat_id = default_chat_id
        self.proxy_url = proxy_url
        self.relay_url = relay_url
        self.relay_secret = relay_secret
        self.min_send_interval = min_send_interval
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._session: aiohttp.ClientSession | None = None
        self._missing_chat_warnings: set[str] = set()
        self._send_lock = asyncio.Lock()
        self._last_send_at = 0.0

    async def start(self) -> None:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30),
        )

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        if not self._session:
            raise RuntimeError("Telegram client is not started")
        return self._session

    async def api(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self.session.post(
            f"{self.base_url}/{method}",
            json=payload or {},
            proxy=self.proxy_url,
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400 or not data.get("ok", False):
                raise TelegramAPIError(method, resp.status, data)
            return data

    async def _respect_send_interval(self) -> None:
        now = time.monotonic()
        delay = self._last_send_at + self.min_send_interval - now
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_send_at = time.monotonic()

    async def send_message_direct(
        self,
        chat_id: str,
        text: str,
        message_thread_id: int | None = None,
    ) -> bool:
        payload = {
            "chat_id": chat_id,
            "text": truncate(text, 3900),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        async with self._send_lock:
            for attempt in range(3):
                await self._respect_send_interval()
                try:
                    await self.api("sendMessage", payload)
                    return True
                except TelegramAPIError as exc:
                    if exc.status == 429 and attempt < 2:
                        delay = max(exc.retry_after or 1, 1) + 1
                        logger.warning(
                            "Telegram rate limit for chat_id=%s; retrying in %ss",
                            chat_id,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.error(
                        "Telegram sendMessage failed for chat_id=%s status=%s error_code=%s",
                        chat_id,
                        exc.status,
                        exc.error_code,
                    )
                    return False
                except Exception as exc:
                    if attempt < 2:
                        await asyncio.sleep(2**attempt)
                        continue
                    logger.error(
                        "Telegram sendMessage failed for chat_id=%s error_type=%s",
                        chat_id,
                        type(exc).__name__,
                    )
                    return False
        return False

    async def send_message_via_relay(
        self,
        chat_id: str,
        text: str,
        message_thread_id: int | None = None,
    ) -> bool:
        if not self.relay_url or not self.relay_secret:
            raise RuntimeError("Telegram relay is not configured")
        for attempt in range(3):
            try:
                async with self.session.post(
                    self.relay_url,
                    json={
                        "chat_id": chat_id,
                        "text": truncate(text, 3900),
                        "message_thread_id": message_thread_id,
                    },
                    headers={"X-Ozon-Notify-Relay-Secret": self.relay_secret},
                ) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status >= 400 or not data.get("ok", False):
                        raise RuntimeError(f"relay returned HTTP {resp.status}")
                return True
            except Exception as exc:
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                logger.error(
                    "Telegram relay failed for chat_id=%s error_type=%s",
                    chat_id,
                    type(exc).__name__,
                )
                return False
        return False

    async def send_message(
        self,
        chat_id: str,
        text: str,
        message_thread_id: int | None = None,
    ) -> bool:
        if self.relay_url:
            return await self.send_message_via_relay(chat_id, text, message_thread_id)
        return await self.send_message_direct(chat_id, text, message_thread_id)

    async def send_to_account(
        self,
        account: AccountConfig | MVideoConfig,
        text: str,
        topic: str | None = None,
    ) -> bool:
        chat_id = account.effective_chat_id or self.default_chat_id
        if not chat_id:
            if account.slug not in self._missing_chat_warnings:
                logger.warning("No Telegram chat_id configured for account %s", account.slug)
                self._missing_chat_warnings.add(account.slug)
            return False
        message_thread_id = account.topic_id(topic)
        if topic and account.effective_chat_id and message_thread_id is None:
            warning_key = f"{account.slug}:{topic}"
            if warning_key not in self._missing_chat_warnings:
                logger.warning(
                    "No Telegram topic configured for account=%s topic=%s",
                    account.slug,
                    topic,
                )
                self._missing_chat_warnings.add(warning_key)
            return False
        sent = await self.send_message(chat_id, text, message_thread_id)
        if sent:
            logger.info(
                "Telegram notification sent account=%s chat_id=%s topic=%s thread_id=%s",
                account.slug,
                chat_id,
                topic or "general",
                message_thread_id,
            )
        return sent

    async def get_updates(self, offset: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "timeout": 0,
            "allowed_updates": ["message", "channel_post", "my_chat_member"],
        }
        if offset is not None:
            payload["offset"] = offset
        return await self.api("getUpdates", payload)

    async def find_known_chats(self) -> list[dict[str, Any]]:
        data = await self.get_updates()
        chats: dict[str, dict[str, Any]] = {}
        for update in data.get("result", []):
            candidates = [
                update.get("message", {}).get("chat"),
                update.get("channel_post", {}).get("chat"),
                update.get("my_chat_member", {}).get("chat"),
            ]
            for chat in candidates:
                if isinstance(chat, dict) and chat.get("id") is not None:
                    chats[str(chat["id"])] = chat
        return list(chats.values())


def make_telegram(settings: Settings) -> TelegramClient:
    return TelegramClient(
        settings.telegram_bot_token,
        settings.telegram_default_chat_id,
        settings.telegram_proxy_url,
        settings.telegram_relay_url,
        settings.telegram_relay_secret,
    )


def effective_news_chat_id(settings: Settings) -> str | None:
    if settings.telegram_news_bot_token:
        if not settings.telegram_news_chat_id:
            raise RuntimeError(
                "NEWS_TELEGRAM_CHAT_ID is required when NEWS_TELEGRAM_BOT_TOKEN is set"
            )
        return settings.telegram_news_chat_id
    return settings.telegram_news_chat_id or settings.telegram_default_chat_id


def make_news_telegram(settings: Settings) -> TelegramClient:
    return TelegramClient(
        settings.telegram_news_bot_token or settings.telegram_bot_token,
        effective_news_chat_id(settings),
        settings.telegram_proxy_url,
    )
