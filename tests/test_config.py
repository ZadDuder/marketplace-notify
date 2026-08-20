import os
import json
import unittest
from unittest.mock import patch

from ozon_notify.config import load_settings


class NewsChatSettingsTests(unittest.TestCase):
    def test_account_topics_are_loaded_from_json(self):
        account = {
            "name": "Test",
            "slug": "test",
            "client_id": "1",
            "api_key": "key",
            "telegram_chat_id": "-1001",
            "telegram_topics": {
                "sales": 10,
                "rfbs": "11",
                "messages": 12,
                "returns": "13",
            },
        }
        env = {
            "APP_MODE": "full",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "OZON_ACCOUNTS_JSON": json.dumps([account]),
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                settings = load_settings()

        self.assertEqual(settings.accounts[0].topic_id("sales"), 10)
        self.assertEqual(settings.accounts[0].topic_id("rfbs"), 11)
        self.assertEqual(settings.accounts[0].topic_id("messages"), 12)
        self.assertEqual(settings.accounts[0].topic_id("returns"), 13)
        self.assertIsNone(settings.accounts[0].topic_id("news"))

    def test_unknown_account_topic_is_rejected(self):
        account = {
            "name": "Test",
            "slug": "test",
            "client_id": "1",
            "api_key": "key",
            "telegram_topics": {"typo": 10},
        }
        env = {
            "APP_MODE": "full",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "OZON_ACCOUNTS_JSON": json.dumps([account]),
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                with self.assertRaisesRegex(RuntimeError, "Unknown telegram_topics"):
                    load_settings()

    def test_positive_supergroup_id_is_normalized_for_news_only(self):
        env = {
            "APP_MODE": "relay",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_DEFAULT_CHAT_ID": "-1001111111111",
            "NEWS_TELEGRAM_BOT_TOKEN": "news-test-token",
            "NEWS_TELEGRAM_CHAT_ID": "1002222222222",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                settings = load_settings()

        self.assertEqual(settings.telegram_news_chat_id, "-1002222222222")
        self.assertEqual(settings.telegram_news_bot_token, "news-test-token")
        self.assertEqual(settings.telegram_default_chat_id, "-1001111111111")

    def test_news_bot_requires_dedicated_chat(self):
        env = {
            "APP_MODE": "relay",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_DEFAULT_CHAT_ID": "-1001111111111",
            "NEWS_TELEGRAM_BOT_TOKEN": "news-test-token",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                with self.assertRaisesRegex(RuntimeError, "NEWS_TELEGRAM_CHAT_ID"):
                    load_settings()

    def test_bootstrap_is_safe_by_default(self):
        env = {
            "APP_MODE": "relay",
            "TELEGRAM_BOT_TOKEN": "test-token",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                settings = load_settings()

        self.assertFalse(settings.bootstrap_send_existing)
        self.assertFalse(settings.news_notifications_enabled)

    def test_news_notifications_can_be_enabled_explicitly(self):
        env = {
            "APP_MODE": "relay",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "NEWS_NOTIFICATIONS_ENABLED": "true",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                settings = load_settings()

        self.assertTrue(settings.news_notifications_enabled)

    def test_mvideo_uses_shared_ozon_route_by_default(self):
        account = {
            "name": "Test",
            "slug": "test",
            "client_id": "1",
            "api_key": "ozon-key",
            "telegram_chat_id": "-1001",
            "telegram_topics": {"sales": 10, "system": 20},
        }
        env = {
            "APP_MODE": "full",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "OZON_ACCOUNTS_JSON": json.dumps([account]),
            "MVIDEO_API_KEY": "mvideo-key",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                settings = load_settings()

        self.assertEqual(settings.mvideo_account.api_key, "mvideo-key")
        self.assertEqual(settings.mvideo_account.effective_chat_id, "-1001")
        self.assertEqual(settings.mvideo_account.topic_id("sales"), 10)

    def test_mvideo_is_disabled_when_key_is_absent(self):
        env = {
            "APP_MODE": "relay",
            "TELEGRAM_BOT_TOKEN": "test-token",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                settings = load_settings()

        self.assertIsNone(settings.mvideo_account)

    def test_mvideo_poll_interval_must_be_positive(self):
        account = {
            "name": "Test",
            "slug": "test",
            "client_id": "1",
            "api_key": "ozon-key",
        }
        env = {
            "APP_MODE": "full",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "OZON_ACCOUNTS_JSON": json.dumps([account]),
            "MVIDEO_API_KEY": "mvideo-key",
            "MVIDEO_POLL_SECONDS": "0",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                with self.assertRaisesRegex(RuntimeError, "MVIDEO_POLL_SECONDS"):
                    load_settings()

    def test_mvideo_lookback_must_be_positive(self):
        env = {
            "APP_MODE": "relay",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "MVIDEO_LOOKBACK_HOURS": "0",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                with self.assertRaisesRegex(RuntimeError, "MVIDEO_LOOKBACK_HOURS"):
                    load_settings()

    def test_mvideo_base_url_is_restricted_to_official_https_host(self):
        account = {
            "name": "Test",
            "slug": "test",
            "client_id": "1",
            "api_key": "ozon-key",
        }
        env = {
            "APP_MODE": "full",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "OZON_ACCOUNTS_JSON": json.dumps([account]),
            "MVIDEO_API_KEY": "mvideo-key",
            "MVIDEO_BASE_URL": "https://example.test",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                with self.assertRaisesRegex(RuntimeError, "official HTTPS"):
                    load_settings()

    def test_mvideo_uses_lkp_api_host_by_default(self):
        env = {
            "APP_MODE": "relay",
            "TELEGRAM_BOT_TOKEN": "test-token",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("ozon_notify.config.load_dotenv"):
                settings = load_settings()

        self.assertEqual(
            settings.mvideo_base_url,
            "https://api.sellers.mvideo.ru",
        )
        self.assertEqual(settings.mvideo_lookback_hours, 720)


if __name__ == "__main__":
    unittest.main()
