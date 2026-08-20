import unittest

from ozon_notify.config import AccountConfig
from ozon_notify.formatters import (
    format_finance_digest,
    format_generic,
    format_news,
    format_mvideo_order,
    format_posting,
    format_question,
    format_rfbs_order,
    format_service_warning,
    format_webhook,
    generic_event_key,
)


class AppliedNotificationFormatterTests(unittest.TestCase):
    def setUp(self):
        self.account = AccountConfig("Demo Store", "demo-store", "1", "key")

    def test_order_notification_leads_with_action_and_moscow_deadline(self):
        text = format_posting(
            self.account,
            {
                "posting_number": "12345-0001-1",
                "status": "awaiting_packaging",
                "delivery_schema": "FBS",
                "shipment_date": "2026-07-14T15:00:00Z",
                "delivery_method": {"warehouse": "Основной склад"},
                "products": [
                    {"name": "Чехол", "offer_id": "CASE-1", "quantity": 2}
                ],
            },
            "fbs_unfulfilled",
        )

        self.assertIn("НОВЫЙ ЗАКАЗ — СОБРАТЬ", text)
        self.assertIn("14.07.2026, 18:00 МСК", text)
        self.assertIn("Чехол — 2 шт. (арт. CASE-1)", text)
        self.assertIn("Что сделать:", text)
        self.assertNotIn("Источник:", text)
        self.assertNotIn("awaiting_packaging", text)

    def test_rfbs_posting_is_labeled_for_managers(self):
        text = format_posting(
            self.account,
            {
                "posting_number": "rfbs-1",
                "status": "awaiting_packaging",
                "integration_type_flow": "hybrid",
                "products": [],
            },
            "fbs_unfulfilled",
        )

        self.assertIn("<b>Схема:</b> realFBS", text)

    def test_rfbs_new_order_is_an_early_actionable_pickup_notice(self):
        text = format_rfbs_order(
            self.account,
            {
                "order_number": "900012345",
                "posting_number": "9000123456-0001-1",
                "status": "awaiting_registration",
                "in_process_at": "2026-08-05T06:28:00Z",
                "shipment_date_without_delay": "2026-08-05T09:00:00Z",
                "products": [
                    {
                        "name": "Экшн-камера Model X",
                        "offer_id": "CAM-X",
                        "quantity": 1,
                    }
                ],
            },
        )

        self.assertIn("НОВЫЙ ЗАКАЗ realFBS", text)
        self.assertIn("900012345", text)
        self.assertIn("9000123456-0001-1", text)
        self.assertIn("05.08.2026, 09:28 МСК", text)
        self.assertIn("05.08.2026, 12:00 МСК", text)
        self.assertIn("Экшн-камера Model X — 1 шт. (арт. CAM-X)", text)
        self.assertIn("до приезда курьера", text)

    def test_webhook_state_change_is_translated_without_raw_payload(self):
        _, text = format_webhook(
            self.account,
            {
                "message_type": "TYPE_STATE_CHANGED",
                "posting_number": "12345-0001-1",
                "new_state": "awaiting_deliver",
                "changed_state_date": "2026-07-14T10:30:00Z",
                "seller_id": 123,
                "warehouse_id": 456,
            },
        )

        self.assertIn("ЗАКАЗ ГОТОВ К ОТГРУЗКЕ", text)
        self.assertIn("Нужно отгрузить", text)
        self.assertIn("Что сделать:", text)
        self.assertNotIn("TYPE_STATE_CHANGED", text)
        self.assertNotIn("seller_id", text)

    def test_courier_message_has_useful_heading_and_body(self):
        _, text = format_webhook(
            self.account,
            {
                "message_type": "TYPE_NEW_MESSAGE",
                "chat_type": "Seller_Notification_FBS",
                "data": [
                    "**Курьер приехал за заказом 123**\n"
                    "Передайте отправление в течение 15 минут."
                ],
            },
        )

        self.assertIn("КУРЬЕР ПРИЕХАЛ", text)
        self.assertIn("<b>Сообщение:</b>", text)
        self.assertIn("Передайте отправление в течение 15 минут.", text)
        self.assertNotIn("**", text)
        self.assertNotIn("НОВОЕ СООБЩЕНИЕ OZON", text)

    def test_buyer_message_is_named_and_escaped(self):
        _, text = format_webhook(
            self.account,
            {
                "message_type": "TYPE_NEW_MESSAGE",
                "chat_type": "Buyer_Seller",
                "data": ["Подойдёт ли <b>XL</b>?"],
            },
        )

        self.assertIn("СООБЩЕНИЕ ПОКУПАТЕЛЯ", text)
        self.assertIn("Подойдёт ли &lt;b&gt;XL&lt;/b&gt;?", text)

    def test_return_notification_extracts_useful_nested_fields(self):
        text = format_generic(
            self.account,
            "returns",
            "Возврат Ozon",
            {
                "id": 42,
                "posting_number": "12345-0001-1",
                "return_reason_name": "Не подошёл размер",
                "product": {
                    "name": "Футболка",
                    "offer_id": "TSHIRT-1",
                    "quantity": 1,
                },
                "target_place": {
                    "name": "ПВЗ Центральный",
                    "address": "ул. Ленина, 1",
                },
                "storage": {"arrived_moment": "2026-07-14T08:00:00Z"},
                "visual": {"status": "ready_for_pickup"},
            },
        )

        self.assertIn("ВОЗВРАТ — ПРОВЕРИТЬ", text)
        self.assertIn("Футболка", text)
        self.assertIn("Не подошёл размер", text)
        self.assertIn("ПВЗ Центральный", text)
        self.assertIn("Что сделать:", text)
        self.assertNotIn("ready_for_pickup", text)

    def test_rfbs_return_approval_names_the_two_manager_choices(self):
        text = format_generic(
            self.account,
            "rfbs_returns",
            "Возврат rFBS",
            {
                "return_id": 7000001,
                "posting_number": "9000765432-0002-1",
                "created_at": "2026-08-11T08:00:00Z",
                "product": {
                    "name": "Экшн-камера",
                    "offer_id": "CAM-1",
                },
                "return_reason": {"name": "Товар не подошёл"},
                "available_actions": [
                    {"id": "VERIFY"},
                    {"id": "REJECT"},
                ],
            },
        )

        self.assertIn("ВОЗВРАТ rFBS — ПРИНЯТЬ РЕШЕНИЕ", text)
        self.assertIn("Экшн-камера", text)
        self.assertIn("Товар не подошёл", text)
        self.assertIn("одобрите или отклоните", text)

    def test_rfbs_return_event_key_ignores_action_order(self):
        first = {
            "return_id": 7000001,
            "state": {"group_state": "new"},
            "available_actions": [{"id": "VERIFY"}, {"id": "REJECT"}],
        }
        reordered = {
            **first,
            "available_actions": [{"id": "REJECT"}, {"id": "VERIFY"}],
        }

        self.assertEqual(
            generic_event_key(self.account, "rfbs_returns", first),
            generic_event_key(self.account, "rfbs_returns", reordered),
        )

    def test_supply_acceptance_is_formatted_per_supply(self):
        text = format_generic(
            self.account,
            "supply_acceptance",
            "Приёмка FBO",
            {
                "order_id": 80000101,
                "supply_id": 2000000000101,
                "status": "COMPLETED",
                "state_updated_date": "2026-08-11T09:15:00Z",
                "warehouse_name": "МСК_КАВКАЗСКИЙ_2_ХАБ",
            },
        )

        self.assertIn("ПОСТАВКА FBO ПРИНЯТА", text)
        self.assertIn("2000000000101", text)
        self.assertIn("МСК_КАВКАЗСКИЙ_2_ХАБ", text)
        self.assertIn("проверьте количество", text)

    def test_supply_act_approval_has_clear_deadline(self):
        text = format_generic(
            self.account,
            "supply_act",
            "Акт FBO",
            {
                "order_id": 80000101,
                "supply_id": 2000000000101,
                "status": "REPORTS_CONFIRMATION",
                "state_updated_date": "2026-08-11T09:15:00Z",
            },
        )

        self.assertIn("АКТ ПРИЁМКИ FBO — СОГЛАСОВАТЬ", text)
        self.assertIn("7 календарных дней", text)
        self.assertIn("примите или отклоните", text)

    def test_return_act_message_is_condensed_into_dispute_card(self):
        _, text = format_webhook(
            self.account,
            {
                "message_type": "TYPE_NEW_MESSAGE",
                "chat_type": "Seller_Notification_Findoc",
                "data": [
                    "**Новый акт по возвратам. Проверьте, всё ли в порядке** "
                    "Сегодня вы получили возвраты в пункте выдачи — отправляем "
                    "акт приёма-передачи №1000000001 от 10.08.2026. "
                    "Проверить качество возвратов нужно в течение 5 календарных "
                    "дней. Если заметите подмену, можете открыть спор. "
                    + "Длинная инструкция. " * 100
                ],
            },
        )

        self.assertIn("ВОЗВРАТЫ ПОЛУЧЕНЫ — ПРОВЕРИТЬ", text)
        self.assertIn("1000000001", text)
        self.assertIn("5 календарных дней", text)
        self.assertIn("откройте спор", text.lower())
        self.assertNotIn("Длинная инструкция", text)

    def test_pickup_return_message_is_condensed(self):
        _, text = format_webhook(
            self.account,
            {
                "message_type": "TYPE_NEW_MESSAGE",
                "chat_type": "Seller_Notification_FBS",
                "data": [
                    "**У вас есть возвраты в точке выдачи — заберите их** "
                    "Новые возвраты по схеме FBS — **2 шт**. "
                    "Всего в точке выдачи вас ждёт возвратов — 4 шт. "
                    "Товары будут ждать вас 10 дней, затем их утилизируют."
                ],
            },
        )

        self.assertIn("ВОЗВРАТЫ В ПВЗ — ЗАБРАТЬ", text)
        self.assertIn("Новых:</b> 2", text)
        self.assertIn("Всего ожидает:</b> 4", text)
        self.assertIn("10 дней", text)

    def test_stock_disposal_message_keeps_application_and_deadline(self):
        _, text = format_webhook(
            self.account,
            {
                "message_type": "TYPE_NEW_MESSAGE",
                "chat_type": "Seller_Notification_FBO",
                "data": [
                    "**FBO: Заберите товары из пункта вывоза до 16.08.2026 19:01** "
                    "Товары приехали в пункт вывоза «МОСКВА_7606». "
                    "Номер заявки №30000001. Если не заберёте товары до "
                    "16.08.2026 19:01, отправим их на утилизацию."
                ],
            },
        )

        self.assertIn("ТОВАРЫ FBO — ЗАБРАТЬ ДО УТИЛИЗАЦИИ", text)
        self.assertIn("30000001", text)
        self.assertIn("МОСКВА_7606", text)
        self.assertIn("16.08.2026, 19:01 МСК", text)

    def test_buyer_complaint_message_highlights_dispute_deadline(self):
        _, text = format_webhook(
            self.account,
            {
                "message_type": "TYPE_NEW_MESSAGE",
                "chat_type": "Seller_Notification_Content",
                "data": [
                    "Покупатель пожаловался на товар SKU 123456. "
                    "Если не согласны, откройте спор до 15.08.2026 18:00."
                ],
            },
        )

        self.assertIn("ЖАЛОБА ПОКУПАТЕЛЯ — ПРОВЕРИТЬ", text)
        self.assertIn("SKU:</b> <code>123456</code>", text)
        self.assertIn("15.08.2026, 18:00 МСК", text)
        self.assertIn("откройте спор", text.lower())

    def test_return_approval_message_names_the_required_decision(self):
        _, text = format_webhook(
            self.account,
            {
                "message_type": "TYPE_NEW_MESSAGE",
                "chat_type": "Seller_Notification_FBS",
                "data": [
                    "Покупатель оформил возврат №771122. "
                    "До 16.08.2026 одобрите возврат или отклоните заявку."
                ],
            },
        )

        self.assertIn("ВОЗВРАТ — ПРИНЯТЬ РЕШЕНИЕ", text)
        self.assertIn("771122", text)
        self.assertIn("16.08.2026", text)
        self.assertIn("одобрите или отклоните", text.lower())

    def test_finance_digest_summarizes_operations(self):
        text = format_finance_digest(
            self.account,
            "finance_accrual",
            "Финансовое начисление / операция",
            [
                {
                    "accrued_category": "Продажи",
                    "date": "2026-07-13",
                    "total_amount": {"amount": "1250.50", "currency": "RUB"},
                },
                {
                    "accrued_category": "Логистика",
                    "date": "2026-07-14",
                    "total_amount": {"amount": "-250.50", "currency": "RUB"},
                },
            ],
        )

        self.assertIn("ФИНАНСЫ — СВОДКА", text)
        self.assertIn("Операций:</b> 2", text)
        self.assertIn("1 000 ₽", text)
        self.assertIn("Продажи", text)
        self.assertIn("Логистика", text)

    def test_finance_digest_orders_period_by_source_date(self):
        text = format_finance_digest(
            self.account,
            "finance_accrual",
            "Финансовое начисление / операция",
            [
                {"date": "2026-08-01", "total_amount": {"amount": "1"}},
                {"date": "2026-07-31", "total_amount": {"amount": "2"}},
            ],
        )

        self.assertIn("31.07.2026 — 01.08.2026", text)

    def test_service_warning_is_actionable_and_hides_api_details(self):
        text = format_service_warning(self.account, "fbs_unfulfilled")

        self.assertIn("Новые заказы FBS/rFBS", text)
        self.assertIn("могут прийти с задержкой", text)
        self.assertIn("Что сделать:", text)
        self.assertNotIn("HTTP", text)
        self.assertNotIn("endpoint", text.lower())

    def test_question_notification_contains_question_and_clear_action(self):
        text = format_question(
            self.account,
            {
                "id": "question-1",
                "sku": 900001,
                "text": "Подойдёт ли размер <b>XL</b>?",
                "author_name": "Покупатель",
                "published_at": "2026-07-20T17:30:00Z",
                "status": "NEW",
            },
        )

        self.assertIn("НОВЫЙ ВОПРОС ПОКУПАТЕЛЯ", text)
        self.assertIn("Кабинет:</b> Demo Store", text)
        self.assertIn("SKU:</b> <code>900001</code>", text)
        self.assertIn("Подойдёт ли размер &lt;b&gt;XL&lt;/b&gt;?", text)
        self.assertIn("20.07.2026, 20:30 МСК", text)
        self.assertIn("Что сделать:", text)
        self.assertNotIn("author_name", text)
        self.assertNotIn("Покупатель</b>", text)

    def test_news_has_source_link_and_action(self):
        text = format_news(
            "OzonSellerAPI/123",
            "<b>Меняются правила FBS</b><br>Обновите процесс сборки заказов.",
        )

        self.assertIn("ИЗМЕНЕНИЕ ПРАВИЛ OZON", text)
        self.assertIn("Что сделать:", text)
        self.assertIn("https://t.me/OzonSellerAPI/123", text)

    def test_technical_news_is_condensed_without_api_symbol_noise(self):
        text = format_news(
            "OzonSellerAPI/456",
            "22 июля 2026<br><br>"
            "/v3/posting/fbs/list: Добавили параметр&nbsp;"
            "filter.integration_type_flow в запрос метода.<br><br>"
            "/v4/posting/fbs/list: Добавили параметр&nbsp;"
            "filter.integration_type_flow в запрос метода.<br><br>"
            "/v1/analytics/stocks: С 17 августа 2026 года метод возвращает "
            "информацию об остатках в реальном времени.<br><br>"
            "Подробнее по ссылке\u200b",
        )

        self.assertIn("ТЕХНИЧЕСКОЕ ОБНОВЛЕНИЕ OZON", text)
        self.assertIn("17 августа 2026 года", text)
        self.assertNotIn("/v3/", text)
        self.assertNotIn("integration_type_flow", text)
        self.assertNotIn("Подробнее по ссылке", text)
        self.assertNotIn("\u200b", text)

    def test_mvideo_order_is_actionable_and_omits_customer_data(self):
        text = format_mvideo_order(
            self.account,
            {
                "reserveId": 1001,
                "status": "RESERVATION_CONFIRMED",
                "reserveCreatedAt": "2026-07-25T12:00:00Z",
                "materialNumber": "900000001",
                "supplierMaterialNumber": "TV-1",
                "materialQuantity": 2,
                "robject": "ROW9",
            },
            "fbs",
        )

        self.assertIn("НОВЫЙ ЗАКАЗ М.ВИДЕО — ДОБАВИТЬ В ПОСТАВКУ", text)
        self.assertIn("FBS", text)
        self.assertIn("900000001", text)
        self.assertIn("TV-1", text)
        self.assertIn("2 шт.", text)
        self.assertIn("ROW9", text)
        self.assertIn("25.07.2026, 15:00 МСК", text)
        self.assertIn("Что сделать:", text)

    def test_mvideo_carrier_arrival_is_explained(self):
        text = format_mvideo_order(
            self.account,
            {
                "reserveId": 1001,
                "status": "CARRIER_AGR_ARRIVED",
                "supplyId": "80000001",
                "reserveDeliveredAt": "2026-07-27T14:27:57Z",
            },
            "fbs",
        )

        self.assertIn("МАШИНА С ПОСТАВКОЙ ПРИБЫЛА", text)
        self.assertIn("80000001", text)
        self.assertIn("27.07.2026, 17:27 МСК", text)
        self.assertIn("Что сделать:", text)


if __name__ == "__main__":
    unittest.main()
