from __future__ import annotations

import asyncio
import hmac
import logging
from datetime import UTC, datetime

from aiohttp import web

from .config import AccountConfig, Settings
from .database import Database
from .formatters import format_webhook, webhook_posting_status_notification_key
from .ozon import OzonAPIError, OzonClient
from .routing import (
    posting_route_key,
    should_notify_webhook,
    webhook_posting_number,
    webhook_topic as route_webhook_topic,
)
from .telegram import TelegramClient

logger = logging.getLogger(__name__)


def webhook_topic(
    payload: dict[str, object],
    known_posting_topic: str | None = None,
) -> str:
    return route_webhook_topic(payload, known_posting_topic)


class WebhookHandler:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        telegram: TelegramClient,
        event_lock: asyncio.Lock | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.telegram = telegram
        self.event_lock = event_lock or asyncio.Lock()
        self.accounts_by_slug = {account.slug: account for account in settings.accounts}

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"ok": True, "service": "ozon-notify", "mode": self.settings.app_mode}
        )

    async def routes(self, request: web.Request) -> web.Response:
        reveal_secret = request.headers.get("X-Ozon-Notify-Secret") == self.settings.webhook_secret

        def webhook_url(account: AccountConfig) -> str:
            url = self.settings.webhook_url_template.format(slug=account.slug)
            if reveal_secret:
                return url
            return url.replace(self.settings.webhook_secret, "***")

        return web.json_response(
            {
                "ok": True,
                "webhooks": {
                    account.slug: webhook_url(account) for account in self.settings.accounts
                },
            }
        )

    async def telegram_relay(self, request: web.Request) -> web.Response:
        relay_secret = self.settings.telegram_relay_secret
        received_secret = request.headers.get("X-Ozon-Notify-Relay-Secret", "")
        if not relay_secret or not hmac.compare_digest(received_secret, relay_secret):
            raise web.HTTPForbidden()

        try:
            payload = await request.json()
        except Exception as exc:
            raise web.HTTPBadRequest(text="Invalid JSON") from exc

        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Invalid payload")

        chat_id = str(payload.get("chat_id") or self.settings.telegram_default_chat_id or "").strip()
        text = str(payload.get("text") or "").strip()
        raw_thread_id = payload.get("message_thread_id")
        try:
            message_thread_id = int(raw_thread_id) if raw_thread_id is not None else None
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text="message_thread_id must be an integer") from exc
        if not chat_id:
            raise web.HTTPBadRequest(text="chat_id is required")
        if not text:
            raise web.HTTPBadRequest(text="text is required")
        if len(text) > 10000:
            raise web.HTTPRequestEntityTooLarge(max_size=10000, actual_size=len(text))

        sent = await self.telegram.send_message_direct(
            chat_id,
            text,
            message_thread_id,
        )
        if not sent:
            return web.json_response({"ok": False}, status=502)
        return web.json_response({"ok": True})

    async def ozon(self, request: web.Request) -> web.Response:
        slug = request.match_info["slug"]
        secret = request.match_info["secret"]
        if secret != self.settings.webhook_secret:
            raise web.HTTPForbidden()
        account = self.accounts_by_slug.get(slug)
        if not account:
            raise web.HTTPNotFound(text="Unknown account")

        payload = await request.json()
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Invalid payload")
        message_type = payload.get("message_type") or payload.get("type")
        if message_type == "TYPE_PING":
            return web.json_response(
                {
                    "version": "1.0",
                    "name": "Ozon Notify",
                    "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            )

        event_key, text = format_webhook(account, payload)
        canonical_key = webhook_posting_status_notification_key(account, payload)
        async with self.event_lock:
            if self.db.has_event(event_key):
                return web.json_response({"result": True})
            posting_number = webhook_posting_number(payload)
            known_topic = (
                self.db.get_value(
                    posting_route_key(account.slug, posting_number)
                )
                if posting_number
                else None
            )
            topic = webhook_topic(payload, known_topic)
            message_type = str(
                payload.get("message_type") or payload.get("type") or ""
            ).upper()
            if (
                message_type != "TYPE_ORDER_NEW"
                and posting_number
                and topic in {"sales", "rfbs"}
            ):
                self.db.set_value(
                    posting_route_key(account.slug, posting_number),
                    topic,
                )
            if not should_notify_webhook(payload, topic):
                self.db.claim_event(event_key, "webhook", account.slug, payload)
                return web.json_response({"result": True})
            if canonical_key and self.db.was_notification_recent(
                canonical_key,
                cooldown_seconds=10 * 60,
            ):
                self.db.claim_event(event_key, "webhook", account.slug, payload)
                return web.json_response({"result": True})
            sent = await self.telegram.send_to_account(
                account,
                text,
                topic=topic,
            )
            if not sent:
                raise web.HTTPServiceUnavailable(
                    text="Temporary notification delivery failure"
                )
            self.db.claim_event(event_key, "webhook", account.slug, payload)
            if canonical_key:
                self.db.mark_notification(canonical_key)
        return web.json_response({"result": True})


async def try_register_ozon_webhooks(settings: Settings, telegram: TelegramClient) -> None:
    push_types = [
        "TYPE_NEW_MESSAGE",
        "TYPE_UPDATE_MESSAGE",
    ]
    del telegram
    # Registration is best-effort: polling remains the safety net.
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
        for account in settings.accounts:
            client = OzonClient(settings, account, session)
            url = settings.webhook_url_template.format(slug=account.slug)
            try:
                action = await ensure_ozon_webhook_with_retry(client, url, push_types)
                logger.info(
                    "Ozon webhook ready for %s action=%s",
                    account.slug,
                    action,
                )
            except OzonAPIError as exc:
                message = ""
                if isinstance(exc.body, dict):
                    message = str(exc.body.get("message", ""))
                if exc.status == 400 and "URL already exists" in message:
                    logger.info("Ozon webhook for %s is already registered", account.slug)
                    continue
                logger.warning(
                    "Could not register Ozon webhook for %s status=%s path=%s",
                    account.slug,
                    exc.status,
                    exc.path,
                )
            except Exception as exc:
                logger.warning(
                    "Could not register Ozon webhook for %s error_type=%s",
                    account.slug,
                    type(exc).__name__,
                )


async def ensure_ozon_webhook(
    client: OzonClient,
    desired_url: str,
    push_types: list[str],
) -> str:
    current = await client.notification_list()
    urls = current.get("urls") if isinstance(current, dict) else None
    entries = urls if isinstance(urls, list) else []

    for entry in entries:
        if isinstance(entry, dict) and entry.get("url") == desired_url:
            return "existing"

    requested = set(push_types)
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("id") is None:
            continue
        raw_types = entry.get("types")
        existing_types = (
            {
                str(item.get("type"))
                for item in raw_types
                if isinstance(item, dict) and item.get("type")
            }
            if isinstance(raw_types, list)
            else set()
        )
        if requested & existing_types:
            await client.notification_update(entry["id"], desired_url)
            return "updated"

    await client.notification_set(desired_url, push_types)
    return "created"


async def ensure_ozon_webhook_with_retry(
    client: OzonClient,
    desired_url: str,
    push_types: list[str],
) -> str:
    for attempt in range(3):
        try:
            return await ensure_ozon_webhook(client, desired_url, push_types)
        except OzonAPIError as exc:
            if exc.status != 429 or attempt == 2:
                raise
            delay = 5 * (2**attempt)
            logger.info(
                "Ozon webhook registration rate-limited for %s; retrying in %ss",
                client.account.slug,
                delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("Ozon webhook retry loop ended unexpectedly")
