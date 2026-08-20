import unittest
from types import SimpleNamespace

from ozon_notify.config import MVideoConfig
from ozon_notify.mvideo import MVideoAPIError, MVideoClient


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

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class MVideoClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.account = MVideoConfig("М.Видео", "mvideo", "secret-key")
        self.settings = SimpleNamespace(
            mvideo_base_url="https://api.sellers.mvideo.ru"
        )

    async def test_get_uses_documented_api_key_header(self):
        session = FakeSession([FakeResponse(200, {"reserves": []})])
        client = MVideoClient(self.settings, self.account, session)

        result = await client.get("/v2/fbs/reserves", {"limit": 1, "next": 0})

        self.assertEqual(result, {"reserves": []})
        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["headers"]["api-key"], "secret-key")
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["params"], {"limit": 1, "next": 0})

    async def test_new_reserves_uses_dedicated_read_only_endpoint(self):
        client = MVideoClient(self.settings, self.account, FakeSession([]))
        calls = []

        async def fake_get(path, params=None):
            calls.append((path, params))
            return {"reserves": []}

        client.get = fake_get

        await client.fbs_new_reserves()

        self.assertEqual(calls, [("/v2/fbs/reserves/new", None)])

    async def test_reserve_history_uses_date_window_and_offset(self):
        client = MVideoClient(self.settings, self.account, FakeSession([]))
        calls = []

        async def fake_get(path, params=None):
            calls.append((path, params))
            return {"reserves": []}

        client.get = fake_get

        await client.fbs_reserves(72, offset=200, limit=100)

        path, params = calls[0]
        self.assertEqual(path, "/v2/fbs/reserves")
        self.assertEqual(params["next"], 200)
        self.assertEqual(params["limit"], 100)
        self.assertIn("dateFrom", params)
        self.assertIn("dateTo", params)

    async def test_api_error_does_not_expose_key(self):
        session = FakeSession(
            [
                FakeResponse(
                    401,
                    {
                        "code": "401",
                        "message": "Ошибка авторизации",
                    },
                )
            ]
        )
        client = MVideoClient(self.settings, self.account, session)

        with self.assertRaises(MVideoAPIError) as raised:
            await client.get("/v2/fbs/reserves/new")

        self.assertNotIn("secret-key", str(raised.exception))
        self.assertEqual(raised.exception.status, 401)


if __name__ == "__main__":
    unittest.main()
