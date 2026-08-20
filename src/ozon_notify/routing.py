from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from .utils import first_present, normalize_plain_text


RFBS_FLOWS = {
    "aggregator",
    "hybrid",
    "non_integrated",
    "non-integrated",
    "seller",
}

IGNORED_WEBHOOK_TYPES = {
    "TYPE_CHAT_CLOSED",
    "TYPE_FBO_POSTING_CANCELLED",
    "TYPE_FBO_POSTING_DELIVERY_DATE_CHANGED",
    "TYPE_FBO_POSTING_NEW",
    "TYPE_FBO_POSTING_STATE_CHANGED",
    "TYPE_MESSAGE_READ",
    "TYPE_ORDER_NEW",
}

RFBS_INITIAL_STATUSES = {
    "awaiting_registration",
    "awaiting_approve",
    "awaiting_packaging",
    "awaiting_deliver",
}

FBS_POSTING_WEBHOOK_TYPES = {
    "TYPE_CUTOFF_DATE_CHANGED",
    "TYPE_DELIVERY_DATE_CHANGED",
    "TYPE_NEW_POSTING",
    "TYPE_POSTING_CANCELLED",
    "TYPE_STATE_CHANGED",
}


def event_data(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("data")
    if isinstance(nested, dict):
        return {**nested, **payload}
    return payload


def webhook_posting_number(payload: dict[str, Any]) -> str | None:
    value = first_present(
        event_data(payload),
        ["posting_number", "postingNumber", "order_number"],
    )
    number = str(value or "").strip()
    return number or None


def posting_route_key(account_slug: str, posting_number: str) -> str:
    return f"posting-route:{account_slug}:{posting_number}"


def posting_announcement_key(
    account_slug: str,
    posting_number: str,
    topic: str,
) -> str:
    route = "rfbs" if topic == "rfbs" else "fbs"
    return f"{route}-announced:{account_slug}:{posting_number}"


def rfbs_announcement_key(account_slug: str, posting_number: str) -> str:
    return posting_announcement_key(account_slug, posting_number, "rfbs")


def should_announce_rfbs_posting(payload: dict[str, Any]) -> bool:
    return should_announce_posting(payload, "rfbs")


def should_announce_posting(
    payload: dict[str, Any],
    topic: str | None = None,
) -> bool:
    status = str(payload.get("status") or "").strip().lower()
    route = topic or posting_topic(payload)
    if route == "rfbs":
        return status in RFBS_INITIAL_STATUSES
    return status == "awaiting_packaging"


def posting_topic(
    payload: dict[str, Any],
    known_topic: str | None = None,
) -> str:
    data = event_data(payload)
    delivery_method = data.get("delivery_method")
    if not isinstance(delivery_method, dict):
        delivery_method = {}

    flow = str(
        first_present(data, ["integration_type_flow", "tpl_integration_type"])
        or first_present(
            delivery_method,
            ["integration_type_flow", "tpl_integration_type"],
        )
        or ""
    ).strip().lower()
    if flow in RFBS_FLOWS:
        return "rfbs"
    if flow == "ozon":
        return "sales"

    delivery_label = " ".join(
        str(value)
        for value in (
            delivery_method.get("name"),
            delivery_method.get("tpl_provider"),
            data.get("delivery_schema"),
            data.get("schema"),
        )
        if value
    ).lower()
    if any(marker in delivery_label for marker in ("rfbs", "real fbs", "рилфбс", "рил фбс")):
        return "rfbs"
    if known_topic in {"sales", "rfbs"}:
        return known_topic
    return "sales"


def ozon_message_text(payload: dict[str, Any]) -> str:
    raw = payload.get("data")
    parts: list[str] = []
    if isinstance(raw, list):
        parts = [str(item) for item in raw if item not in (None, "")]
    elif isinstance(raw, dict):
        value = first_present(raw, ["text", "message", "content", "body"])
        if value:
            parts = [str(value)]
    elif raw not in (None, ""):
        parts = [str(raw)]
    else:
        value = first_present(payload, ["text", "message", "content", "body"])
        if value:
            parts = [str(value)]
    return normalize_plain_text("\n".join(parts))


def actionable_message_kind(payload: dict[str, Any]) -> str | None:
    chat_type = str(payload.get("chat_type") or "").strip().lower()
    if "seller_notification" not in chat_type:
        return None

    text = ozon_message_text(payload).lower()
    if (
        "возврат" in text
        and any(
            marker in text
            for marker in (
                "одобрит",
                "отклонит",
                "принять решение",
                "примите решение",
            )
        )
    ):
        return "return_approval"
    if (
        "новый акт по возвратам" in text
        and "открыть спор" in text
    ):
        return "return_dispute"
    if (
        "возврат" in text
        and any(marker in text for marker in ("точке выдачи", "пункте выдачи", "пвз"))
        and any(marker in text for marker in ("заберите", "ждёт", "ждет"))
    ):
        return "return_pickup"
    if (
        "утилиз" in text
        and any(
            marker in text
            for marker in (
                "пункт вывоза",
                "вывезти товары",
                "заберите товары",
                "вывоз со стока",
            )
        )
    ):
        return "stock_removal"
    if (
        "постав" in text
        and any(
            marker in text
            for marker in (
                "акт приём",
                "акт прием",
                "согласование актов",
            )
        )
        and any(
            marker in text
            for marker in (
                "согласовать",
                "согласуйте",
                "примите или отклоните",
                "подтверждение актов",
            )
        )
    ):
        return "supply_act"
    if (
        "постав" in text
        and any(
            marker in text
            for marker in (
                "завершили приём",
                "завершили прием",
                "поставка принята",
                "результаты приём",
                "результаты прием",
            )
        )
    ):
        return "supply_acceptance"
    if (
        any(marker in text for marker in ("жалоб", "пожалов"))
        and ("покупател" in text or "на товар" in text)
        and any(marker in text for marker in ("спор", "оспор"))
        and re.search(r"\bдо\s+\d{2}\.\d{2}\.\d{4}\b", text)
    ):
        return "buyer_complaint"
    return None


def message_topic(payload: dict[str, Any]) -> str:
    chat_type = str(payload.get("chat_type") or "").strip().lower()
    text = ozon_message_text(payload).lower()
    kind = actionable_message_kind(payload)

    if kind in {
        "return_approval",
        "return_dispute",
        "return_pickup",
        "stock_removal",
    }:
        return "returns"
    if kind in {"supply_acceptance", "supply_act"}:
        return "supplies"
    if kind == "buyer_complaint":
        return "messages"

    if chat_type in {"buyer_seller", "seller_support"}:
        return "messages"
    if any(
        marker in chat_type
        for marker in ("notification_major", "api_updates", "api_notifications")
    ):
        return "news"
    if "findoc" in chat_type or any(
        marker in text
        for marker in (
            "акт сверки",
            "финансов",
            "штраф",
            "удержан",
            "комисси",
        )
    ):
        return "finance"
    if any(marker in text for marker in ("возврат", "заберите товар", "заберите их")):
        return "returns"
    if any(
        marker in text
        for marker in (
            "курьер приехал",
            "курьер уже приехал",
            "за заказом приехал",
            "водитель приехал",
            "машина приехала",
            "передать отправление",
            "забор курьером",
        )
    ):
        return "logistics"
    if "notification_fbs" in chat_type and any(
        marker in text for marker in ("rfbs", "real fbs", "рилфбс", "рил фбс")
    ):
        return "rfbs"
    if "notification" in chat_type:
        return "system"
    return "messages"


def webhook_topic(
    payload: dict[str, Any],
    known_posting_topic: str | None = None,
) -> str:
    message_type = str(
        payload.get("message_type") or payload.get("type") or ""
    ).upper()
    if "MESSAGE" in message_type or "CHAT" in message_type:
        return message_topic(payload)
    if message_type.startswith("TYPE_FBO_") and "POSTING" in message_type:
        return "sales"
    if message_type in FBS_POSTING_WEBHOOK_TYPES:
        return posting_topic(payload, known_posting_topic)
    if "ORDER" in message_type or "POSTING" in message_type:
        return "sales"
    return "system"


def should_notify_webhook(
    payload: dict[str, Any],
    known_posting_topic: str | None = None,
) -> bool:
    message_type = str(
        payload.get("message_type") or payload.get("type") or ""
    ).upper()
    if message_type not in {"TYPE_NEW_MESSAGE", "TYPE_UPDATE_MESSAGE"}:
        return False
    return actionable_message_kind(payload) is not None


def is_actionable_carriage(item: dict[str, Any]) -> bool:
    first_mile_type = str(item.get("first_mile_type") or "").strip().lower()
    return "pickup" in first_mile_type or "courier" in first_mile_type


def is_actionable_generic(source: str, item: dict[str, Any]) -> bool:
    if source == "rfbs_returns":
        actions = item.get("available_actions")
        return isinstance(actions, list) and bool(actions)
    if source == "return_giveout":
        status = str(item.get("giveout_status") or "").upper()
        return status in {"GIVEOUT_STATUS_CREATED", "GIVEOUT_STATUS_APPROVED"}
    if source == "removal_from_stock":
        state = " ".join(
            str(item.get(key) or "")
            for key in ("box_state", "return_state")
        ).lower()
        if any(
            marker in state
            for marker in (
                "заверш",
                "получен",
                "выдан",
                "утилиз",
                "отмен",
                "закрыт",
            )
        ):
            return False
        utilization_date = item.get("utilization_date")
        if utilization_date:
            try:
                deadline = datetime.fromisoformat(
                    str(utilization_date).replace("Z", "+00:00")
                )
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=UTC)
                if deadline <= datetime.now(UTC):
                    return False
            except ValueError:
                pass
            return True
        return any(
            marker in state
            for marker in ("доступ", "готов", "ожидает выдачи")
        )
    return True
