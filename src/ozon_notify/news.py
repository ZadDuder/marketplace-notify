from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re

import aiohttp

from .config import Settings
from .database import Database
from .formatters import format_news
from .telegram import TelegramClient, effective_news_chat_id
from .utils import strip_tags

logger = logging.getLogger(__name__)

NEWS_URL = "https://t.me/s/OzonSellerAPI"
IMPORTANT_NEWS_WORDS = {
    "штраф",
    "удержан",
    "отключ",
    "устар",
    "обязател",
    "вступит в силу",
    "изменение правил",
    "критичес",
    "тариф",
    "комисс",
    "маркиров",
    "тн вэд",
    "в реальном времени",
}
IMPORTANT_NEWS_SUBJECTS = {
    "fbs",
    "rfbs",
    "возврат",
    "поставка",
    "курьер",
    "склад",
    "отправлен",
    "прием",
    "приём",
}
IMPORTANT_NEWS_CHANGES = {
    "измен",
    "перейд",
    "переход",
    "новые правила",
    "новый порядок",
    "срок",
}


@dataclass(frozen=True)
class NewsDestination:
    key: str
    chat_id: str
    message_thread_id: int | None = None


def news_destinations(settings: Settings) -> list[NewsDestination]:
    if settings.telegram_news_bot_token or settings.telegram_news_chat_id:
        chat_id = effective_news_chat_id(settings)
        if not chat_id:
            return []
        return [NewsDestination(f"dedicated:{chat_id}", chat_id)]

    destinations: list[NewsDestination] = []
    seen: set[tuple[str, int | None]] = set()
    for account in settings.accounts:
        chat_id = account.effective_chat_id or settings.telegram_default_chat_id
        if not chat_id:
            continue
        thread_id = account.topic_id("news")
        if account.effective_chat_id and thread_id is None:
            logger.warning(
                "Skipping news destination without configured topic account=%s",
                account.slug,
            )
            continue
        identity = (chat_id, thread_id)
        if identity in seen:
            continue
        seen.add(identity)
        destinations.append(
            NewsDestination(
                f"account:{chat_id}:{thread_id or 0}",
                chat_id,
                thread_id,
            )
        )

    if not settings.accounts and settings.telegram_default_chat_id:
        chat_id = settings.telegram_default_chat_id
        destinations.append(NewsDestination(f"default:{chat_id}", chat_id))
    return destinations


def extract_telegram_posts(html: str) -> list[tuple[str, str]]:
    posts: list[tuple[str, str]] = []
    for match in re.finditer(
        r'<div class="tgme_widget_message[^"]*"[^>]*data-post="(?P<post>[^"]+)".*?'
        r'<div class="tgme_widget_message_text js-message_text"[^>]*>(?P<text>.*?)</div>',
        html,
        flags=re.S,
    ):
        post_id = match.group("post")
        text = match.group("text")
        posts.append((post_id, text))
    return posts


def is_important_news(text: str) -> bool:
    lower = strip_tags(text).lower()
    if any(word in lower for word in IMPORTANT_NEWS_WORDS):
        return True
    return any(subject in lower for subject in IMPORTANT_NEWS_SUBJECTS) and any(
        change in lower for change in IMPORTANT_NEWS_CHANGES
    )


async def process_news_posts(
    db: Database,
    telegram: TelegramClient,
    destinations: list[NewsDestination],
    posts: list[tuple[str, str]],
    send_existing: bool,
) -> int:
    sent = 0
    for destination in destinations:
        bootstrap_marker = f"bootstrap:news:{destination.key}"
        deliver_existing = send_existing or bool(db.get_value(bootstrap_marker))
        for post_id, raw_text in posts:
            if not is_important_news(raw_text):
                continue
            event_key = f"news:{destination.key}:{post_id}"
            if db.has_event(event_key):
                continue
            payload = {"post_id": post_id, "destination": destination.key}
            if not deliver_existing:
                db.claim_event(event_key, "news", None, payload)
                continue
            delivered = await telegram.send_message(
                destination.chat_id,
                format_news(post_id, raw_text),
                destination.message_thread_id,
            )
            if delivered:
                db.claim_event(event_key, "news", None, payload)
                sent += 1
        db.set_value(bootstrap_marker, "complete")
    return sent


async def poll_news(
    db: Database,
    telegram: TelegramClient,
    destinations: list[NewsDestination],
    send_existing: bool,
) -> int:
    if not destinations:
        logger.info("Skipping news polling: no Telegram destination is configured")
        return 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.get(NEWS_URL) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"Telegram public channel fetch failed: HTTP {resp.status}")

    return await process_news_posts(
        db,
        telegram,
        destinations,
        extract_telegram_posts(text),
        send_existing,
    )


class NewsPoller:
    def __init__(self, settings: Settings, db: Database, telegram: TelegramClient) -> None:
        self.settings = settings
        self.db = db
        self.telegram = telegram
        self._stop = asyncio.Event()

    async def poll_once(self) -> int:
        return await poll_news(
            self.db,
            self.telegram,
            news_destinations(self.settings),
            self.settings.bootstrap_send_existing,
        )

    async def start(self) -> None:
        logger.info("Starting news poller every %ss", self.settings.news_poll_seconds)
        while not self._stop.is_set():
            try:
                sent = await self.poll_once()
                if sent:
                    logger.info("Sent %s news notifications", sent)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("News poll failed error_type=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.news_poll_seconds,
                )
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
