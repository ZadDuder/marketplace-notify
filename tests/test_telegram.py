import logging
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp

from ozon_notify.config import AccountConfig
from ozon_notify.telegram import TelegramClient, make_news_telegram


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TelegramClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_topic_id_is_included_in_send_message_payload(self):
        session = FakeSession(
            [FakeResponse(200, {"ok": True, "result": {"message_id": 1}})]
        )
        client = TelegramClient("secret-token", min_send_interval=0)
        client._session = session

        sent = await client.send_message_direct("-1001", "test", 42)

        self.assertTrue(sent)
        self.assertEqual(session.calls[0][1]["json"]["message_thread_id"], 42)

    async def test_account_topic_is_used_for_routing(self):
        client = TelegramClient("secret-token", default_chat_id="-100-default")
        client.send_message = AsyncMock(return_value=True)
        account = AccountConfig(
            "Test",
            "test",
            "1",
            "key",
            telegram_chat_id="-100-account",
            telegram_topics={"returns": 77},
        )

        sent = await client.send_to_account(account, "return", topic="returns")

        self.assertTrue(sent)
        client.send_message.assert_awaited_once_with("-100-account", "return", 77)

    async def test_explicit_account_group_never_falls_back_to_general_topic(self):
        client = TelegramClient("secret-token", default_chat_id="-100-default")
        client.send_message = AsyncMock(return_value=True)
        account = AccountConfig(
            "Test",
            "test",
            "1",
            "key",
            telegram_chat_id="-100-account",
        )

        sent = await client.send_to_account(account, "finance", topic="finance")

        self.assertFalse(sent)
        client.send_message.assert_not_awaited()

    async def test_news_client_uses_dedicated_bot_without_relay(self):
        settings = SimpleNamespace(
            telegram_news_bot_token="news-token",
            telegram_bot_token="orders-token",
            telegram_news_chat_id="-1002",
            telegram_default_chat_id="-1001",
            telegram_proxy_url=None,
        )

        client = make_news_telegram(settings)

        self.assertEqual(client.token, "news-token")
        self.assertEqual(client.default_chat_id, "-1002")
        self.assertIsNone(client.relay_url)

    async def test_news_route_falls_back_to_primary_bot_and_group(self):
        settings = SimpleNamespace(
            telegram_news_bot_token=None,
            telegram_bot_token="company-token",
            telegram_news_chat_id=None,
            telegram_default_chat_id="-1002222222222",
            telegram_proxy_url=None,
        )

        client = make_news_telegram(settings)

        self.assertEqual(client.token, "company-token")
        self.assertEqual(client.default_chat_id, "-1002222222222")

    async def test_retries_after_telegram_rate_limit(self):
        session = FakeSession(
            [
                FakeResponse(
                    429,
                    {
                        "ok": False,
                        "error_code": 429,
                        "description": "Too Many Requests",
                        "parameters": {"retry_after": 2},
                    },
                ),
                FakeResponse(200, {"ok": True, "result": {"message_id": 1}}),
            ]
        )
        client = TelegramClient("secret-token", min_send_interval=0)
        client._session = session

        with patch("ozon_notify.telegram.asyncio.sleep", new=AsyncMock()) as sleep:
            sent = await client.send_message_direct("-1001", "test")

        self.assertTrue(sent)
        self.assertEqual(len(session.calls), 2)
        sleep.assert_awaited_once_with(3)

    async def test_network_error_does_not_expose_bot_token_in_logs(self):
        token = "super-secret-token"
        error = aiohttp.ClientConnectionError(
            f"cannot connect to https://api.telegram.org/bot{token}/sendMessage"
        )
        client = TelegramClient(token, min_send_interval=0)
        client._session = FakeSession([error, error, error])

        with self.assertLogs("ozon_notify.telegram", logging.ERROR) as captured:
            with patch("ozon_notify.telegram.asyncio.sleep", new=AsyncMock()):
                sent = await client.send_message_direct("-1001", "test")

        self.assertFalse(sent)
        combined_logs = "\n".join(captured.output)
        self.assertNotIn(token, combined_logs)
        self.assertIn("ClientConnectionError", combined_logs)

    async def test_serializes_and_spaces_messages_for_one_channel(self):
        session = FakeSession(
            [
                FakeResponse(200, {"ok": True, "result": {"message_id": 1}}),
                FakeResponse(200, {"ok": True, "result": {"message_id": 2}}),
            ]
        )
        client = TelegramClient("secret-token", min_send_interval=1.05)
        client._session = session

        with patch("ozon_notify.telegram.asyncio.sleep", new=AsyncMock()) as sleep:
            results = [
                await client.send_message_direct("-1001", "first"),
                await client.send_message_direct("-1001", "second"),
            ]

        self.assertEqual(results, [True, True])
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(sleep.await_count, 1)
        self.assertGreater(sleep.await_args.args[0], 1.0)


if __name__ == "__main__":
    unittest.main()
