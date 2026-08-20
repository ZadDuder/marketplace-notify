import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ozon_notify.config import MVideoConfig
from ozon_notify.database import Database
from ozon_notify.mvideo import MVideoAPIError
from ozon_notify.mvideo_pollers import MVideoPoller


class MVideoPollerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(str(Path(self.temp_dir.name) / "test.sqlite3"))
        self.account = MVideoConfig(
            "М.Видео",
            "mvideo",
            "key",
            telegram_chat_id="-1001",
            telegram_topics={"sales": 10, "system": 20},
        )
        self.settings = SimpleNamespace(
            mvideo_account=self.account,
            bootstrap_send_existing=False,
            bootstrap_lookback_hours=72,
            mvideo_lookback_hours=720,
        )
        self.telegram = SimpleNamespace(
            send_to_account=AsyncMock(return_value=True)
        )
        self.poller = MVideoPoller(
            self.settings,
            self.database,
            self.telegram,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_must_be_started_before_direct_access(self):
        with self.assertRaisesRegex(RuntimeError, "session is not started"):
            _ = self.poller.session

    async def test_poll_is_noop_when_mvideo_is_disabled(self):
        self.settings.mvideo_account = None

        await self.poller.poll()

        self.telegram.send_to_account.assert_not_awaited()

    async def test_poll_uses_mvideo_specific_reconciliation_window(self):
        self.poller._session = object()
        client = SimpleNamespace(
            account=self.account,
            fbs_new_reserves=AsyncMock(return_value={"reserves": []}),
            fbs_reserves=AsyncMock(return_value={"reserves": []}),
        )

        with patch(
            "ozon_notify.mvideo_pollers.MVideoClient",
            return_value=client,
        ):
            await self.poller.poll()

        client.fbs_reserves.assert_awaited_once_with(720, 0, 100)

    async def test_first_poll_seeds_history_without_sending(self):
        client = SimpleNamespace(account=self.account)

        async def call(offset):
            return {
                "reserves": [
                    {
                        "reserveId": 1001,
                        "status": "RESERVATION_CONFIRMED",
                        "reserveCreatedAt": "2026-07-25T12:00:00Z",
                    }
                ]
            }

        await self.poller._poll_reserves(client, "fbs_reserves", call)

        self.telegram.send_to_account.assert_not_awaited()
        self.assertTrue(
            self.database.has_event(
                "mvideo:fbs:1001:status:RESERVATION_CONFIRMED"
            )
        )
        self.assertEqual(
            self.database.get_value("bootstrap:mvideo:fbs_reserves"),
            "complete",
        )

    async def test_persisted_event_payload_contains_only_deduplication_fields(self):
        client = SimpleNamespace(account=self.account)

        async def call(offset):
            return {
                "reserves": [
                    {
                        "reserveId": 1001,
                        "status": "RESERVATION_CONFIRMED",
                        "materialNumber": "900000001",
                        "supplierMaterialNumber": "ART-1",
                        "barcode": "200000000000000001",
                    }
                ]
            }

        await self.poller._poll_reserves(client, "fbs_reserves", call)

        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM events WHERE event_key = ?",
                ("mvideo:fbs:1001:status:RESERVATION_CONFIRMED",),
            ).fetchone()
        payload = json.loads(row["payload"])
        self.assertEqual(
            payload,
            {
                "order_id": "1001",
                "status": "RESERVATION_CONFIRMED",
            },
        )

    async def test_new_status_after_bootstrap_is_sent_to_sales(self):
        self.database.set_value("bootstrap:mvideo:fbs_reserves", "complete")
        client = SimpleNamespace(account=self.account)

        async def call(offset):
            return {
                "reserves": [
                    {
                        "reserveId": 1001,
                        "status": "RESERVATION_CONFIRMED",
                        "materialNumber": "900000001",
                        "supplierMaterialNumber": "TV-1",
                        "materialQuantity": 2,
                    }
                ]
            }

        await self.poller._poll_reserves(client, "fbs_reserves", call)

        self.telegram.send_to_account.assert_awaited_once()
        args, kwargs = self.telegram.send_to_account.await_args
        self.assertIs(args[0], self.account)
        self.assertIn("900000001", args[1])
        self.assertIn("TV-1", args[1])
        self.assertEqual(kwargs["topic"], "sales")

    async def test_failed_telegram_send_does_not_claim_event(self):
        self.database.set_value("bootstrap:mvideo:fbs_reserves", "complete")
        self.telegram.send_to_account.return_value = False
        client = SimpleNamespace(account=self.account)

        async def call(offset):
            return {
                "reserves": [
                    {
                        "reserveId": 1001,
                        "status": "RESERVATION_CONFIRMED",
                    }
                ]
            }

        await self.poller._poll_reserves(client, "fbs_reserves", call)

        self.assertFalse(
            self.database.has_event(
                "mvideo:fbs:1001:status:RESERVATION_CONFIRMED"
            )
        )

    async def test_same_status_from_new_and_history_sources_is_not_duplicated(self):
        self.database.set_value("bootstrap:mvideo:fbs_new_reserves", "complete")
        self.database.set_value("bootstrap:mvideo:fbs_reserves", "complete")
        client = SimpleNamespace(account=self.account)
        new_item = {"reserveId": 1001, "materialNumber": "900000001"}
        history_item = {
            **new_item,
            "status": "RESERVATION_CONFIRMED",
        }

        async def new_call(offset):
            return {"reserves": [new_item]}

        async def history_call(offset):
            return {"reserves": [history_item]}

        await self.poller._poll_reserves(
            client,
            "fbs_new_reserves",
            new_call,
        )
        await self.poller._poll_reserves(
            client,
            "fbs_reserves",
            history_call,
        )

        self.assertEqual(self.telegram.send_to_account.await_count, 1)

    async def test_resupply_is_a_distinct_actionable_status(self):
        self.database.set_value("bootstrap:mvideo:fbs_reserves", "complete")
        client = SimpleNamespace(account=self.account)

        async def call(offset):
            return {
                "reserves": [
                    {
                        "reserveId": 1001,
                        "status": "RESERVATION_CONFIRMED",
                        "reSupply": True,
                    }
                ]
            }

        await self.poller._poll_reserves(client, "fbs_reserves", call)

        self.telegram.send_to_account.assert_awaited_once()
        text = self.telegram.send_to_account.await_args.args[1]
        self.assertIn("ПОВТОРНАЯ ПОСТАВКА", text)
        self.assertTrue(
            self.database.has_event(
                "mvideo:fbs:1001:status:RESUPPLY_REQUIRED"
            )
        )

    async def test_pagination_advances_by_number_of_reserves(self):
        client = SimpleNamespace(account=self.account)
        offsets = []

        async def call(offset):
            offsets.append(offset)
            if offset == 0:
                return {
                    "reserves": [
                        {
                            "reserveId": 1001,
                            "status": "RESERVATION_CONFIRMED",
                        }
                    ],
                }
            if offset == 1:
                return {
                    "reserves": [
                        {
                            "reserveId": 1002,
                            "status": "RESERVATION_WAITING_FOR_PICKING",
                        }
                    ],
                }
            return {
                "reserves": [],
            }

        await self.poller._poll_reserves(
            client,
            "fbs_reserves",
            call,
            page_size=1,
            max_pages=3,
        )

        self.assertEqual(offsets, [0, 1, 2])
        self.assertTrue(
            self.database.has_event(
                "mvideo:fbs:1002:status:RESERVATION_WAITING_FOR_PICKING"
            )
        )

    async def test_short_page_stops_pagination(self):
        client = SimpleNamespace(account=self.account)
        offsets = []

        async def call(offset):
            offsets.append(offset)
            return {"reserves": []}

        await self.poller._poll_reserves(client, "fbs_reserves", call)

        self.assertEqual(offsets, [0])

    async def test_malformed_reserves_are_ignored_without_notifications(self):
        client = SimpleNamespace(account=self.account)

        async def call(offset):
            return {"reserves": [None, {}]}

        await self.poller._poll_reserves(client, "fbs_reserves", call)

        self.telegram.send_to_account.assert_not_awaited()

    async def test_full_page_at_safety_cap_does_not_complete_bootstrap(self):
        client = SimpleNamespace(account=self.account)

        async def call(offset):
            return {
                "reserves": [
                    {
                        "reserveId": 1001 + offset,
                        "status": "RESERVATION_CONFIRMED",
                    }
                ]
            }

        with self.assertRaisesRegex(RuntimeError, "pagination safety limit"):
            await self.poller._poll_reserves(
                client,
                "fbs_reserves",
                call,
                page_size=1,
                max_pages=1,
            )

        self.assertIsNone(
            self.database.get_value("bootstrap:mvideo:fbs_reserves")
        )

    async def test_auth_error_stays_in_logs(self):
        async def failing_call():
            raise MVideoAPIError(
                "/v2/fbs/reserves/new",
                401,
                {"message": "Ошибка авторизации"},
            )

        await self.poller._safe_call(
            self.account,
            "fbs_new_reserves",
            failing_call(),
        )

        self.telegram.send_to_account.assert_not_awaited()
        self.assertEqual(
            self.database.get_value(
                "failure-streak:mvideo:fbs_new_reserves"
            ),
            "1",
        )

    async def test_transient_error_stays_in_logs_after_three_failures(self):
        async def failing_call():
            raise MVideoAPIError(
                "/v2/fbs/reserves",
                500,
                {"message": "Внутренняя ошибка"},
            )

        for _ in range(3):
            await self.poller._safe_call(
                self.account,
                "fbs_reserves",
                failing_call(),
            )

        self.telegram.send_to_account.assert_not_awaited()
        self.assertEqual(
            self.database.get_value("failure-streak:mvideo:fbs_reserves"),
            "3",
        )


if __name__ == "__main__":
    unittest.main()
