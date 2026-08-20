from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import aiohttp

from .config import AccountConfig, Settings
from .database import Database
from .formatters import (
    format_fbs_order,
    format_finance_digest,
    format_generic,
    format_question,
    format_rfbs_order,
    generic_event_key,
    posting_event_key,
    posting_status_notification_key,
)
from .ozon import OzonAPIError, OzonClient
from .routing import (
    is_actionable_carriage,
    is_actionable_generic,
    posting_announcement_key,
    posting_route_key,
    posting_topic,
    should_announce_posting,
)
from .telegram import TelegramClient
from .utils import first_present, list_from_response

logger = logging.getLogger(__name__)

SOURCE_TOPICS = {
    "returns": "returns",
    "rfbs_returns": "returns",
    "return_giveout": "returns",
    "removal_from_stock": "returns",
    "carriage_delivery": "logistics",
    "pickup_history": "logistics",
    "supply_order": "supplies",
    "supply_acceptance": "supplies",
    "supply_act": "supplies",
    "finance_decompensation": "finance",
    "finance_accrual": "finance",
    "questions": "messages",
}


class Poller:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        telegram: TelegramClient,
        event_lock: asyncio.Lock | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.telegram = telegram
        self.event_lock = event_lock or asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45))
        tasks = [
            asyncio.create_task(self._loop("important", self.settings.important_poll_seconds, self.poll_important)),
            asyncio.create_task(self._loop("secondary", self.settings.secondary_poll_seconds, self.poll_secondary)),
        ]
        await asyncio.gather(*tasks)

    async def stop(self) -> None:
        self._stop.set()
        if self._session:
            await self._session.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        if not self._session:
            raise RuntimeError("Poller session is not started")
        return self._session

    async def _loop(self, name: str, interval: int, fn: Callable[[], Awaitable[None]]) -> None:
        logger.info("Starting %s poller every %ss", name, interval)
        while not self._stop.is_set():
            try:
                await fn()
            except Exception:
                logger.exception("%s poll failed", name)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def poll_important(self) -> None:
        for account in self.settings.accounts:
            client = OzonClient(self.settings, account, self.session)
            await self._safe_account_call(account, "fbs_unfulfilled", self._poll_fbs_unfulfilled(client))
            await self._safe_account_call(account, "fbs_recent", self._poll_fbs_recent(client))

    async def poll_secondary(self) -> None:
        for account in self.settings.accounts:
            client = OzonClient(self.settings, account, self.session)
            jobs = [
                (
                    "rfbs_returns",
                    self._poll_rfbs_returns(client),
                ),
                (
                    "return_giveout",
                    self._poll_return_giveouts(client),
                ),
                (
                    "removal_from_stock",
                    self._poll_removals(client),
                ),
                (
                    "supply_order",
                    self._poll_supply_orders(client),
                ),
            ]
            for name, job in jobs:
                await self._safe_account_call(account, name, job)

    async def _poll_fbs_unfulfilled(self, client: OzonClient) -> None:
        await self._poll_fbs(client, unfulfilled=True)

    async def _poll_fbs_recent(self, client: OzonClient) -> None:
        await self._poll_fbs(client, unfulfilled=False)

    async def _poll_fbs(self, client: OzonClient, unfulfilled: bool) -> None:
        cursor: str | None = None
        source = "fbs_unfulfilled" if unfulfilled else "fbs_recent"
        bootstrap_marker = f"bootstrap:{client.account.slug}:{source}"
        deliver_existing = self.settings.bootstrap_send_existing or bool(
            self.db.get_value(bootstrap_marker)
        )
        while True:
            data = (
                await client.fbs_unfulfilled(
                    self.settings.bootstrap_lookback_hours,
                    cursor=cursor,
                )
                if unfulfilled
                else await client.fbs_recent(
                    self.settings.bootstrap_lookback_hours,
                    cursor=cursor,
                )
            )
            postings = list_from_response(data, ["postings"])
            for posting in postings:
                if not isinstance(posting, dict):
                    continue
                route = posting_topic(posting)
                posting_number = str(
                    first_present(
                        posting,
                        ["posting_number", "postingNumber", "number"],
                    )
                    or ""
                ).strip()
                if posting_number:
                    self.db.set_value(
                        posting_route_key(client.account.slug, posting_number),
                        route,
                    )
                key = posting_event_key(client.account, posting, source)
                canonical_key = posting_status_notification_key(
                    client.account,
                    first_present(posting, ["posting_number", "postingNumber", "number"]),
                    posting.get("status"),
                )
                announcement_key = (
                    posting_announcement_key(
                        client.account.slug,
                        posting_number,
                        route,
                    )
                    if posting_number
                    else None
                )
                async with self.event_lock:
                    if self.db.has_event(key):
                        continue
                    if canonical_key and self.db.was_notification_recent(
                        canonical_key,
                        cooldown_seconds=10 * 60,
                    ):
                        self.db.claim_event(key, source, client.account.slug, posting)
                        if announcement_key:
                            self.db.set_value(announcement_key, "seeded")
                        continue
                    if not deliver_existing:
                        self.db.claim_event(key, source, client.account.slug, posting)
                        if announcement_key:
                            self.db.set_value(announcement_key, "seeded")
                        if canonical_key:
                            self.db.mark_notification(canonical_key)
                        continue

                    if announcement_key:
                        announced = bool(self.db.get_value(announcement_key))
                        if not announced and should_announce_posting(posting, route):
                            text = (
                                format_rfbs_order(client.account, posting)
                                if route == "rfbs"
                                else format_fbs_order(client.account, posting)
                            )
                            sent = await self.telegram.send_to_account(
                                client.account,
                                text,
                                topic=route,
                            )
                            if sent:
                                self.db.set_value(announcement_key, "sent")
                                self.db.claim_event(
                                    key,
                                    source,
                                    client.account.slug,
                                    posting,
                                )
                                if canonical_key:
                                    self.db.mark_notification(canonical_key)
                            continue
                        if not announced:
                            self.db.set_value(announcement_key, "seen")
                    self.db.claim_event(
                        key,
                        source,
                        client.account.slug,
                        posting,
                    )
                    if canonical_key:
                        self.db.mark_notification(canonical_key)
            result = data.get("result", data) if isinstance(data, dict) else {}
            if not isinstance(result, dict) or not result.get("has_next"):
                break
            next_cursor = str(result.get("cursor") or "").strip()
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError(f"Ozon {source} returned an invalid cursor")
            cursor = next_cursor
        self.db.set_value(bootstrap_marker, "complete")

    async def _poll_rfbs_returns(self, client: OzonClient) -> None:
        items: list[dict[str, Any]] = []
        raw_items = await self._collect_last_id_pages(
            client.rfbs_returns_list,
            ["returns"],
            ["return_id", "id"],
            source="rFBS returns",
        )
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item = raw_item
            state = item.get("state")
            group_state = (
                str(state.get("group_state") or "").lower()
                if isinstance(state, dict)
                else ""
            )
            return_id = item.get("return_id")
            if group_state == "new" and return_id not in (None, ""):
                details = await client.rfbs_return_get(return_id)
                detailed_return = details.get("returns")
                if not isinstance(detailed_return, dict):
                    detailed_return = details.get("return")
                if isinstance(detailed_return, dict):
                    item = {**item, **detailed_return}
            items.append(item)

        bootstrap_marker = (
            f"bootstrap:{client.account.slug}:rfbs_return_actions_v1"
        )
        deliver_existing = self.settings.bootstrap_send_existing or bool(
            self.db.get_value(bootstrap_marker)
        )
        await self._process_generic_items(
            client,
            "rfbs_returns",
            "Возврат rFBS на согласовании",
            items,
            deliver_existing,
        )
        self.db.set_value(bootstrap_marker, "complete")

    async def _poll_return_giveouts(self, client: OzonClient) -> None:
        items = await self._collect_last_id_pages(
            client.return_giveout_list,
            ["giveouts", "returns"],
            ["giveout_id", "id"],
            source="return giveouts",
        )
        bootstrap_marker = (
            f"bootstrap:{client.account.slug}:return_giveout_actions_v1"
        )
        deliver_existing = self.settings.bootstrap_send_existing or bool(
            self.db.get_value(bootstrap_marker)
        )
        await self._process_generic_items(
            client,
            "return_giveout",
            "Возвратная отгрузка / ПВЗ",
            items,
            deliver_existing,
        )
        self.db.set_value(bootstrap_marker, "complete")

    async def _poll_removals(self, client: OzonClient) -> None:
        items = await self._collect_last_id_pages(
            client.removal_from_stock_list,
            ["returns_summary_report_rows"],
            ["return_id", "id"],
            source="removal from stock",
        )
        bootstrap_marker = f"bootstrap:{client.account.slug}:removal_actions_v1"
        deliver_existing = self.settings.bootstrap_send_existing or bool(
            self.db.get_value(bootstrap_marker)
        )
        await self._process_generic_items(
            client,
            "removal_from_stock",
            "Вывоз со стока",
            items,
            deliver_existing,
        )
        self.db.set_value(bootstrap_marker, "complete")

    async def _collect_last_id_pages(
        self,
        call: Callable[..., Awaitable[dict[str, Any]]],
        preferred_keys: list[str],
        id_fields: list[str],
        *,
        source: str,
        limit: int = 100,
        max_pages: int = 100,
    ) -> list[Any]:
        collected: list[Any] = []
        last_id: str | int | None = None

        for _ in range(max_pages):
            data = await call(last_id=last_id, limit=limit)
            page_items = list_from_response(data, preferred_keys)
            collected.extend(page_items)
            result = data.get("result") if isinstance(data, dict) else None
            roots = [data, result]
            explicit_next = next(
                (
                    root.get("last_id")
                    for root in roots
                    if isinstance(root, dict)
                    and root.get("last_id") not in (None, "", 0, "0")
                ),
                None,
            )
            has_next = any(
                bool(root.get("has_next"))
                for root in roots
                if isinstance(root, dict)
            )

            next_id = explicit_next
            if next_id is None and (has_next or len(page_items) >= limit):
                last_item = page_items[-1] if page_items else None
                if isinstance(last_item, dict):
                    next_id = first_present(last_item, id_fields)
            if next_id is None:
                return collected
            if next_id == last_id:
                if not has_next:
                    return collected
                raise RuntimeError(f"Ozon {source} returned an invalid last_id")
            last_id = next_id

        raise RuntimeError(f"Ozon {source} pagination safety limit reached")

    async def _poll_supply_orders(self, client: OzonClient) -> None:
        order_ids: list[str | int] = []
        last_id: str | int | None = None

        for _ in range(100):
            data = await client.supply_order_list(last_id=last_id)
            page_ids = list_from_response(data, ["order_ids"])
            order_ids.extend(
                order_id
                for order_id in page_ids
                if isinstance(order_id, (str, int))
            )
            next_id = data.get("last_id") if isinstance(data, dict) else None
            if not next_id or next_id == last_id or not page_ids:
                break
            last_id = next_id
        else:
            raise RuntimeError("Ozon supply pagination safety limit reached")

        acceptance_events: list[dict[str, Any]] = []
        act_events: list[dict[str, Any]] = []
        for start in range(0, len(order_ids), 50):
            data = await client.supply_order_get(order_ids[start : start + 50])
            orders = list_from_response(data, ["orders"])
            for order in orders:
                if not isinstance(order, dict):
                    continue
                order_state = str(order.get("state") or "").upper()
                warehouse = order.get("drop_off_warehouse")
                warehouse_name = (
                    warehouse.get("name")
                    if isinstance(warehouse, dict)
                    else warehouse
                )
                supplies = order.get("supplies")
                normalized_supplies = (
                    [supply for supply in supplies if isinstance(supply, dict)]
                    if isinstance(supplies, list)
                    else []
                )
                if not normalized_supplies:
                    normalized_supplies = [{}]

                act_required = (
                    "REPORT" in order_state
                    and any(
                        marker in order_state
                        for marker in ("CONFIRM", "APPROV", "AGREE")
                    )
                )
                for supply in normalized_supplies:
                    supply_state = str(
                        supply.get("supply_state")
                        or supply.get("state")
                        or ""
                    ).upper()
                    event = {
                        "order_id": order.get("order_id"),
                        "supply_id": supply.get("supply_id"),
                        "supply_state": supply_state,
                        "order_state": order_state,
                        "state_updated_date": order.get("state_updated_date"),
                        "warehouse_name": warehouse_name,
                    }
                    if supply_state == "COMPLETED":
                        acceptance_events.append(event)
                    if act_required:
                        act_events.append(event)

        bootstrap_marker = f"bootstrap:{client.account.slug}:supply_actions_v1"
        deliver_existing = self.settings.bootstrap_send_existing or bool(
            self.db.get_value(bootstrap_marker)
        )
        await self._process_generic_items(
            client,
            "supply_acceptance",
            "Приёмка поставки FBO",
            acceptance_events,
            deliver_existing,
        )
        await self._process_generic_items(
            client,
            "supply_act",
            "Акт приёмки FBO",
            act_events,
            deliver_existing,
        )
        self.db.set_value(bootstrap_marker, "complete")

    async def _poll_generic(
        self,
        client: OzonClient,
        source: str,
        title: str,
        call: Callable[[], Awaitable[dict[str, Any]]],
        preferred_keys: list[str],
    ) -> None:
        data = await call()
        items = list_from_response(data, preferred_keys)
        bootstrap_marker = f"bootstrap:{client.account.slug}:{source}"
        deliver_existing = self.settings.bootstrap_send_existing or bool(
            self.db.get_value(bootstrap_marker)
        )
        await self._process_generic_items(
            client,
            source,
            title,
            items,
            deliver_existing,
        )
        self.db.set_value(bootstrap_marker, "complete")

    async def _process_generic_items(
        self,
        client: OzonClient,
        source: str,
        title: str,
        items: list[Any],
        deliver_existing: bool,
    ) -> None:
        pending: list[tuple[str, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                item = {"id": item}
            key = generic_event_key(client.account, source, item)
            if self.db.has_event(key):
                continue
            if source == "carriage_delivery" and not is_actionable_carriage(item):
                self.db.claim_event(key, source, client.account.slug, item)
                continue
            if not is_actionable_generic(source, item):
                self.db.claim_event(key, source, client.account.slug, item)
                continue
            if not deliver_existing:
                self.db.claim_event(key, source, client.account.slug, item)
                continue
            pending.append((key, item))

        if source in {"finance_accrual", "finance_decompensation"} and pending:
            sent = await self.telegram.send_to_account(
                client.account,
                format_finance_digest(
                    client.account,
                    source,
                    title,
                    [item for _, item in pending],
                ),
                topic=SOURCE_TOPICS.get(source, "system"),
            )
            if sent:
                for key, item in pending:
                    self.db.claim_event(key, source, client.account.slug, item)
        else:
            for key, item in pending:
                text = (
                    format_question(client.account, item)
                    if source == "questions"
                    else format_generic(client.account, source, title, item)
                )
                sent = await self.telegram.send_to_account(
                    client.account,
                    text,
                    topic=SOURCE_TOPICS.get(source, "system"),
                )
                if not sent:
                    continue
                self.db.claim_event(key, source, client.account.slug, item)

    async def _safe_account_call(self, account: AccountConfig, source: str, awaitable: Awaitable[None]) -> None:
        try:
            await awaitable
            self.db.set_value(f"failure-streak:{account.slug}:{source}", "0")
        except OzonAPIError as exc:
            self._record_failure(account, source)
            logger.warning("Ozon API warning account=%s source=%s error=%s", account.slug, source, exc)
        except Exception:
            self._record_failure(account, source)
            logger.exception("Poll call failed account=%s source=%s", account.slug, source)

    def _record_failure(self, account: AccountConfig, source: str) -> int:
        key = f"failure-streak:{account.slug}:{source}"
        raw_value = self.db.get_value(key)
        try:
            count = int(raw_value or "0") + 1
        except ValueError:
            count = 1
        self.db.set_value(key, str(count))
        return count
