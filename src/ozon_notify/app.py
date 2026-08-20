from __future__ import annotations

import asyncio
import logging
import signal

from aiohttp import web

from .config import load_settings, mask_secret
from .database import Database
from .mvideo_pollers import MVideoPoller
from .news import NewsPoller
from .pollers import Poller
from .telegram import make_news_telegram, make_telegram
from .webhooks import WebhookHandler, try_register_ozon_webhooks


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def should_poll_news(settings: object) -> bool:
    if not getattr(settings, "news_notifications_enabled", False):
        return False
    return getattr(settings, "app_mode") == "relay" or not getattr(
        settings,
        "telegram_relay_url",
    )


async def create_app() -> web.Application:
    settings = load_settings()
    db = Database(settings.database_path)
    telegram = make_telegram(settings)
    await telegram.start()

    event_lock = asyncio.Lock()
    handler = WebhookHandler(settings, db, telegram, event_lock)
    app = web.Application()
    app["settings"] = settings
    app["db"] = db
    app["telegram"] = telegram
    app.router.add_get("/health", handler.health)
    app.router.add_get("/routes", handler.routes)
    app.router.add_post("/relay/telegram", handler.telegram_relay)

    if settings.app_mode == "full":
        app.router.add_post("/webhook/ozon/{slug}/{secret}", handler.ozon)
        poller = Poller(settings, db, telegram, event_lock)
        app["poller"] = poller
        app["poller_task"] = asyncio.create_task(poller.start())
        if settings.mvideo_account:
            mvideo_poller = MVideoPoller(settings, db, telegram, event_lock)
            app["mvideo_poller"] = mvideo_poller
            app["mvideo_poller_task"] = asyncio.create_task(mvideo_poller.start())

    if should_poll_news(settings):
        news_telegram = telegram
        if settings.telegram_news_bot_token:
            news_telegram = make_news_telegram(settings)
            await news_telegram.start()
            app["news_telegram"] = news_telegram
        news_poller = NewsPoller(settings, db, news_telegram)
        app["news_poller"] = news_poller
        app["news_poller_task"] = asyncio.create_task(news_poller.start())

    async def log_known_chats() -> None:
        try:
            chats = await telegram.find_known_chats()
            if chats:
                logging.info("Telegram known chats: %s", chats)
            else:
                logging.info("Telegram has no known chats in getUpdates yet")
        except Exception as exc:
            logging.error(
                "Could not read Telegram updates; service will keep running error_type=%s",
                type(exc).__name__,
            )

    if settings.app_mode == "full" and not settings.telegram_relay_url:
        app["telegram_chats_task"] = asyncio.create_task(log_known_chats())

    async def on_cleanup(_: web.Application) -> None:
        poller = app.get("poller")
        if poller:
            await poller.stop()
        mvideo_poller = app.get("mvideo_poller")
        if mvideo_poller:
            await mvideo_poller.stop()
        news_poller = app.get("news_poller")
        if news_poller:
            await news_poller.stop()
        for task_name in (
            "poller_task",
            "mvideo_poller_task",
            "news_poller_task",
            "telegram_chats_task",
        ):
            if task_name not in app:
                continue
            task = app[task_name]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        news_telegram = app.get("news_telegram")
        if news_telegram:
            await news_telegram.close()
        await telegram.close()

    app.on_cleanup.append(on_cleanup)

    if settings.app_env == "production" and settings.app_mode == "full":
        asyncio.create_task(try_register_ozon_webhooks(settings, telegram))

    logging.info(
        "Started ozon-notify env=%s mode=%s port=%s public=%s "
        "accounts=%s mvideo=%s token=%s",
        settings.app_env,
        settings.app_mode,
        settings.port,
        settings.public_base_url,
        [account.slug for account in settings.accounts],
        settings.mvideo_account.slug if settings.mvideo_account else None,
        mask_secret(settings.telegram_bot_token),
    )
    return app


def make_runner(app: web.Application) -> web.AppRunner:
    # Ozon requires the webhook secret in the URL, so request paths must not be logged.
    return web.AppRunner(app, access_log=None)


def make_site(runner: web.AppRunner, settings: object) -> web.TCPSite:
    return web.TCPSite(
        runner,
        getattr(settings, "bind_host"),
        getattr(settings, "port"),
    )


async def main() -> None:
    setup_logging()
    settings = load_settings()
    app = await create_app()
    runner = make_runner(app)
    await runner.setup()
    site = make_site(runner, settings)
    await site.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)
    await stop_event.wait()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
