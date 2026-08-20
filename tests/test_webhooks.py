import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web

from ozon_notify.config import AccountConfig
from ozon_notify.ozon import OzonAPIError
from ozon_notify.webhooks import (
    WebhookHandler,
    ensure_ozon_webhook,
    ensure_ozon_webhook_with_retry,
    webhook_topic,
)


class FakeDatabase:
    def __init__(self):
        self.events = set()
        self.notifications = set()
        self.values = {}

    def has_event(self, event_key):
        return event_key in self.events

    def claim_event(self, event_key, source, account_slug=None, payload=None):
        self.events.add(event_key)
        return True

    def was_notification_recent(self, key, cooldown_seconds):
        return key in self.notifications

    def mark_notification(self, key):
        self.notifications.add(key)

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value):
        self.values[key] = value


class WebhookSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_webhooks_are_routed_by_business_event(self):
        self.assertEqual(webhook_topic({"message_type": "TYPE_NEW_POSTING"}), "sales")
        self.assertEqual(
            webhook_topic(
                {
                    "message_type": "TYPE_NEW_POSTING",
                    "tpl_integration_type": "aggregator",
                }
            ),
            "rfbs",
        )
        self.assertEqual(
            webhook_topic(
                {
                    "message_type": "TYPE_NEW_MESSAGE",
                    "chat_type": "Buyer_Seller",
                    "data": ["Как оформить возврат заказа?"],
                }
            ),
            "messages",
        )
        self.assertEqual(
            webhook_topic(
                {
                    "message_type": "TYPE_NEW_MESSAGE",
                    "chat_type": "Seller_Notification_FBS",
                    "data": ["Курьер приехал за заказом, передайте отправление."],
                }
            ),
            "logistics",
        )
        self.assertEqual(
            webhook_topic(
                {
                    "message_type": "TYPE_NEW_MESSAGE",
                    "chat_type": "Seller_Notification_FBS",
                    "data": ["У вас есть возвраты в точке выдачи."],
                }
            ),
            "returns",
        )
        self.assertEqual(
            webhook_topic(
                {
                    "message_type": "TYPE_NEW_MESSAGE",
                    "chat_type": "Seller_Notification_Findoc",
                    "data": ["Нужно согласовать акт сверки."],
                }
            ),
            "finance",
        )
        self.assertEqual(
            webhook_topic(
                {
                    "message_type": "TYPE_NEW_MESSAGE",
                    "chat_type": "Seller_Notification_Findoc",
                    "data": [
                        "Новый акт по возвратам. Получили возвраты в ПВЗ; "
                        "проверить качество нужно за 5 дней и открыть спор."
                    ],
                }
            ),
            "returns",
        )
        self.assertEqual(
            webhook_topic(
                {
                    "message_type": "TYPE_NEW_MESSAGE",
                    "chat_type": "Seller_Notification_FBO",
                    "data": [
                        "Поставка принята. Нужно согласовать акт приёмки "
                        "в течение 7 календарных дней."
                    ],
                }
            ),
            "supplies",
        )
        self.assertEqual(
            webhook_topic(
                {
                    "message_type": "TYPE_NEW_MESSAGE",
                    "chat_type": "Seller_Notification_Content",
                    "data": [
                        "Поступила жалоба покупателя. Откройте спор до 15.08.2026."
                    ],
                }
            ),
            "messages",
        )
        self.assertEqual(
            webhook_topic(
                {
                    "message_type": "TYPE_NEW_MESSAGE",
                    "chat_type": "Seller_Notification_Major",
                    "data": ["Изменяются тарифы на логистику."],
                }
            ),
            "news",
        )
        self.assertEqual(webhook_topic({"message_type": "TYPE_STOCKS_CHANGED"}), "system")

    async def test_rfbs_route_is_remembered_while_routine_webhooks_are_deferred(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(accounts=[account], webhook_secret="secret")
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        handler = WebhookHandler(settings, database, telegram)

        new_posting = SimpleNamespace(
            match_info={"slug": "test", "secret": "secret"},
            json=AsyncMock(
                return_value={
                    "message_type": "TYPE_NEW_POSTING",
                    "posting_number": "rfbs-123",
                    "tpl_integration_type": "aggregator",
                }
            ),
        )
        await handler.ozon(new_posting)

        self.assertEqual(
            database.get_value("posting-route:test:rfbs-123"),
            "rfbs",
        )
        telegram.send_to_account.assert_not_awaited()

        status_change = SimpleNamespace(
            match_info={"slug": "test", "secret": "secret"},
            json=AsyncMock(
                return_value={
                    "message_type": "TYPE_STATE_CHANGED",
                    "posting_number": "rfbs-123",
                    "new_state": "awaiting_deliver",
                }
            ),
        )
        await handler.ozon(status_change)

        telegram.send_to_account.assert_not_awaited()

        cancellation = SimpleNamespace(
            match_info={"slug": "test", "secret": "secret"},
            json=AsyncMock(
                return_value={
                    "message_type": "TYPE_POSTING_CANCELLED",
                    "posting_number": "rfbs-123",
                }
            ),
        )
        await handler.ozon(cancellation)

        telegram.send_to_account.assert_not_awaited()

    async def test_order_level_webhook_without_posting_details_is_ignored(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(accounts=[account], webhook_secret="secret")
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        handler = WebhookHandler(settings, database, telegram)
        request = SimpleNamespace(
            match_info={"slug": "test", "secret": "secret"},
            json=AsyncMock(
                return_value={
                    "message_type": "TYPE_ORDER_NEW",
                    "order_id": 900012345,
                    "order_number": "900012345",
                }
            ),
        )

        await handler.ozon(request)

        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(len(database.events), 1)

    async def test_fbs_new_and_routine_state_webhooks_wait_for_detailed_polling(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(accounts=[account], webhook_secret="secret")
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        handler = WebhookHandler(settings, database, telegram)

        for payload in (
            {
                "message_type": "TYPE_NEW_POSTING",
                "posting_number": "fbs-123",
                "tpl_integration_type": "ozon",
            },
            {
                "message_type": "TYPE_STATE_CHANGED",
                "posting_number": "fbs-123",
                "new_state": "awaiting_deliver",
            },
        ):
            request = SimpleNamespace(
                match_info={"slug": "test", "secret": "secret"},
                json=AsyncMock(return_value=payload),
            )
            await handler.ozon(request)

        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(database.get_value("posting-route:test:fbs-123"), "sales")

    async def test_system_event_is_recorded_but_not_sent_to_telegram(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(accounts=[account], webhook_secret="secret")
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        handler = WebhookHandler(settings, database, telegram)
        request = SimpleNamespace(
            match_info={"slug": "test", "secret": "secret"},
            json=AsyncMock(
                return_value={
                    "message_type": "TYPE_STOCKS_CHANGED",
                    "sku": 123,
                }
            ),
        )

        response = await handler.ozon(request)

        self.assertEqual(response.status, 200)
        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(len(database.events), 1)

    async def test_non_actionable_chat_updates_are_acknowledged_without_sending(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(accounts=[account], webhook_secret="secret")
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        handler = WebhookHandler(settings, database, telegram)
        request = SimpleNamespace(
            match_info={"slug": "test", "secret": "secret"},
            json=AsyncMock(
                return_value={
                    "message_type": "TYPE_MESSAGE_READ",
                    "chat_id": "chat-1",
                    "message_id": "message-1",
                }
            ),
        )

        response = await handler.ozon(request)

        self.assertEqual(response.status, 200)
        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(len(database.events), 1)

    async def test_failed_telegram_delivery_returns_retryable_http_error(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(accounts=[account], webhook_secret="secret")
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=False))
        handler = WebhookHandler(settings, database, telegram)
        request = SimpleNamespace(
            match_info={"slug": "test", "secret": "secret"},
            json=AsyncMock(
                return_value={
                    "message_type": "TYPE_NEW_MESSAGE",
                    "chat_type": "Seller_Notification_FBO",
                    "message_id": "message-1",
                    "data": [
                        "Поступила жалоба от покупателя по SKU 123. "
                        "Откройте спор до 15.08.2026."
                    ],
                }
            ),
        )

        with self.assertRaises(web.HTTPServiceUnavailable):
            await handler.ozon(request)

        self.assertEqual(database.events, set())

    async def test_updated_buyer_message_is_silent(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(accounts=[account], webhook_secret="secret")
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        handler = WebhookHandler(settings, database, telegram)
        request = SimpleNamespace(
            match_info={"slug": "test", "secret": "secret"},
            json=AsyncMock(
                return_value={
                    "message_type": "TYPE_UPDATE_MESSAGE",
                    "chat_type": "Buyer_Seller",
                    "message_id": "message-1",
                    "data": ["Уточнённый вопрос покупателя"],
                }
            ),
        )

        await handler.ozon(request)

        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(len(database.events), 1)

    async def test_buyer_complaint_without_dispute_deadline_is_silent(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(accounts=[account], webhook_secret="secret")
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        handler = WebhookHandler(settings, database, telegram)
        request = SimpleNamespace(
            match_info={"slug": "test", "secret": "secret"},
            json=AsyncMock(
                return_value={
                    "message_type": "TYPE_NEW_MESSAGE",
                    "chat_type": "Seller_Notification_Content",
                    "data": [
                        "Покупатель пожаловался на товар. Спор по жалобе закрыт."
                    ],
                }
            ),
        )

        await handler.ozon(request)

        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(len(database.events), 1)

    def test_ozon_error_string_does_not_include_response_body(self):
        error = OzonAPIError(
            "/v1/notification/set",
            400,
            {"message": "bad URL https://example.test/webhook/secret-value"},
        )

        self.assertNotIn("secret-value", str(error))
        self.assertIn("HTTP 400", str(error))

    async def test_recent_polling_state_suppresses_webhook_duplicate(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(
            accounts=[account],
            webhook_secret="secret",
        )
        database = FakeDatabase()
        database.mark_notification("test:posting-status:123:awaiting_packaging")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        handler = WebhookHandler(settings, database, telegram)
        request = SimpleNamespace(
            match_info={"slug": "test", "secret": "secret"},
            json=AsyncMock(
                return_value={
                    "message_type": "TYPE_NEW_POSTING",
                    "posting_number": "123",
                }
            ),
        )

        response = await handler.ozon(request)

        self.assertEqual(response.status, 200)
        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(len(database.events), 1)

    async def test_existing_types_are_moved_to_new_webhook_url(self):
        client = SimpleNamespace(
            notification_list=AsyncMock(
                return_value={
                    "urls": [
                        {
                            "id": 42,
                            "url": "https://old.example/webhook",
                            "types": [{"type": "TYPE_NEW_POSTING"}],
                        }
                    ]
                }
            ),
            notification_update=AsyncMock(return_value={}),
            notification_set=AsyncMock(return_value={}),
        )

        action = await ensure_ozon_webhook(
            client,
            "https://new.example/webhook",
            ["TYPE_NEW_POSTING"],
        )

        self.assertEqual(action, "updated")
        client.notification_update.assert_awaited_once_with(
            42,
            "https://new.example/webhook",
        )
        client.notification_set.assert_not_awaited()

    async def test_matching_webhook_url_is_left_unchanged(self):
        client = SimpleNamespace(
            notification_list=AsyncMock(
                return_value={
                    "urls": [
                        {
                            "id": 42,
                            "url": "https://new.example/webhook",
                            "types": [{"type": "TYPE_NEW_POSTING"}],
                        }
                    ]
                }
            ),
            notification_update=AsyncMock(return_value={}),
            notification_set=AsyncMock(return_value={}),
        )

        action = await ensure_ozon_webhook(
            client,
            "https://new.example/webhook",
            ["TYPE_NEW_POSTING"],
        )

        self.assertEqual(action, "existing")
        client.notification_update.assert_not_awaited()
        client.notification_set.assert_not_awaited()

    async def test_webhook_registration_retries_transient_rate_limit(self):
        client = SimpleNamespace(
            account=SimpleNamespace(slug="test"),
            notification_list=AsyncMock(
                side_effect=[
                    OzonAPIError("/v1/notification/list", 429, {}),
                    {"urls": []},
                ]
            ),
            notification_update=AsyncMock(return_value={}),
            notification_set=AsyncMock(return_value={}),
        )

        with patch("ozon_notify.webhooks.asyncio.sleep", new=AsyncMock()) as sleep:
            action = await ensure_ozon_webhook_with_retry(
                client,
                "https://new.example/webhook",
                ["TYPE_NEW_POSTING"],
            )

        self.assertEqual(action, "created")
        sleep.assert_awaited_once_with(5)
        self.assertEqual(client.notification_list.await_count, 2)


if __name__ == "__main__":
    unittest.main()
