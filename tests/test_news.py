import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from ozon_notify.config import AccountConfig
from ozon_notify.news import (
    NewsDestination,
    NewsPoller,
    is_important_news,
    news_destinations,
    process_news_posts,
)


class FakeDatabase:
    def __init__(self):
        self.events = set()
        self.values = {}

    def has_event(self, event_key):
        return event_key in self.events

    def claim_event(self, event_key, source, account_slug=None, payload=None):
        self.events.add(event_key)
        return True

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value):
        self.values[key] = value


class NewsBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_news_is_seeded_then_new_news_is_sent(self):
        database = FakeDatabase()
        telegram = AsyncMock()
        telegram.send_message.return_value = True

        seeded = await process_news_posts(
            database,
            telegram,
            [NewsDestination("account:-1001:50", "-1001", 50)],
            [("OzonSellerAPI/1", "Старый метод FBS будет отключён 1 августа")],
            send_existing=False,
        )

        self.assertEqual(seeded, 0)
        telegram.send_message.assert_not_awaited()
        self.assertEqual(
            database.get_value("bootstrap:news:account:-1001:50"),
            "complete",
        )

        delivered = await process_news_posts(
            database,
            telegram,
            [NewsDestination("account:-1001:50", "-1001", 50)],
            [
                (
                    "OzonSellerAPI/2",
                    "Новые обязательные правила rFBS вступят в силу 1 сентября",
                )
            ],
            send_existing=False,
        )

        self.assertEqual(delivered, 1)
        telegram.send_message.assert_awaited_once_with(
            "-1001",
            unittest.mock.ANY,
            50,
        )
        self.assertIn("news:account:-1001:50:OzonSellerAPI/2", database.events)

    def test_minor_api_changelog_is_not_news_for_managers(self):
        self.assertFalse(
            is_important_news(
                "/v3/posting/fbs/list: обновили описание параметра "
                "filter.integration_type_flow"
            )
        )
        self.assertTrue(
            is_important_news(
                "Метод FBS устаревает и будет отключён 31 августа 2026 года"
            )
        )
        self.assertTrue(
            is_important_news(
                "Код маркировки станет обязательным для новых категорий"
            )
        )

    async def test_news_destinations_are_built_for_each_account_topic(self):
        settings = SimpleNamespace(
            telegram_news_bot_token=None,
            telegram_news_chat_id=None,
            telegram_default_chat_id=None,
            accounts=[
                AccountConfig(
                    "One",
                    "one",
                    "1",
                    "key",
                    "-1001",
                    {"news": 51},
                ),
                AccountConfig(
                    "Two",
                    "two",
                    "2",
                    "key",
                    "-1002",
                    {"news": 52},
                ),
            ],
        )

        destinations = news_destinations(settings)

        self.assertEqual(
            [(item.chat_id, item.message_thread_id) for item in destinations],
            [("-1001", 51), ("-1002", 52)],
        )

    async def test_accounts_sharing_one_group_receive_one_news_copy(self):
        settings = SimpleNamespace(
            telegram_news_bot_token=None,
            telegram_news_chat_id=None,
            telegram_default_chat_id=None,
            accounts=[
                AccountConfig("One", "one", "1", "key", "-1001", {"news": 51}),
                AccountConfig("Two", "two", "2", "key", "-1001", {"news": 51}),
            ],
        )

        destinations = news_destinations(settings)

        self.assertEqual(
            [(item.chat_id, item.message_thread_id) for item in destinations],
            [("-1001", 51)],
        )

    async def test_account_without_news_topic_does_not_use_general(self):
        settings = SimpleNamespace(
            telegram_news_bot_token=None,
            telegram_news_chat_id=None,
            telegram_default_chat_id="-100-default",
            accounts=[
                AccountConfig("One", "one", "1", "key", "-1001"),
            ],
        )

        destinations = news_destinations(settings)

        self.assertEqual(destinations, [])

    async def test_news_poller_prefers_dedicated_news_chat(self):
        settings = SimpleNamespace(
            news_poll_seconds=900,
            telegram_news_bot_token="news-token",
            telegram_news_chat_id="-1002222222222",
            telegram_default_chat_id="-1001111111111",
            bootstrap_send_existing=False,
            accounts=[],
        )
        database = FakeDatabase()
        telegram = AsyncMock()
        poller = NewsPoller(settings, database, telegram)

        with patch("ozon_notify.news.poll_news", new=AsyncMock(return_value=0)) as poll_news:
            await poller.poll_once()

        poll_news.assert_awaited_once_with(
            database,
            telegram,
            [NewsDestination("dedicated:-1002222222222", "-1002222222222")],
            False,
        )


if __name__ == "__main__":
    unittest.main()
