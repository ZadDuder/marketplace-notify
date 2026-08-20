from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import aiohttp

from .config import MVideoConfig, Settings
from .database import Database
from .formatters import format_mvideo_order
from .mvideo import MVideoAPIError, MVideoClient
from .telegram import TelegramClient
from .utils import first_present

logger = logging.getLogger(__name__)


class MVideoPoller:
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
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45)
        )
        logger.info(
            "Starting M.Video poller every %ss",
            self.settings.mvideo_poll_seconds,
        )
        while not self._stop.is_set():
            try:
                await self.poll()
            except Exception:
                logger.exception("M.Video poll failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.mvideo_poll_seconds,
                )
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._session:
            await self._session.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        if not self._session:
            raise RuntimeError("M.Video poller session is not started")
        return self._session

    async def poll(self) -> None:
        account = self.settings.mvideo_account
        if not account:
            return
        client = MVideoClient(self.settings, account, self.session)
        await self._safe_call(
            account,
            "fbs_new_reserves",
            self._poll_reserves(
                client,
                "fbs_new_reserves",
                lambda _: client.fbs_new_reserves(),
            ),
        )
        await self._safe_call(
            account,
            "fbs_reserves",
            self._poll_reserves(
                client,
                "fbs_reserves",
                lambda offset: client.fbs_reserves(
                    self.settings.mvideo_lookback_hours,
                    offset,
                    100,
                ),
                page_size=100,
            ),
        )

    async def _poll_reserves(
        self,
        client: Any,
        source: str,
        call: Callable[[int], Awaitable[dict[str, Any]]],
        page_size: int | None = None,
        max_pages: int = 100,
    ) -> None:
        bootstrap_marker = f"bootstrap:{client.account.slug}:{source}"
        deliver_existing = self.settings.bootstrap_send_existing or bool(
            self.db.get_value(bootstrap_marker)
        )
        offset = 0
        pages = 0

        while True:
            data = await call(offset)
            items = data.get("reserves", []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                await self._process_reserve(
                    client.account,
                    source,
                    item,
                    deliver_existing,
                )

            pages += 1
            if page_size is None or len(items) < page_size:
                break
            if pages >= max_pages:
                raise RuntimeError(
                    "M.Video reserves pagination safety limit reached"
                )
            offset += len(items)

        self.db.set_value(bootstrap_marker, "complete")

    async def _process_reserve(
        self,
        account: MVideoConfig,
        source: str,
        item: dict[str, Any],
        deliver_existing: bool,
    ) -> None:
        order_id = str(first_present(item, ["reserveId", "reserve_id"]) or "").strip()
        status = _reserve_status(item)
        if not order_id:
            return

        status_key = f"mvideo:fbs:{order_id}:status:{status}"
        stored_event = {
            "order_id": order_id,
            "status": status,
        }
        async with self.event_lock:
            has_status = self.db.has_event(status_key)
            if has_status:
                return

            if not deliver_existing:
                self.db.claim_event(
                    status_key,
                    source,
                    account.slug,
                    stored_event,
                )
                return

            normalized_item = {**item, "status": status}
            text = format_mvideo_order(account, normalized_item, "fbs")
            sent = await self.telegram.send_to_account(
                account,
                text,
                topic="sales",
            )
            if not sent:
                return
            self.db.claim_event(
                status_key,
                source,
                account.slug,
                stored_event,
            )

    async def _safe_call(
        self,
        account: MVideoConfig,
        source: str,
        awaitable: Awaitable[None],
    ) -> None:
        try:
            await awaitable
            self.db.set_value(f"failure-streak:{account.slug}:{source}", "0")
        except MVideoAPIError as exc:
            self._record_failure(account, source)
            logger.warning(
                "M.Video API warning account=%s source=%s status=%s path=%s",
                account.slug,
                source,
                exc.status,
                exc.path,
            )
        except Exception:
            self._record_failure(account, source)
            logger.exception(
                "M.Video poll call failed account=%s source=%s",
                account.slug,
                source,
            )

    def _record_failure(self, account: MVideoConfig, source: str) -> int:
        key = f"failure-streak:{account.slug}:{source}"
        try:
            count = int(self.db.get_value(key) or "0") + 1
        except ValueError:
            count = 1
        self.db.set_value(key, str(count))
        return count


def _reserve_status(item: dict[str, Any]) -> str:
    status = str(
        item.get("status") or "RESERVATION_CONFIRMED"
    ).strip().upper()
    if item.get("reSupply") and status == "RESERVATION_CONFIRMED":
        return "RESUPPLY_REQUIRED"
    return status
