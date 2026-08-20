import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ozon_notify.config import AccountConfig
from ozon_notify.ozon import OzonAPIError, OzonClient


class OzonQuestionClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_fbs_polling_uses_current_v4_api_and_cursor(self):
        client = OzonClient(
            SimpleNamespace(ozon_base_url="https://api-seller.ozon.ru"),
            AccountConfig("Test", "test", "1", "key"),
            AsyncMock(),
        )
        client.post = AsyncMock(return_value={"postings": []})

        await client.fbs_unfulfilled(72, cursor="next-page", limit=50)

        path, payload = client.post.await_args.args
        self.assertEqual(path, "/v4/posting/fbs/unfulfilled/list")
        self.assertEqual(payload["cursor"], "next-page")
        self.assertEqual(payload["limit"], 50)
        self.assertNotIn("offset", payload)

    async def test_supply_order_get_requests_details_for_ids(self):
        client = OzonClient(
            SimpleNamespace(ozon_base_url="https://api-seller.ozon.ru"),
            AccountConfig("Test", "test", "1", "key"),
            AsyncMock(),
        )
        client.post = AsyncMock(return_value={"orders": []})

        await client.supply_order_get([101, 102])

        client.post.assert_awaited_once_with(
            "/v3/supply-order/get",
            {"order_ids": [101, 102]},
        )

    async def test_rfbs_return_get_requests_action_details(self):
        client = OzonClient(
            SimpleNamespace(ozon_base_url="https://api-seller.ozon.ru"),
            AccountConfig("Test", "test", "1", "key"),
            AsyncMock(),
        )
        client.post = AsyncMock(return_value={"returns": {}})

        await client.rfbs_return_get(7000001)

        client.post.assert_awaited_once_with(
            "/v2/returns/rfbs/get",
            {"return_id": 7000001},
        )

    async def test_question_list_requests_newest_unanswered_questions(self):
        client = OzonClient(
            SimpleNamespace(ozon_base_url="https://api-seller.ozon.ru"),
            AccountConfig("Test", "test", "1", "key"),
            AsyncMock(),
        )
        client.post = AsyncMock(return_value={"questions": []})

        await client.question_list()

        client.post.assert_awaited_once_with(
            "/v1/question/list",
            {
                "limit": 100,
                "sort_dir": "DESC",
                "filter": {"status": "NEW"},
            },
        )

    async def test_question_list_treats_missing_premium_plus_as_unavailable(self):
        client = OzonClient(
            SimpleNamespace(ozon_base_url="https://api-seller.ozon.ru"),
            AccountConfig("Test", "test", "1", "key"),
            AsyncMock(),
        )
        client.post = AsyncMock(
            side_effect=OzonAPIError(
                "/v1/question/list",
                403,
                {
                    "code": 7,
                    "message": "Information is only available with a Premium Plus subscription.",
                },
            )
        )

        self.assertEqual(await client.question_list(), {"questions": []})

    async def test_question_list_reraises_unrelated_permission_error(self):
        client = OzonClient(
            SimpleNamespace(ozon_base_url="https://api-seller.ozon.ru"),
            AccountConfig("Test", "test", "1", "key"),
            AsyncMock(),
        )
        error = OzonAPIError(
            "/v1/question/list",
            403,
            {"code": 7, "message": "Permission denied"},
        )
        client.post = AsyncMock(side_effect=error)

        with self.assertRaises(OzonAPIError) as raised:
            await client.question_list()

        self.assertIs(raised.exception, error)


if __name__ == "__main__":
    unittest.main()
