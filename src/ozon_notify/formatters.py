from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from zoneinfo import ZoneInfo

from .config import AccountConfig, MVideoConfig
from .routing import (
    actionable_message_kind,
    message_topic,
    ozon_message_text,
    posting_topic,
)
from .utils import (
    first_present,
    h,
    stable_hash,
    strip_markdown,
    strip_tags,
    truncate,
)


MOSCOW = ZoneInfo("Europe/Moscow")

STATUS_RU = {
    "awaiting_registration": "Нужно зарегистрировать",
    "acceptance_in_progress": "Идёт приёмка",
    "awaiting_approve": "Ожидает подтверждения",
    "awaiting_packaging": "Нужно собрать",
    "awaiting_deliver": "Нужно отгрузить",
    "arbitration": "Арбитраж",
    "client_arbitration": "Арбитраж покупателя",
    "delivering": "Передан в доставку",
    "delivered": "Доставлен",
    "cancelled": "Отменён",
    "ready_for_pickup": "Готов к получению",
    "returned_to_seller": "Возвращён продавцу",
}

POSTING_META = {
    "awaiting_registration": (
        "🟠",
        "ЗАКАЗ — ЗАРЕГИСТРИРОВАТЬ",
        "Зарегистрируйте отправление в Ozon Seller.",
    ),
    "awaiting_approve": (
        "🟠",
        "ЗАКАЗ — ПОДТВЕРДИТЬ",
        "Откройте заказ и подтвердите его в Ozon Seller.",
    ),
    "awaiting_packaging": (
        "🔴",
        "НОВЫЙ ЗАКАЗ — СОБРАТЬ",
        "Соберите товары и подготовьте отправление до указанного срока.",
    ),
    "awaiting_deliver": (
        "🟠",
        "ЗАКАЗ ГОТОВ К ОТГРУЗКЕ",
        "Подготовьте отправление и передайте его в доставку.",
    ),
    "arbitration": (
        "🔴",
        "ЗАКАЗ В АРБИТРАЖЕ",
        "Откройте заказ в Ozon Seller и проверьте причину арбитража.",
    ),
    "client_arbitration": (
        "🔴",
        "АРБИТРАЖ ПОКУПАТЕЛЯ",
        "Проверьте обращение покупателя и ответьте в Ozon Seller.",
    ),
    "delivering": (
        "🔵",
        "ЗАКАЗ ПЕРЕДАН В ДОСТАВКУ",
        "Действий не требуется. Контролируйте статус доставки.",
    ),
    "delivered": (
        "🟢",
        "ЗАКАЗ ДОСТАВЛЕН",
        "Действий не требуется.",
    ),
    "cancelled": (
        "⚫",
        "ЗАКАЗ ОТМЕНЁН",
        "Проверьте причину отмены и верните товар в доступный остаток.",
    ),
}

GENERIC_META = {
    "returns": (
        "🟡",
        "ВОЗВРАТ — ПРОВЕРИТЬ",
        "Проверьте причину, состояние товара и дальнейший маршрут возврата.",
    ),
    "rfbs_returns": (
        "🔴",
        "ВОЗВРАТ rFBS — ПРИНЯТЬ РЕШЕНИЕ",
        "Откройте заявку в Ozon Seller и одобрите или отклоните возврат.",
    ),
    "return_giveout": (
        "🟠",
        "ВОЗВРАТ ГОТОВ К ПОЛУЧЕНИЮ",
        "Проверьте место и срок хранения, затем заберите возврат.",
    ),
    "carriage_delivery": (
        "🟠",
        "КУРЬЕР / ОТГРУЗКА — ПРОВЕРИТЬ",
        "Проверьте время приезда курьера и подготовьте отправления.",
    ),
    "pickup_history": (
        "🔵",
        "ЗАБОР КУРЬЕРОМ — СТАТУС",
        "Сверьте факт забора и количество переданных отправлений.",
    ),
    "supply_order": (
        "🟡",
        "ПОСТАВКА — ПРОВЕРИТЬ ПРИЁМКУ",
        "Откройте поставку в Ozon Seller и проверьте статус и расхождения.",
    ),
    "supply_acceptance": (
        "🟠",
        "ПОСТАВКА FBO ПРИНЯТА",
        "Откройте поставку и проверьте количество принятых товаров и расхождения.",
    ),
    "supply_act": (
        "🔴",
        "АКТ ПРИЁМКИ FBO — СОГЛАСОВАТЬ",
        "В течение 7 календарных дней примите или отклоните акт в Ozon Seller.",
    ),
    "removal_from_stock": (
        "🔴",
        "ТОВАРЫ СО СТОКА — ЗАБРАТЬ",
        "Оформите или завершите вывоз до указанной даты, чтобы товары не утилизировали.",
    ),
    "finance_decompensation": (
        "🔴",
        "УДЕРЖАНИЕ / ШТРАФ — ПРОВЕРИТЬ",
        "Проверьте основание и при необходимости подготовьте обращение в поддержку.",
    ),
    "finance_accrual": (
        "🔵",
        "ФИНАНСОВАЯ ОПЕРАЦИЯ",
        "Сверьте операцию с финансовым отчётом Ozon.",
    ),
}

SOURCE_RU = {
    "fbs_unfulfilled": "Новые заказы FBS/rFBS",
    "fbs_recent": "Изменения заказов FBS/rFBS",
    "returns": "Возвраты",
    "rfbs_returns": "Возвраты rFBS",
    "return_giveout": "Возвраты к получению",
    "carriage_delivery": "Курьеры и отгрузки",
    "pickup_history": "Заборы курьером",
    "supply_order": "Поставки и приёмка",
    "supply_acceptance": "Приёмка поставок FBO",
    "supply_act": "Акты приёмки FBO",
    "removal_from_stock": "Вывоз и утилизация FBO",
    "finance_decompensation": "Удержания и штрафы",
    "finance_accrual": "Финансовые операции",
    "questions": "Вопросы покупателей",
}

MVIDEO_STATUS_RU = {
    "RESERVATION_CONFIRMED": "Новый",
    "RESUPPLY_REQUIRED": "Требуется повторная поставка",
    "RESERVE_WAIT_APPROVE": "Ждёт подтверждения",
    "RESERVATION_WAITING_FOR_PICKING": "Ожидает сборки",
    "RESERVATION_BEING_PICKED": "На сборке",
    "RESERVATION_WAIT_DELIVERY": "Ожидает доставки",
    "CARRIER_AGR_ARRIVED": "Машина прибыла на склад",
    "ORDER_COMPLETE": "Принят на складе",
    "ORDER_NOT_COMPLETE": "Не доставлен или не принят",
    "RESERVATION_CANCELED": "Отменён",
}

MVIDEO_STATUS_META = {
    "RESERVATION_CONFIRMED": (
        "🔴",
        "НОВЫЙ ЗАКАЗ М.ВИДЕО — ДОБАВИТЬ В ПОСТАВКУ",
        "Откройте FBS-заказы в М.Видео, добавьте резерв в поставку и подготовьте товар.",
    ),
    "RESUPPLY_REQUIRED": (
        "🔴",
        "М.ВИДЕО — ТРЕБУЕТСЯ ПОВТОРНАЯ ПОСТАВКА",
        "Товар не приняли ранее. Создайте повторную поставку и проверьте причину непринятия.",
    ),
    "RESERVE_WAIT_APPROVE": (
        "🟡",
        "ЗАКАЗ М.ВИДЕО ЖДЁТ ПОДТВЕРЖДЕНИЯ",
        "Срочных действий не требуется. После подтверждения добавьте заказ в поставку.",
    ),
    "RESERVATION_WAITING_FOR_PICKING": (
        "🟠",
        "ЗАКАЗ М.ВИДЕО ОЖИДАЕТ СБОРКИ",
        "Откройте лист сборки, подготовьте товар и укажите грузовые места.",
    ),
    "RESERVATION_BEING_PICKED": (
        "🟠",
        "ЗАКАЗ М.ВИДЕО НА СБОРКЕ",
        "Завершите сборку, оформите доставку и подготовьте QR ТТН.",
    ),
    "RESERVATION_WAIT_DELIVERY": (
        "🟠",
        "ЗАКАЗ М.ВИДЕО ОЖИДАЕТ ДОСТАВКИ",
        "Передайте поставку водителю и контролируйте её прибытие на объект.",
    ),
    "CARRIER_AGR_ARRIVED": (
        "🔵",
        "МАШИНА С ПОСТАВКОЙ ПРИБЫЛА НА СКЛАД М.ВИДЕО",
        "Срочных действий не требуется. Дождитесь результата приёмки.",
    ),
    "ORDER_COMPLETE": (
        "🟢",
        "ЗАКАЗ ПРИНЯТ НА СКЛАДЕ М.ВИДЕО",
        "Срочных действий не требуется. Поставка успешно принята.",
    ),
    "ORDER_NOT_COMPLETE": (
        "🔴",
        "ЗАКАЗ М.ВИДЕО НЕ ПРИНЯТ — ПРОВЕРИТЬ",
        "Проверьте поставку и причину, по которой заказ не был доставлен или принят.",
    ),
    "RESERVATION_CANCELED": (
        "⚫",
        "ЗАКАЗ М.ВИДЕО ОТМЕНЁН — ПРОВЕРИТЬ",
        "Проверьте причину отмены и актуализируйте доступный остаток.",
    ),
}

MVIDEO_CANCEL_CAUSE_RU = {
    "BY_CLIENT": "покупателем",
    "BY_SUPPLIER": "поставщиком",
    "BY_TIME_LIMIT": "по истечении срока",
}


def human_status(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Статус не указан"
    return STATUS_RU.get(raw, raw.replace("_", " ").strip().capitalize())


def format_moscow_datetime(value: Any) -> str | None:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    try:
        if len(raw) == 10:
            return datetime.fromisoformat(raw).strftime("%d.%m.%Y")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(MOSCOW).strftime("%d.%m.%Y, %H:%M МСК")
    except ValueError:
        return raw


def _nested_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _event_data(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("data")
    if not isinstance(nested, dict):
        return payload
    return {**nested, **payload}


def _posting_meta(status: str) -> tuple[str, str, str]:
    return POSTING_META.get(
        status,
        (
            "🟡",
            "ИЗМЕНЕНИЕ ЗАКАЗА",
            "Откройте отправление в Ozon Seller и проверьте изменения.",
        ),
    )


def _amount(item: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    total = item.get("total_amount")
    raw_value: Any = None
    currency: str | None = None
    if isinstance(total, dict):
        raw_value = total.get("amount")
        currency = str(total.get("currency") or "") or None
    if raw_value is None:
        raw_value = first_present(item, ["amount", "total", "price", "sum", "value"])
        currency = str(item.get("currency") or item.get("currency_code") or "") or currency
    try:
        return (Decimal(str(raw_value)), currency) if raw_value is not None else (None, currency)
    except (InvalidOperation, ValueError):
        return None, currency


def _format_amount(value: Decimal, currency: str | None) -> str:
    if value == value.to_integral():
        number = f"{int(value):,}".replace(",", " ")
    else:
        number = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    symbol = {"RUB": "₽", "RUR": "₽"}.get((currency or "").upper(), currency or "")
    return f"{number} {symbol}".strip()


def _mvideo_meta(status: str) -> tuple[str, str, str]:
    return MVIDEO_STATUS_META.get(
        status,
        (
            "🟡",
            "ИЗМЕНЕНИЕ ЗАКАЗА М.ВИДЕО — ПРОВЕРИТЬ",
            "Откройте заказ в кабинете М.Видео и проверьте изменение.",
        ),
    )


def format_mvideo_order(
    account: AccountConfig | MVideoConfig,
    item: dict[str, Any],
    scheme: str,
) -> str:
    status = str(item.get("status") or "UNKNOWN").upper()
    emoji, title, action = _mvideo_meta(status)
    order_id = first_present(item, ["reserveId", "reserve_id"])
    material_number = item.get("materialNumber")
    supplier_material_number = item.get("supplierMaterialNumber")
    quantity = item.get("materialQuantity")
    supply_id = item.get("supplyId")
    delivery_object = item.get("robject")
    created_at = format_moscow_datetime(item.get("reserveCreatedAt"))
    delivered_at = format_moscow_datetime(item.get("reserveDeliveredAt"))
    cancel_cause = str(item.get("cancelCause") or "").upper()

    lines = [
        f"{emoji} <b>{title}</b>",
        "<b>Маркетплейс:</b> М.Видео",
        f"<b>Схема:</b> {h(scheme.upper())}",
    ]
    if account.name and account.name != "М.Видео":
        lines.append(f"<b>Кабинет:</b> {h(account.name)}")
    if order_id:
        lines.append(f"<b>Заказ:</b> <code>{h(order_id)}</code>")
    lines.append(
        f"<b>Статус:</b> {h(MVIDEO_STATUS_RU.get(status, status))}"
    )
    if material_number:
        lines.append(
            f"<b>Код товара М.Видео:</b> <code>{h(material_number)}</code>"
        )
    if supplier_material_number:
        lines.append(
            f"<b>Ваш артикул:</b> <code>{h(supplier_material_number)}</code>"
        )
    if quantity is not None:
        lines.append(f"<b>Количество:</b> {h(quantity)} шт.")
    if supply_id:
        lines.append(f"<b>Поставка:</b> <code>{h(supply_id)}</code>")
    if delivery_object:
        lines.append(f"<b>Объект:</b> <code>{h(delivery_object)}</code>")
    if created_at:
        lines.append(f"<b>Создан:</b> {h(created_at)}")
    if delivered_at:
        lines.append(f"<b>Прибыл на объект:</b> {h(delivered_at)}")
    if cancel_cause:
        cause = MVIDEO_CANCEL_CAUSE_RU.get(cancel_cause, cancel_cause)
        lines.append(f"<b>Причина отмены:</b> {h(cause)}")
    lines.extend(["", f"<b>Что сделать:</b> {h(action)}"])
    return "\n".join(lines)


def format_mvideo_service_warning(
    account: AccountConfig | MVideoConfig,
) -> str:
    lines = [
        "🟠 <b>ПРОВЕРКА М.ВИДЕО ЗАДЕРЖИВАЕТСЯ</b>",
        "<b>Маркетплейс:</b> М.Видео",
    ]
    if account.name and account.name != "М.Видео":
        lines.append(f"<b>Кабинет:</b> {h(account.name)}")
    lines.extend(
        [
            "",
            "<b>Что это значит:</b> новые уведомления по заказам М.Видео "
            "могут прийти с задержкой. Уведомления Ozon продолжают работать.",
            "",
            "<b>Что сделать:</b> передайте сообщение ответственному за интеграцию.",
        ]
    )
    return "\n".join(lines)


def _product(item: dict[str, Any]) -> dict[str, Any]:
    product = item.get("product")
    if isinstance(product, dict):
        return product
    products = item.get("products")
    if isinstance(products, list) and products and isinstance(products[0], dict):
        return products[0]
    return {}


def posting_event_key(account: AccountConfig, posting: dict[str, Any], prefix: str = "posting") -> str:
    posting_number = str(first_present(posting, ["posting_number", "postingNumber", "number"]) or "")
    status = str(posting.get("status") or "")
    date = str(
        first_present(
            posting,
            ["shipment_date_without_delay", "shipment_date", "delivering_date", "in_process_at"],
        )
        or ""
    )
    products = posting.get("products") or []
    products_hash = stable_hash(products)[:12]
    return f"{account.slug}:{prefix}:{posting_number}:{status}:{date}:{products_hash}"


def posting_status_notification_key(
    account: AccountConfig,
    posting_number: Any,
    status: Any,
) -> str | None:
    number = str(posting_number or "").strip()
    normalized_status = str(status or "").strip()
    if not number or not normalized_status:
        return None
    return f"{account.slug}:posting-status:{number}:{normalized_status}"


def webhook_posting_status_notification_key(
    account: AccountConfig,
    payload: dict[str, Any],
) -> str | None:
    data = _event_data(payload)
    message_type = str(payload.get("message_type") or payload.get("type") or "")
    posting_number = first_present(data, ["posting_number", "postingNumber", "order_number"])
    status = first_present(data, ["new_state", "status", "state"])
    if message_type in {"TYPE_NEW_POSTING", "TYPE_ORDER_NEW", "TYPE_FBO_POSTING_NEW"}:
        status = status or "awaiting_packaging"
    elif message_type in {"TYPE_POSTING_CANCELLED", "TYPE_FBO_POSTING_CANCELLED"}:
        status = "cancelled"
    elif message_type not in {"TYPE_STATE_CHANGED", "TYPE_FBO_POSTING_STATE_CHANGED"}:
        return None
    return posting_status_notification_key(account, posting_number, status)


def format_posting(account: AccountConfig, posting: dict[str, Any], source: str = "polling") -> str:
    posting_number = first_present(posting, ["posting_number", "postingNumber", "number"]) or "без номера"
    status = str(posting.get("status") or "unknown")
    emoji, title, action = _posting_meta(status)
    delivery_method = _nested_dict(posting, "delivery_method")
    analytics = _nested_dict(posting, "analytics_data")
    route = posting_topic(posting)
    schema = (
        "realFBS"
        if route == "rfbs"
        else first_present(posting, ["delivery_schema", "schema"])
        or delivery_method.get("schema")
        or "FBS"
    )
    deadline = format_moscow_datetime(
        first_present(
            posting,
            ["shipment_date_without_delay", "shipment_date", "delivering_date", "in_process_at"],
        )
    )
    warehouse = delivery_method.get("warehouse") or analytics.get("warehouse")

    lines = [
        f"{emoji} <b>{title}</b>",
        f"<b>Кабинет:</b> {h(account.name)}",
        f"<b>Схема:</b> {h(schema)}",
        f"<b>Отправление:</b> <code>{h(posting_number)}</code>",
        f"<b>Статус:</b> {h(human_status(status))}",
    ]
    if deadline:
        lines.append(f"<b>Срок:</b> {h(deadline)}")
    if warehouse:
        lines.append(f"<b>Склад:</b> {h(warehouse)}")

    products = posting.get("products")
    if isinstance(products, list) and products:
        lines.extend(["", "<b>Товары:</b>"])
        for product in products[:8]:
            if not isinstance(product, dict):
                continue
            name = first_present(product, ["name", "offer_id", "sku", "product_id"]) or "Товар"
            quantity = product.get("quantity") or product.get("qty") or 1
            offer = product.get("offer_id")
            suffix = f" (арт. {offer})" if offer and str(offer) != str(name) else ""
            lines.append(f"• {h(name)} — {h(quantity)} шт.{h(suffix)}")
        if len(products) > 8:
            lines.append(f"• Ещё {len(products) - 8} поз.")

    lines.extend(["", f"<b>Что сделать:</b> {h(action)}"])
    return "\n".join(lines)


def _format_new_order(
    account: AccountConfig,
    posting: dict[str, Any],
    *,
    rfbs: bool,
) -> str:
    order_number = first_present(posting, ["order_number", "order_id"])
    posting_number = first_present(
        posting,
        ["posting_number", "postingNumber", "number"],
    )
    received_at = format_moscow_datetime(
        first_present(posting, ["in_process_at", "created_at"])
    )
    deadline = format_moscow_datetime(
        first_present(
            posting,
            [
                "shipment_date_without_delay",
                "shipment_date",
                "delivering_date",
            ],
        )
    )
    delivery_method = _nested_dict(posting, "delivery_method")
    analytics = _nested_dict(posting, "analytics_data")
    warehouse = delivery_method.get("warehouse") or analytics.get("warehouse")

    title = (
        "НОВЫЙ ЗАКАЗ realFBS — ПОДГОТОВИТЬ"
        if rfbs
        else "НОВЫЙ ЗАКАЗ FBS — СОБРАТЬ"
    )
    lines = ["🔴 <b>" + title + "</b>", f"<b>Кабинет:</b> {h(account.name)}"]
    if order_number:
        lines.append(f"<b>Заказ:</b> <code>{h(order_number)}</code>")
    if posting_number:
        lines.append(f"<b>Отправление:</b> <code>{h(posting_number)}</code>")
    if received_at:
        lines.append(f"<b>Поступил:</b> {h(received_at)}")
    if deadline:
        lines.append(f"<b>Подготовить до:</b> {h(deadline)}")
    if warehouse:
        lines.append(f"<b>Склад:</b> {h(warehouse)}")

    products = posting.get("products")
    if isinstance(products, list) and products:
        lines.extend(["", "<b>Товары:</b>"])
        for product in products[:8]:
            if not isinstance(product, dict):
                continue
            name = first_present(
                product,
                ["name", "offer_id", "sku", "product_id"],
            ) or "Товар"
            quantity = product.get("quantity") or product.get("qty") or 1
            offer = product.get("offer_id")
            suffix = (
                f" (арт. {offer})"
                if offer and str(offer) != str(name)
                else ""
            )
            lines.append(f"• {h(name)} — {h(quantity)} шт.{h(suffix)}")
        if len(products) > 8:
            lines.append(f"• Ещё {len(products) - 8} поз.")

    action = (
        "Соберите товары и подготовьте отправление до приезда курьера."
        if rfbs
        else "Соберите товары и подготовьте отправление к отгрузке до указанного срока."
    )
    lines.extend(["", f"<b>Что сделать:</b> {action}"])
    return "\n".join(lines)


def format_rfbs_order(account: AccountConfig, posting: dict[str, Any]) -> str:
    return _format_new_order(account, posting, rfbs=True)


def format_fbs_order(account: AccountConfig, posting: dict[str, Any]) -> str:
    return _format_new_order(account, posting, rfbs=False)


def _message_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.I | re.S)
    return match.group(1).strip() if match else None


def _message_deadline(text: str) -> str | None:
    match = re.search(
        r"\bдо\s+(\d{2}\.\d{2}\.\d{4})(?:[\s,]+(\d{1,2}:\d{2}))?",
        text,
        flags=re.I,
    )
    if not match:
        return None
    if match.group(2):
        return f"{match.group(1)}, {match.group(2)} МСК"
    return match.group(1)


def _format_actionable_message(
    account: AccountConfig,
    payload: dict[str, Any],
    kind: str,
) -> str:
    text = strip_markdown(ozon_message_text(payload))
    lines: list[str]

    if kind == "return_approval":
        return_number = _message_match(text, r"(?:№|No\.?)\s*(\d+)")
        deadline = _message_deadline(text)
        lines = [
            "🔴 <b>ВОЗВРАТ — ПРИНЯТЬ РЕШЕНИЕ</b>",
            f"<b>Кабинет:</b> {h(account.name)}",
        ]
        if return_number:
            lines.append(f"<b>Заявка:</b> <code>{h(return_number)}</code>")
        if deadline:
            lines.append(f"<b>Решить до:</b> {h(deadline)}")
        lines.extend(
            [
                "",
                "<b>Что сделать:</b> Откройте заявку на возврат в "
                "Ozon Seller и одобрите или отклоните её.",
            ]
        )
        return "\n".join(lines)

    if kind == "return_dispute":
        act_number = _message_match(text, r"(?:№|No\.?)\s*(\d+)")
        act_date = _message_match(text, r"(?:№|No\.?)\s*\d+\s+от\s+(\d{2}\.\d{2}\.\d{4})")
        lines = [
            "🔴 <b>ВОЗВРАТЫ ПОЛУЧЕНЫ — ПРОВЕРИТЬ</b>",
            f"<b>Кабинет:</b> {h(account.name)}",
        ]
        if act_number:
            lines.append(f"<b>Акт:</b> <code>{h(act_number)}</code>")
        if act_date:
            lines.append(f"<b>Получены:</b> {h(act_date)}")
        lines.extend(
            [
                "<b>Срок проверки:</b> 5 календарных дней с получения",
                "",
                "<b>Что сделать:</b> Сверьте качество и комплектность. "
                "При подмене, повреждении или недостаче откройте спор в Ozon Seller.",
            ]
        )
        return "\n".join(lines)

    if kind == "return_pickup":
        new_count = _message_match(
            text,
            r"нов\w*\s+возврат\w*.*?(\d+)\s*шт",
        )
        total_count = _message_match(
            text,
            r"всего.*?жд[её]т\s+возврат\w*\s*[—-]?\s*(\d+)\s*шт",
        )
        lines = [
            "🔴 <b>ВОЗВРАТЫ В ПВЗ — ЗАБРАТЬ</b>",
            f"<b>Кабинет:</b> {h(account.name)}",
        ]
        if new_count:
            lines.append(f"<b>Новых:</b> {h(new_count)} шт.")
        if total_count:
            lines.append(f"<b>Всего ожидает:</b> {h(total_count)} шт.")
        lines.extend(
            [
                "<b>Срок хранения:</b> 10 дней с поступления",
                "",
                "<b>Что сделать:</b> Откройте Ozon Seller → Возвраты и отмены "
                "→ В пункте выдачи и заберите товары до утилизации.",
            ]
        )
        return "\n".join(lines)

    if kind == "stock_removal":
        request_number = _message_match(
            text,
            r"(?:номер\s+заявки\s*)?(?:№|No\.?)\s*(\d+)",
        )
        point = _message_match(
            text,
            r"пункт(?:а|е)?\s+вывоза\s+[«\"]([^»\"]+)",
        )
        deadline = _message_deadline(text)
        lines = [
            "🔴 <b>ТОВАРЫ FBO — ЗАБРАТЬ ДО УТИЛИЗАЦИИ</b>",
            f"<b>Кабинет:</b> {h(account.name)}",
        ]
        if request_number:
            lines.append(f"<b>Заявка:</b> <code>{h(request_number)}</code>")
        if point:
            lines.append(f"<b>Пункт вывоза:</b> {h(point)}")
        if deadline:
            lines.append(f"<b>Забрать до:</b> {h(deadline)}")
        lines.extend(
            [
                "",
                "<b>Что сделать:</b> Получите товары в пункте вывоза до срока, "
                "иначе Ozon отправит их на платную утилизацию.",
            ]
        )
        return "\n".join(lines)

    if kind == "supply_act":
        supply_number = _message_match(
            text,
            r"поставк\w*\s*(?:№|No\.?)?\s*(\d+)",
        )
        lines = [
            "🔴 <b>АКТ ПРИЁМКИ FBO — СОГЛАСОВАТЬ</b>",
            f"<b>Кабинет:</b> {h(account.name)}",
        ]
        if supply_number:
            lines.append(f"<b>Поставка:</b> <code>{h(supply_number)}</code>")
        lines.extend(
            [
                "<b>Срок:</b> 7 календарных дней после размещения акта",
                "",
                "<b>Что сделать:</b> Откройте FBO → Заявки на поставку → "
                "Подтверждение актов и примите либо отклоните акт приёмки.",
            ]
        )
        return "\n".join(lines)

    if kind == "supply_acceptance":
        supply_number = _message_match(
            text,
            r"поставк\w*\s*(?:№|No\.?)?\s*(\d+)",
        )
        lines = [
            "🟠 <b>ПОСТАВКА FBO ПРИНЯТА</b>",
            f"<b>Кабинет:</b> {h(account.name)}",
        ]
        if supply_number:
            lines.append(f"<b>Поставка:</b> <code>{h(supply_number)}</code>")
        lines.extend(
            [
                "",
                "<b>Что сделать:</b> Проверьте количество принятых товаров и "
                "расхождения по поставке в Ozon Seller.",
            ]
        )
        return "\n".join(lines)

    sku = _message_match(text, r"\bSKU\s*(\d+)")
    deadline = _message_deadline(text)
    lines = [
        "🔴 <b>ЖАЛОБА ПОКУПАТЕЛЯ — ПРОВЕРИТЬ</b>",
        f"<b>Кабинет:</b> {h(account.name)}",
    ]
    if sku:
        lines.append(f"<b>SKU:</b> <code>{h(sku)}</code>")
    if deadline:
        lines.append(f"<b>Открыть спор до:</b> {h(deadline)}")
    lines.extend(
        [
            "",
            "<b>Что сделать:</b> Откройте жалобу в Ozon Seller и, если не "
            "согласны, откройте спор до указанного срока.",
        ]
    )
    return "\n".join(lines)


def format_webhook(account: AccountConfig, payload: dict[str, Any]) -> tuple[str, str]:
    data = _event_data(payload)
    message_type = str(payload.get("message_type") or payload.get("type") or "UNKNOWN")
    posting_number = first_present(data, ["posting_number", "postingNumber", "order_number"])
    status = str(first_present(data, ["new_state", "status", "state"]) or "")
    event_key = (
        f"{account.slug}:webhook:{message_type}:{posting_number}:{stable_hash(payload)[:12]}"
        if posting_number
        else f"{account.slug}:webhook:{message_type}:{stable_hash(payload)}"
    )
    kind = actionable_message_kind(payload)
    if message_type in {"TYPE_NEW_MESSAGE", "TYPE_UPDATE_MESSAGE"} and kind:
        return event_key, _format_actionable_message(account, payload, kind)

    if message_type in {"TYPE_NEW_POSTING", "TYPE_ORDER_NEW", "TYPE_FBO_POSTING_NEW"}:
        emoji, title, action = POSTING_META["awaiting_packaging"]
        status = status or "awaiting_packaging"
    elif message_type in {"TYPE_POSTING_CANCELLED", "TYPE_FBO_POSTING_CANCELLED"}:
        emoji, title, action = POSTING_META["cancelled"]
        status = "cancelled"
    elif message_type in {"TYPE_STATE_CHANGED", "TYPE_FBO_POSTING_STATE_CHANGED"} and status:
        emoji, title, action = _posting_meta(status)
    elif message_type in {"TYPE_CUTOFF_DATE_CHANGED", "TYPE_DELIVERY_DATE_CHANGED", "TYPE_FBO_POSTING_DELIVERY_DATE_CHANGED"}:
        emoji, title, action = (
            "🟠",
            "ИЗМЕНИЛСЯ СРОК ЗАКАЗА",
            "Проверьте новый срок и скорректируйте сборку или отгрузку.",
        )
    elif message_type in {"TYPE_NEW_MESSAGE", "TYPE_UPDATE_MESSAGE"}:
        topic = message_topic(payload)
        chat_type = str(payload.get("chat_type") or "").lower()
        if topic == "logistics":
            emoji, title, action = (
                "🔴",
                "КУРЬЕР ПРИЕХАЛ",
                "Подготовьте и передайте курьеру указанные отправления без задержки.",
            )
        elif topic == "returns":
            emoji, title, action = (
                "🟠",
                "ВОЗВРАТЫ OZON — ПРОВЕРИТЬ",
                "Проверьте место, срок хранения и порядок получения возвратов.",
            )
        elif topic == "finance":
            emoji, title, action = (
                "🟠",
                "ФИНАНСОВОЕ УВЕДОМЛЕНИЕ OZON",
                "Откройте финансовые документы Ozon и проверьте уведомление.",
            )
        elif topic == "news":
            emoji, title, action = (
                "📣",
                "ВАЖНОЕ ИЗМЕНЕНИЕ OZON",
                "Проверьте, как изменение влияет на работу кабинета.",
            )
        elif chat_type == "buyer_seller":
            emoji, title, action = (
                "💬",
                (
                    "СООБЩЕНИЕ ПОКУПАТЕЛЯ ОБНОВЛЕНО"
                    if message_type == "TYPE_UPDATE_MESSAGE"
                    else "СООБЩЕНИЕ ПОКУПАТЕЛЯ"
                ),
                "Откройте чат с покупателем в Ozon Seller и ответьте.",
            )
        elif chat_type == "seller_support":
            emoji, title, action = (
                "💬",
                (
                    "ОТВЕТ ПОДДЕРЖКИ OZON ОБНОВЛЁН"
                    if message_type == "TYPE_UPDATE_MESSAGE"
                    else "ОТВЕТ ПОДДЕРЖКИ OZON"
                ),
                "Откройте обращение в Ozon Seller и проверьте ответ.",
            )
        else:
            emoji, title, action = (
                "💬",
                "УВЕДОМЛЕНИЕ OZON",
                "Откройте Ozon Seller и проверьте сообщение.",
            )
    elif message_type in {"TYPE_STOCKS_CHANGED", "TYPE_FBO_STOCKS_CHANGED"}:
        emoji, title, action = (
            "🟡",
            "ИЗМЕНИЛИСЬ ОСТАТКИ",
            "Проверьте остатки и доступность товаров в Ozon Seller.",
        )
    elif message_type in {"TYPE_DESCRIPTION_CATEGORY_TREE_CHANGED", "TYPE_CREATE_OR_UPDATE_ITEM"}:
        emoji, title, action = (
            "🔵",
            "ИЗМЕНЕНИЕ ТОВАРОВ / КАТЕГОРИЙ",
            "Проверьте затронутые карточки и требования Ozon.",
        )
    else:
        emoji, title, action = (
            "🟡",
            "СОБЫТИЕ OZON — ПРОВЕРИТЬ",
            "Откройте кабинет Ozon Seller и проверьте последние изменения.",
        )

    changed_at = format_moscow_datetime(
        first_present(
            data,
            ["changed_state_date", "cutoff_date", "delivery_date", "updated_at", "created_at"],
        )
    )

    lines = [f"{emoji} <b>{title}</b>", f"<b>Кабинет:</b> {h(account.name)}"]
    if posting_number:
        lines.append(f"<b>Отправление:</b> <code>{h(posting_number)}</code>")
    if status:
        lines.append(f"<b>Статус:</b> {h(human_status(status))}")
    if changed_at:
        lines.append(f"<b>Время:</b> {h(changed_at)}")
    if message_type in {"TYPE_NEW_MESSAGE", "TYPE_UPDATE_MESSAGE"}:
        message = strip_markdown(ozon_message_text(payload))
        if message:
            lines.extend(
                [
                    "",
                    "<b>Сообщение:</b>",
                    h(truncate(message, 1600)),
                ]
            )
    lines.extend(["", f"<b>Что сделать:</b> {h(action)}"])
    return event_key, "\n".join(lines)


def _format_rfbs_return_approval(
    account: AccountConfig,
    item: dict[str, Any],
) -> str:
    product = _product(item)
    reason = _nested_dict(item, "return_reason")
    return_id = item.get("return_id")
    posting_number = item.get("posting_number")
    created_at = format_moscow_datetime(item.get("created_at"))
    lines = [
        "🔴 <b>ВОЗВРАТ rFBS — ПРИНЯТЬ РЕШЕНИЕ</b>",
        f"<b>Кабинет:</b> {h(account.name)}",
    ]
    if return_id:
        lines.append(f"<b>Заявка:</b> <code>{h(return_id)}</code>")
    if posting_number:
        lines.append(f"<b>Отправление:</b> <code>{h(posting_number)}</code>")
    if product:
        name = first_present(product, ["name", "offer_id", "sku"])
        offer_id = product.get("offer_id")
        if name:
            product_text = str(name)
            if offer_id and str(offer_id) != str(name):
                product_text += f" (арт. {offer_id})"
            lines.append(f"<b>Товар:</b> {h(product_text)}")
    if reason.get("name"):
        lines.append(f"<b>Причина:</b> {h(reason['name'])}")
    if created_at:
        lines.append(f"<b>Создана:</b> {h(created_at)}")
    lines.extend(
        [
            "",
            "<b>Что сделать:</b> Откройте заявку на возврат rFBS в "
            "Ozon Seller и одобрите или отклоните её.",
        ]
    )
    return "\n".join(lines)


def _format_supply_event(
    account: AccountConfig,
    source: str,
    item: dict[str, Any],
) -> str:
    is_act = source == "supply_act"
    title = (
        "🔴 <b>АКТ ПРИЁМКИ FBO — СОГЛАСОВАТЬ</b>"
        if is_act
        else "🟠 <b>ПОСТАВКА FBO ПРИНЯТА</b>"
    )
    lines = [title, f"<b>Кабинет:</b> {h(account.name)}"]
    if item.get("order_id"):
        lines.append(f"<b>Заявка:</b> <code>{h(item['order_id'])}</code>")
    if item.get("supply_id"):
        lines.append(f"<b>Поставка:</b> <code>{h(item['supply_id'])}</code>")
    if item.get("warehouse_name"):
        lines.append(f"<b>Склад:</b> {h(item['warehouse_name'])}")
    changed_at = format_moscow_datetime(item.get("state_updated_date"))
    if changed_at:
        lines.append(f"<b>Обновлено:</b> {h(changed_at)}")
    if is_act:
        lines.append("<b>Срок:</b> 7 календарных дней после размещения акта")
        action = (
            "Откройте FBO → Заявки на поставку → Подтверждение актов "
            "и примите или отклоните акт приёмки."
        )
    else:
        action = (
            "Откройте поставку и проверьте количество принятых товаров "
            "и расхождения."
        )
    lines.extend(["", f"<b>Что сделать:</b> {action}"])
    return "\n".join(lines)


def _format_stock_removal(
    account: AccountConfig,
    item: dict[str, Any],
) -> str:
    lines = [
        "🔴 <b>ТОВАРЫ СО СТОКА — ЗАБРАТЬ</b>",
        f"<b>Кабинет:</b> {h(account.name)}",
    ]
    if item.get("return_id"):
        lines.append(f"<b>Заявка:</b> <code>{h(item['return_id'])}</code>")
    name = first_present(item, ["name", "offer_id", "sku"])
    if name:
        product_text = str(name)
        if item.get("quantity_for_return"):
            product_text += f" — {item['quantity_for_return']} шт."
        lines.append(f"<b>Товар:</b> {h(product_text)}")
    if item.get("destination_warehouse_name"):
        lines.append(
            f"<b>Пункт вывоза:</b> {h(item['destination_warehouse_name'])}"
        )
    deadline = format_moscow_datetime(item.get("utilization_date"))
    if deadline:
        lines.append(f"<b>Утилизация:</b> {h(deadline)}")
    lines.extend(
        [
            "",
            "<b>Что сделать:</b> Завершите вывоз до указанной даты, "
            "иначе Ozon утилизирует товары.",
        ]
    )
    return "\n".join(lines)


def generic_event_key(account: AccountConfig, source: str, item: dict[str, Any]) -> str:
    candidates = [
        "id",
        "return_id",
        "posting_number",
        "postingNumber",
        "giveout_id",
        "supply_id",
        "supply_order_id",
        "order_id",
        "carriage_id",
        "delivery_method_id",
        "operation_id",
        "transaction_id",
    ]
    identity = first_present(item, candidates)
    nested_state = _nested_dict(item, "state")
    visual = _nested_dict(item, "visual")
    visual_status = visual.get("status")
    if isinstance(visual_status, dict):
        visual_status = first_present(
            visual_status,
            ["sys_name", "display_name", "id"],
        )
    status = first_present(
        item,
        [
            "event_type",
            "status",
            "supply_state",
            "giveout_status",
            "box_state",
            "return_state",
            "delivery_method_status",
            "operation_type",
            "type",
            "accrual_id",
        ],
    ) or first_present(
        nested_state,
        ["state", "group_state", "state_name"],
    ) or visual_status or ""
    if identity is None:
        identity = stable_hash(item)
    actions = item.get("available_actions")
    action_ids: list[str] = []
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, dict):
                value = first_present(action, ["id", "name", "action"])
            else:
                value = action
            if value not in (None, ""):
                action_ids.append(str(value))
    actions_suffix = stable_hash(sorted(action_ids))[:10] if action_ids else ""
    deadline = item.get("utilization_date") or ""
    return (
        f"{account.slug}:{source}:{identity}:{status}:"
        f"{actions_suffix}:{deadline}"
    )


def format_generic(account: AccountConfig, source: str, title: str, item: dict[str, Any]) -> str:
    if source == "rfbs_returns":
        return _format_rfbs_return_approval(account, item)
    if source in {"supply_acceptance", "supply_act"}:
        return _format_supply_event(account, source, item)
    if source == "removal_from_stock":
        return _format_stock_removal(account, item)

    emoji, heading, action = GENERIC_META.get(
        source,
        ("🟡", title.upper(), "Откройте Ozon Seller и проверьте событие."),
    )
    identity = first_present(
        item,
        [
            "posting_number",
            "return_number",
            "return_id",
            "giveout_id",
            "supply_order_id",
            "order_id",
            "carriage_id",
            "delivery_method_id",
            "operation_id",
            "id",
        ],
    )
    state = _nested_dict(item, "state")
    visual = _nested_dict(item, "visual")
    status = (
        first_present(state, ["state_name", "group_state", "money_return_state_name", "state"])
        or first_present(visual, ["status"])
        or first_present(
            item,
            [
                "status",
                "delivery_method_status",
                "operation_type",
                "type",
                "accrued_category",
            ],
        )
    )
    product = _product(item)
    product_name = first_present(product, ["name", "offer_id", "sku"])
    product_offer = product.get("offer_id")
    product_quantity = product.get("quantity")
    return_reason = _nested_dict(item, "return_reason")
    reason = first_present(
        item,
        ["return_reason_name", "reason", "comment", "description"],
    ) or return_reason.get("name")
    place = _nested_dict(item, "target_place") or _nested_dict(item, "place")
    place_text = " — ".join(
        str(value)
        for value in [
            place.get("name") or item.get("warehouse_name"),
            place.get("address") or item.get("dropoff_address"),
        ]
        if value
    )
    storage = _nested_dict(item, "storage")
    logistic = _nested_dict(item, "logistic")
    date = format_moscow_datetime(
        first_present(
            item,
            [
                "created_at",
                "updated_at",
                "date",
                "operation_date",
                "processed_at",
                "timeslot_from",
                "recommended_time_local",
                "departure_date",
            ],
        )
        or first_present(storage, ["arrived_moment", "utilization_forecast_date"])
        or first_present(logistic, ["return_date", "final_moment"])
    )
    amount, currency = _amount(item)

    lines = [f"{emoji} <b>{heading}</b>", f"<b>Кабинет:</b> {h(account.name)}"]
    if identity is not None:
        lines.append(f"<b>Номер:</b> <code>{h(identity)}</code>")
    if status:
        lines.append(f"<b>Статус:</b> {h(human_status(status))}")
    if product_name:
        product_text = str(product_name)
        if product_quantity:
            product_text += f" — {product_quantity} шт."
        if product_offer and str(product_offer) != str(product_name):
            product_text += f" (арт. {product_offer})"
        lines.append(f"<b>Товар:</b> {h(product_text)}")
    if reason:
        lines.append(f"<b>Причина:</b> {h(reason)}")
    if place_text:
        lines.append(f"<b>Где:</b> {h(place_text)}")
    if amount is not None:
        lines.append(f"<b>Сумма:</b> {h(_format_amount(amount, currency))}")
    if date:
        lines.append(f"<b>Дата:</b> {h(date)}")
    lines.extend(["", f"<b>Что сделать:</b> {h(action)}"])
    return "\n".join(lines)


def format_question(account: AccountConfig, question: dict[str, Any]) -> str:
    sku = first_present(question, ["sku", "product_id"])
    text = first_present(question, ["text", "question", "content"])
    published_at = format_moscow_datetime(
        first_present(question, ["published_at", "created_at", "updated_at"])
    )

    lines = [
        "💬 <b>НОВЫЙ ВОПРОС ПОКУПАТЕЛЯ</b>",
        f"<b>Кабинет:</b> {h(account.name)}",
    ]
    if sku is not None:
        lines.append(f"<b>SKU:</b> <code>{h(sku)}</code>")
    if text:
        lines.extend(["", f"<b>Вопрос:</b> {h(truncate(str(text), 1200))}"])
    if published_at:
        lines.append(f"<b>Время:</b> {h(published_at)}")
    lines.extend(
        [
            "",
            "<b>Что сделать:</b> Откройте Ozon Seller → "
            "Отзывы и вопросы → Вопросы и ответьте покупателю.",
        ]
    )
    return "\n".join(lines)


def format_finance_digest(
    account: AccountConfig,
    source: str,
    title: str,
    items: list[dict[str, Any]],
) -> str:
    is_penalty = source == "finance_decompensation"
    heading = "УДЕРЖАНИЯ / ШТРАФЫ — СВОДКА" if is_penalty else "ФИНАНСЫ — СВОДКА"
    emoji = "🔴" if is_penalty else "🔵"
    action = (
        "Проверьте основания удержаний и при необходимости подготовьте обращение."
        if is_penalty
        else "Сверьте итог с финансовым отчётом Ozon."
    )
    amounts: list[Decimal] = []
    currencies: set[str] = set()
    categories: Counter[str] = Counter()
    dates: list[tuple[str, str]] = []
    for item in items:
        value, currency = _amount(item)
        if value is not None:
            amounts.append(value)
            if currency:
                currencies.add(currency.upper())
        category = first_present(
            item,
            ["accrued_category", "operation_type_name", "operation_type", "reason", "type"],
        )
        if category:
            categories[human_status(category)] += 1
        raw_date = first_present(item, ["date", "operation_date", "created_at", "processed_at"])
        if raw_date:
            formatted = format_moscow_datetime(raw_date)
            if formatted:
                dates.append((str(raw_date), formatted))

    lines = [
        f"{emoji} <b>{heading}</b>",
        f"<b>Кабинет:</b> {h(account.name)}",
        f"<b>Операций:</b> {len(items)}",
    ]
    if amounts and len(currencies) <= 1:
        currency = next(iter(currencies), None)
        lines.append(f"<b>Итого:</b> {h(_format_amount(sum(amounts, Decimal(0)), currency))}")
    if dates:
        ordered_dates = sorted(dates, key=lambda value: value[0])
        lines.append(
            f"<b>Период:</b> {h(ordered_dates[0][1])} — {h(ordered_dates[-1][1])}"
        )
    if categories:
        lines.extend(["", "<b>По категориям:</b>"])
        for category, count in categories.most_common(5):
            lines.append(f"• {h(category)} — {count}")
    lines.extend(["", f"<b>Что сделать:</b> {h(action)}"])
    return "\n".join(lines)


def format_service_warning(account: AccountConfig, source: str) -> str:
    section = SOURCE_RU.get(source, "Один из разделов кабинета")
    return (
        "🟠 <b>ПРОВЕРКА ДАННЫХ OZON ЗАДЕРЖИВАЕТСЯ</b>\n"
        f"<b>Кабинет:</b> {h(account.name)}\n"
        f"<b>Раздел:</b> {h(section)}\n\n"
        "<b>Что это значит:</b> несколько проверок подряд не получили данные; "
        "новые уведомления из этого раздела могут прийти с задержкой. "
        "Остальные проверки продолжают работать.\n\n"
        "<b>Что сделать:</b> срочных действий не требуется — сервис повторит запрос автоматически. "
        "Если сообщение повторяется, передайте его ответственному за интеграцию."
    )


API_PATH_RE = re.compile(r"/v\d+(?:/[A-Za-z0-9_-]+)+")
TECH_FIELD_RE = re.compile(
    r"\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\b",
    flags=re.I,
)
NEWS_NOISE = (
    "подробнее по ссылке",
    "следите за обновлениями самостоятельно",
    "задавайте вопросы и делитесь обратной связью",
)
NEWS_HIGHLIGHT_MARKERS = (
    "обязател",
    "отключ",
    "устар",
    "перейд",
    "переход",
    "в реальном времени",
    "вступит в силу",
    "критичес",
    "срок",
)


def _clean_news_paragraphs(text: str) -> list[str]:
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n{2,}", strip_tags(text))
        if paragraph.strip()
    ]
    return [
        paragraph
        for paragraph in paragraphs
        if not any(noise in paragraph.lower() for noise in NEWS_NOISE)
    ]


def _humanize_technical_news(paragraph: str) -> str:
    text = re.sub(
        r"^(?:(?:/v\d+(?:/[A-Za-z0-9_-]+)+)(?:\s*[;,]\s*)?)+:\s*",
        "",
        paragraph,
    )
    text = API_PATH_RE.sub("новую версию метода", text)
    text = TECH_FIELD_RE.sub("техническое поле", text)
    text = re.sub(
        r"(?:новую версию метода)(?:\s*[,;]\s*новую версию метода)+",
        "новые версии методов",
        text,
    )
    return re.sub(r"\s{2,}", " ", text).strip(" ;")


def _news_summary(text: str) -> tuple[str, str]:
    paragraphs = _clean_news_paragraphs(text)
    technical = sum(len(API_PATH_RE.findall(item)) for item in paragraphs) >= 2
    if technical:
        highlights = [
            _humanize_technical_news(paragraph)
            for paragraph in paragraphs
            if any(marker in paragraph.lower() for marker in NEWS_HIGHLIGHT_MARKERS)
        ]
        unique_highlights = list(dict.fromkeys(item for item in highlights if item))
        summary = "\n\n".join(unique_highlights[:3])
        if not summary:
            summary = (
                "Ozon обновил Seller API. Технические подробности доступны "
                "в оригинальной публикации."
            )
        return "ТЕХНИЧЕСКОЕ ОБНОВЛЕНИЕ OZON", summary

    summary = "\n\n".join(paragraphs[:3])
    return "ИЗМЕНЕНИЕ ПРАВИЛ OZON", summary


def format_news(post_id: str, text: str) -> str:
    clean = strip_tags(text)
    lower = clean.lower()
    if any(word in lower for word in ("fbs", "rfbs", "сборк", "отгруз")):
        action = "Проверьте, меняются ли сроки или процесс сборки и отгрузки заказов."
    elif any(word in lower for word in ("штраф", "тариф", "комисс", "удержан")):
        action = "Проверьте новые условия и оцените влияние на расходы кабинета."
    elif "api" in lower:
        action = "Передайте изменение ответственному за интеграцию с Ozon."
    else:
        action = "Откройте публикацию и проверьте, затрагивает ли изменение ваш кабинет."
    heading, summary = _news_summary(text)
    link = f"https://t.me/{post_id.lstrip('/')}"
    return (
        f"📣 <b>{heading}</b>\n\n"
        f"<b>Кратко:</b> {h(truncate(summary, 1400))}\n\n"
        f"<b>Что сделать:</b> {h(action)}\n"
        f'<a href="{h(link)}">Открыть оригинал</a>'
    )
