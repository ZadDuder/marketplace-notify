import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ozon_notify.config import AccountConfig
from ozon_notify.ozon import OzonAPIError
from ozon_notify.pollers import Poller


class FakeDatabase:
    def __init__(self):
        self.events = set()
        self.values = {}
        self.notifications = {}

    def has_event(self, event_key):
        return event_key in self.events

    def claim_event(self, event_key, source, account_slug=None, payload=None):
        self.events.add(event_key)
        return True

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value):
        self.values[key] = value

    def was_notification_recent(self, key, cooldown_seconds):
        return key in self.notifications

    def mark_notification(self, key):
        self.notifications[key] = True

    def should_send_error(self, key, cooldown_seconds):
        return True


class PollerBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_important_poll_only_checks_actionable_orders(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(
            accounts=[account],
            bootstrap_send_existing=False,
            bootstrap_lookback_hours=72,
        )
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        poller._session = AsyncMock()
        client = SimpleNamespace(
            account=account,
            fbs_unfulfilled=AsyncMock(
                return_value={"result": {"postings": [], "has_next": False}}
            ),
            fbs_recent=AsyncMock(
                return_value={"result": {"postings": [], "has_next": False}}
            ),
            question_list=AsyncMock(return_value={"questions": []}),
        )

        with patch("ozon_notify.pollers.OzonClient", return_value=client):
            await poller.poll_important()

        client.question_list.assert_not_awaited()
        self.assertIsNone(database.get_value("bootstrap:test:questions"))

    async def test_single_transient_ozon_error_does_not_alert(self):
        account = AccountConfig("Test", "test", "1", "key")
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(SimpleNamespace(), database, telegram)

        async def fail():
            raise OzonAPIError("/temporary", 500, {})

        await poller._safe_account_call(account, "returns", fail())

        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(database.get_value("failure-streak:test:returns"), "1")

    async def test_repeated_ozon_errors_stay_in_logs_and_success_resets_streak(self):
        account = AccountConfig("Test", "test", "1", "key")
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(SimpleNamespace(), database, telegram)

        async def fail():
            raise OzonAPIError("/temporary", 500, {})

        for _ in range(3):
            await poller._safe_account_call(account, "returns", fail())

        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(database.get_value("failure-streak:test:returns"), "3")

        async def succeed():
            return None

        await poller._safe_account_call(account, "returns", succeed())
        self.assertEqual(database.get_value("failure-streak:test:returns"), "0")

    async def test_first_generic_poll_seeds_existing_items_without_sending(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(
            bootstrap_send_existing=False,
            bootstrap_lookback_hours=72,
        )
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(account=account)
        call = AsyncMock(return_value={"items": [{"id": "old-item"}]})

        await poller._poll_generic(client, "returns", "Returns", call, ["items"])

        telegram.send_to_account.assert_not_awaited()
        self.assertTrue(database.events)
        self.assertEqual(database.get_value("bootstrap:test:returns"), "complete")

    async def test_items_are_sent_after_source_bootstrap_is_complete(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value("bootstrap:test:returns", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(account=account)
        call = AsyncMock(return_value={"items": [{"id": "new-item"}]})

        await poller._poll_generic(client, "returns", "Returns", call, ["items"])

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(
            telegram.send_to_account.await_args.kwargs,
            {"topic": "returns"},
        )
        self.assertTrue(database.events)

    async def test_new_question_is_sent_to_messages_topic(self):
        account = AccountConfig("Retail North", "retail-north", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value("bootstrap:retail-north:questions", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(account=account)
        question = {
            "id": "question-1",
            "sku": 900001,
            "text": "Когда товар появится в наличии?",
            "published_at": "2026-07-20T17:30:00Z",
            "status": "NEW",
        }

        await poller._poll_generic(
            client,
            "questions",
            "Новый вопрос покупателя",
            AsyncMock(return_value={"questions": [question]}),
            ["questions"],
        )

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(
            telegram.send_to_account.await_args.kwargs,
            {"topic": "messages"},
        )
        self.assertIn(
            "НОВЫЙ ВОПРОС ПОКУПАТЕЛЯ",
            telegram.send_to_account.await_args.args[1],
        )
        self.assertTrue(database.events)

    async def test_fbs_and_rfbs_postings_use_separate_topics(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(
            bootstrap_send_existing=False,
            bootstrap_lookback_hours=72,
        )
        database = FakeDatabase()
        database.set_value("bootstrap:test:fbs_unfulfilled", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(
            account=account,
            fbs_unfulfilled=AsyncMock(
                return_value={
                    "result": {
                        "postings": [
                            {
                                "posting_number": "fbs-1",
                                "status": "awaiting_packaging",
                                "integration_type_flow": "ozon",
                                "products": [],
                            },
                            {
                                "posting_number": "rfbs-1",
                                "status": "awaiting_packaging",
                                "integration_type_flow": "aggregator",
                                "products": [],
                            },
                        ],
                        "has_next": False,
                    }
                }
            ),
        )

        await poller._poll_fbs(client, unfulfilled=True)

        self.assertEqual(
            [call.kwargs["topic"] for call in telegram.send_to_account.await_args_list],
            ["sales", "rfbs"],
        )
        self.assertEqual(database.get_value("posting-route:test:fbs-1"), "sales")
        self.assertEqual(database.get_value("posting-route:test:rfbs-1"), "rfbs")

    async def test_rfbs_order_is_announced_once_with_full_polling_details(self):
        account = AccountConfig("Retail West", "retail-west", "1", "key")
        settings = SimpleNamespace(
            bootstrap_send_existing=False,
            bootstrap_lookback_hours=72,
        )
        database = FakeDatabase()
        database.set_value("bootstrap:retail-west:fbs_unfulfilled", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        first = {
            "posting_number": "9000123456-0001-1",
            "order_number": "900012345",
            "status": "awaiting_registration",
            "integration_type_flow": "aggregator",
            "in_process_at": "2026-08-05T06:28:00Z",
            "products": [{"name": "Экшн-камера Model X", "quantity": 1}],
        }
        client = SimpleNamespace(
            account=account,
            fbs_unfulfilled=AsyncMock(
                side_effect=[
                    {"result": {"postings": [first], "has_next": False}},
                    {
                        "result": {
                            "postings": [{**first, "status": "awaiting_deliver"}],
                            "has_next": False,
                        }
                    },
                ]
            ),
        )

        await poller._poll_fbs(client, unfulfilled=True)
        await poller._poll_fbs(client, unfulfilled=True)

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(
            telegram.send_to_account.await_args.kwargs,
            {"topic": "rfbs"},
        )
        self.assertIn(
            "НОВЫЙ ЗАКАЗ realFBS",
            telegram.send_to_account.await_args.args[1],
        )
        self.assertEqual(
            database.get_value("rfbs-announced:retail-west:9000123456-0001-1"),
            "sent",
        )

    async def test_fbs_order_is_announced_once_and_routine_states_are_silent(self):
        account = AccountConfig("Demo Store", "demo-store", "1", "key")
        settings = SimpleNamespace(
            bootstrap_send_existing=False,
            bootstrap_lookback_hours=72,
        )
        database = FakeDatabase()
        database.set_value("bootstrap:demo-store:fbs_unfulfilled", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        first = {
            "posting_number": "12345-0001-1",
            "order_number": "12345-0001",
            "status": "awaiting_packaging",
            "integration_type_flow": "ozon",
            "shipment_date_without_delay": "2026-08-11T12:00:00Z",
            "products": [{"name": "Чехол", "quantity": 2}],
        }
        client = SimpleNamespace(
            account=account,
            fbs_unfulfilled=AsyncMock(
                side_effect=[
                    {"postings": [first], "has_next": False},
                    {
                        "postings": [{**first, "status": "awaiting_deliver"}],
                        "has_next": False,
                    },
                ]
            ),
        )

        await poller._poll_fbs(client, unfulfilled=True)
        await poller._poll_fbs(client, unfulfilled=True)

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(
            telegram.send_to_account.await_args.kwargs,
            {"topic": "sales"},
        )
        self.assertIn(
            "НОВЫЙ ЗАКАЗ FBS — СОБРАТЬ",
            telegram.send_to_account.await_args.args[1],
        )
        self.assertEqual(
            database.get_value("fbs-announced:demo-store:12345-0001-1"),
            "sent",
        )

    async def test_first_seen_fbs_order_already_awaiting_delivery_is_silent(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(
            bootstrap_send_existing=False,
            bootstrap_lookback_hours=72,
        )
        database = FakeDatabase()
        database.set_value("bootstrap:test:fbs_unfulfilled", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(
            account=account,
            fbs_unfulfilled=AsyncMock(
                return_value={
                    "postings": [
                        {
                            "posting_number": "fbs-late-1",
                            "status": "awaiting_deliver",
                            "integration_type_flow": "ozon",
                        }
                    ],
                    "has_next": False,
                }
            ),
        )

        await poller._poll_fbs(client, unfulfilled=True)

        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(
            database.get_value("fbs-announced:test:fbs-late-1"),
            "seen",
        )

    async def test_rfbs_return_is_sent_only_when_seller_actions_are_available(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value(
            "bootstrap:test:rfbs_return_actions_v1",
            "complete",
        )
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        base_return = {
            "return_id": 7000001,
            "posting_number": "9000765432-0002-1",
            "state": {"group_state": "new"},
            "product": {"name": "Экшн-камера"},
        }
        client = SimpleNamespace(
            account=account,
            rfbs_returns_list=AsyncMock(return_value={"returns": [base_return]}),
            rfbs_return_get=AsyncMock(
                return_value={
                    "returns": {
                        **base_return,
                        "return_reason": {"name": "Не подошёл"},
                        "available_actions": [
                            {"id": "VERIFY"},
                            {"id": "REJECT"},
                        ],
                    }
                }
            ),
        )

        await poller._poll_rfbs_returns(client)

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(
            telegram.send_to_account.await_args.kwargs,
            {"topic": "returns"},
        )
        self.assertIn(
            "ВОЗВРАТ rFBS — ПРИНЯТЬ РЕШЕНИЕ",
            telegram.send_to_account.await_args.args[1],
        )

    async def test_stale_rfbs_return_without_actions_is_not_sent(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value(
            "bootstrap:test:rfbs_return_actions_v1",
            "complete",
        )
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        base_return = {
            "return_id": 7000001,
            "state": {"group_state": "new"},
        }
        client = SimpleNamespace(
            account=account,
            rfbs_returns_list=AsyncMock(return_value={"returns": [base_return]}),
            rfbs_return_get=AsyncMock(
                return_value={"returns": {**base_return, "available_actions": []}}
            ),
        )

        await poller._poll_rfbs_returns(client)

        telegram.send_to_account.assert_not_awaited()
        self.assertTrue(database.events)

    async def test_rfbs_return_pagination_reaches_actionable_second_page(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value(
            "bootstrap:test:rfbs_return_actions_v1",
            "complete",
        )
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        actionable = {
            "return_id": 2,
            "state": {"group_state": "new"},
        }
        client = SimpleNamespace(
            account=account,
            rfbs_returns_list=AsyncMock(
                side_effect=[
                    {
                        "returns": [
                            {
                                "return_id": 1,
                                "state": {"group_state": "closed"},
                            }
                        ],
                        "last_id": "next",
                    },
                    {"returns": [actionable]},
                ]
            ),
            rfbs_return_get=AsyncMock(
                return_value={
                    "returns": {
                        **actionable,
                        "available_actions": [{"id": "VERIFY"}],
                    }
                }
            ),
        )

        await poller._poll_rfbs_returns(client)

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(client.rfbs_returns_list.await_count, 2)

    async def test_return_giveout_pagination_reaches_ready_second_page(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value(
            "bootstrap:test:return_giveout_actions_v1",
            "complete",
        )
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(
            account=account,
            return_giveout_list=AsyncMock(
                side_effect=[
                    {
                        "giveouts": [
                            {
                                "giveout_id": 1,
                                "giveout_status": "GIVEOUT_STATUS_COMPLETED",
                            }
                        ],
                        "last_id": "next",
                    },
                    {
                        "giveouts": [
                            {
                                "giveout_id": 2,
                                "giveout_status": "GIVEOUT_STATUS_APPROVED",
                            }
                        ]
                    },
                ]
            ),
        )

        await poller._poll_return_giveouts(client)

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(client.return_giveout_list.await_count, 2)

    async def test_removal_pagination_reaches_ready_second_page(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value("bootstrap:test:removal_actions_v1", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(
            account=account,
            removal_from_stock_list=AsyncMock(
                side_effect=[
                    {
                        "returns_summary_report_rows": [
                            {
                                "return_id": 1,
                                "return_state": "Собирается на складе",
                            }
                        ],
                        "last_id": "next",
                    },
                    {
                        "returns_summary_report_rows": [
                            {
                                "return_id": 2,
                                "box_state": "Доступно к вывозу",
                                "utilization_date": "2099-08-16T00:00:00Z",
                            }
                        ]
                    },
                ]
            ),
        )

        await poller._poll_removals(client)

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(client.removal_from_stock_list.await_count, 2)

    async def test_last_id_pagination_can_derive_cursor_from_full_page(self):
        poller = Poller(
            SimpleNamespace(),
            FakeDatabase(),
            SimpleNamespace(send_to_account=AsyncMock()),
        )
        call = AsyncMock(
            side_effect=[
                {"returns": [{"return_id": 10}, {"return_id": 11}]},
                {"returns": [{"return_id": 12}]},
            ]
        )

        items = await poller._collect_last_id_pages(
            call,
            ["returns"],
            ["return_id"],
            source="test returns",
            limit=2,
        )

        self.assertEqual([item["return_id"] for item in items], [10, 11, 12])
        self.assertEqual(
            call.await_args_list[1].kwargs,
            {"last_id": 11, "limit": 2},
        )

    async def test_repeated_terminal_last_id_finishes_pagination(self):
        poller = Poller(
            SimpleNamespace(),
            FakeDatabase(),
            SimpleNamespace(send_to_account=AsyncMock()),
        )
        call = AsyncMock(
            side_effect=[
                {"items": [{"id": 1}], "last_id": "terminal"},
                {"items": [], "last_id": "terminal"},
            ]
        )

        items = await poller._collect_last_id_pages(
            call,
            ["items"],
            ["id"],
            source="test items",
        )

        self.assertEqual(items, [{"id": 1}])
        self.assertEqual(call.await_count, 2)

    async def test_supply_poll_sends_acceptance_and_act_approval_per_supply(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value("bootstrap:test:supply_actions_v1", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(
            account=account,
            supply_order_list=AsyncMock(
                side_effect=[
                    {"order_ids": [101], "last_id": "next"},
                    {"order_ids": [], "last_id": ""},
                ]
            ),
            supply_order_get=AsyncMock(
                return_value={
                    "orders": [
                        {
                            "order_id": 101,
                            "state": "REPORTS_CONFIRMATION",
                            "state_updated_date": "2026-08-11T09:15:00Z",
                            "drop_off_warehouse": {"name": "Хаб"},
                            "supplies": [
                                {
                                    "supply_id": 201,
                                    "supply_state": "COMPLETED",
                                }
                            ],
                        }
                    ]
                }
            ),
        )

        await poller._poll_supply_orders(client)

        self.assertEqual(telegram.send_to_account.await_count, 2)
        self.assertEqual(
            [call.kwargs for call in telegram.send_to_account.await_args_list],
            [{"topic": "supplies"}, {"topic": "supplies"}],
        )
        texts = [call.args[1] for call in telegram.send_to_account.await_args_list]
        self.assertTrue(any("ПОСТАВКА FBO ПРИНЯТА" in text for text in texts))
        self.assertTrue(any("АКТ ПРИЁМКИ FBO" in text for text in texts))

    async def test_only_ready_removal_is_sent(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value("bootstrap:test:removal_from_stock", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(account=account)
        items = [
            {
                "return_id": 1,
                "return_state": "Собирается на складе",
                "stock_type": "Брак, доступный к вывозу со стока",
            },
            {
                "return_id": 2,
                "box_state": "Доступно к вывозу",
                "stock_type": "Брак, доступный к вывозу со стока",
                "name": "Экшн-камера",
                "quantity_for_return": 1,
                "destination_warehouse_name": "МОСКВА_7606",
                "utilization_date": "2099-08-16T00:00:00Z",
            },
            {
                "return_id": 3,
                "box_state": "Утилизировано",
                "utilization_date": "2026-08-10T00:00:00Z",
            },
            {
                "return_id": 4,
                "box_state": "Доступно к вывозу",
                "utilization_date": "2020-01-01T00:00:00Z",
            },
        ]

        await poller._poll_generic(
            client,
            "removal_from_stock",
            "Вывоз со стока",
            AsyncMock(return_value={"returns_summary_report_rows": items}),
            ["returns_summary_report_rows"],
        )

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(
            telegram.send_to_account.await_args.kwargs,
            {"topic": "returns"},
        )
        self.assertIn(
            "ТОВАРЫ СО СТОКА — ЗАБРАТЬ",
            telegram.send_to_account.await_args.args[1],
        )
        self.assertEqual(len(database.events), 4)

    async def test_existing_rfbs_order_is_seeded_without_later_replay(self):
        account = AccountConfig("Retail West", "retail-west", "1", "key")
        settings = SimpleNamespace(
            bootstrap_send_existing=False,
            bootstrap_lookback_hours=72,
        )
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        posting = {
            "posting_number": "old-rfbs-1",
            "status": "awaiting_registration",
            "integration_type_flow": "aggregator",
            "products": [],
        }
        client = SimpleNamespace(
            account=account,
            fbs_unfulfilled=AsyncMock(
                side_effect=[
                    {"result": {"postings": [posting], "has_next": False}},
                    {
                        "result": {
                            "postings": [
                                {**posting, "status": "awaiting_deliver"}
                            ],
                            "has_next": False,
                        }
                    },
                ]
            ),
        )

        await poller._poll_fbs(client, unfulfilled=True)
        database.set_value("bootstrap:retail-west:fbs_unfulfilled", "complete")
        await poller._poll_fbs(client, unfulfilled=True)

        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(
            database.get_value("rfbs-announced:retail-west:old-rfbs-1"),
            "seeded",
        )

    async def test_dropoff_is_not_reported_as_courier_but_pickup_is(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value("bootstrap:test:carriage_delivery", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(account=account)
        items = [
            {
                "delivery_method_id": 10,
                "delivery_method_status": "active",
                "first_mile_type": "dropoff",
            },
            {
                "delivery_method_id": 11,
                "delivery_method_status": "active",
                "first_mile_type": "pickup",
                "timeslot_from": "2026-07-30T12:00:00Z",
            },
        ]

        await poller._poll_generic(
            client,
            "carriage_delivery",
            "Отгрузка / курьер / доставка",
            AsyncMock(return_value={"items": items}),
            ["items"],
        )

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(
            telegram.send_to_account.await_args.kwargs,
            {"topic": "logistics"},
        )
        self.assertEqual(len(database.events), 2)

    async def test_finance_items_are_sent_as_one_digest(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value("bootstrap:test:finance_accrual", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(account=account)
        items = [
            {
                "accrual_id": 1,
                "accrued_category": "Продажи",
                "total_amount": {"amount": "100", "currency": "RUB"},
            },
            {
                "accrual_id": 2,
                "accrued_category": "Логистика",
                "total_amount": {"amount": "-10", "currency": "RUB"},
            },
        ]

        await poller._poll_generic(
            client,
            "finance_accrual",
            "Финансовое начисление / операция",
            AsyncMock(return_value={"items": items}),
            ["items"],
        )

        telegram.send_to_account.assert_awaited_once()
        self.assertEqual(len(database.events), 2)

    async def test_failed_finance_digest_is_not_claimed(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(bootstrap_send_existing=False)
        database = FakeDatabase()
        database.set_value("bootstrap:test:finance_accrual", "complete")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=False))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(account=account)

        await poller._poll_generic(
            client,
            "finance_accrual",
            "Finance",
            AsyncMock(return_value={"items": [{"accrual_id": 1}]}),
            ["items"],
        )

        self.assertEqual(database.events, set())

    async def test_recent_webhook_state_suppresses_polling_duplicate(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(
            bootstrap_send_existing=False,
            bootstrap_lookback_hours=72,
        )
        database = FakeDatabase()
        database.set_value("bootstrap:test:fbs_unfulfilled", "complete")
        database.mark_notification("test:posting-status:123:awaiting_packaging")
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        poller._session = AsyncMock()
        client = SimpleNamespace(
            account=account,
            fbs_unfulfilled=AsyncMock(
                return_value={
                    "result": {
                        "postings": [
                            {
                                "posting_number": "123",
                                "status": "awaiting_packaging",
                                "products": [],
                            }
                        ],
                        "has_next": False,
                    },
                }
            ),
        )

        await poller._poll_fbs(client, unfulfilled=True)

        telegram.send_to_account.assert_not_awaited()
        self.assertEqual(len(database.events), 1)

    async def test_bootstrap_posting_marks_status_to_suppress_late_webhook(self):
        account = AccountConfig("Test", "test", "1", "key")
        settings = SimpleNamespace(
            bootstrap_send_existing=False,
            bootstrap_lookback_hours=72,
        )
        database = FakeDatabase()
        telegram = SimpleNamespace(send_to_account=AsyncMock(return_value=True))
        poller = Poller(settings, database, telegram)
        client = SimpleNamespace(
            account=account,
            fbs_unfulfilled=AsyncMock(
                return_value={
                    "result": {
                        "postings": [
                            {
                                "posting_number": "seeded-123",
                                "status": "awaiting_packaging",
                                "products": [],
                            }
                        ],
                        "has_next": False,
                    },
                }
            ),
        )

        await poller._poll_fbs(client, unfulfilled=True)

        telegram.send_to_account.assert_not_awaited()
        self.assertTrue(
            database.was_notification_recent(
                "test:posting-status:seeded-123:awaiting_packaging",
                600,
            )
        )


if __name__ == "__main__":
    unittest.main()
