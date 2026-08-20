import unittest
from unittest.mock import patch

from types import SimpleNamespace

from ozon_notify.app import make_runner, make_site, should_poll_news


class AppRunnerTests(unittest.TestCase):
    def test_access_log_is_disabled_to_protect_webhook_secrets(self):
        app = object()

        with patch("ozon_notify.app.web.AppRunner") as app_runner:
            make_runner(app)

        app_runner.assert_called_once_with(app, access_log=None)

    def test_news_polling_runs_on_relay_when_full_server_uses_it(self):
        relay = SimpleNamespace(
            app_mode="relay",
            telegram_relay_url=None,
            news_notifications_enabled=True,
        )
        full_with_relay = SimpleNamespace(
            app_mode="full",
            telegram_relay_url="https://relay.example/telegram",
            news_notifications_enabled=True,
        )
        full_direct = SimpleNamespace(
            app_mode="full",
            telegram_relay_url=None,
            news_notifications_enabled=True,
        )
        disabled = SimpleNamespace(
            app_mode="full",
            telegram_relay_url=None,
            news_notifications_enabled=False,
        )

        self.assertTrue(should_poll_news(relay))
        self.assertFalse(should_poll_news(full_with_relay))
        self.assertTrue(should_poll_news(full_direct))
        self.assertFalse(should_poll_news(disabled))

    def test_site_binds_to_configured_host(self):
        runner = object()
        settings = SimpleNamespace(bind_host="127.0.0.1", port=8080)

        with patch("ozon_notify.app.web.TCPSite") as tcp_site:
            make_site(runner, settings)

        tcp_site.assert_called_once_with(runner, "127.0.0.1", 8080)


if __name__ == "__main__":
    unittest.main()
