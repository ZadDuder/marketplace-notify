from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import aiohttp

from .config import AccountConfig, Settings
from .utils import date_window, month_key, recent_dates, utc_window

logger = logging.getLogger(__name__)


class OzonAPIError(RuntimeError):
    def __init__(self, path: str, status: int, body: Any) -> None:
        super().__init__(f"Ozon API {path} failed with HTTP {status}")
        self.path = path
        self.status = status
        self.body = body


@dataclass
class OzonClient:
    settings: Settings
    account: AccountConfig
    session: aiohttp.ClientSession

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.settings.ozon_base_url.rstrip("/") + path
        headers = {
            "Client-Id": self.account.client_id,
            "Api-Key": self.account.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with self.session.post(url, json=payload or {}, headers=headers) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise OzonAPIError(path, resp.status, data)
            return data

    async def fbs_unfulfilled(
        self,
        hours_back: int,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        cutoff_from, cutoff_to = utc_window(hours_back)
        payload: dict[str, Any] = {
            "dir": "ASC",
            "sort_by": "created_at",
            "filter": {"cutoff_from": cutoff_from, "cutoff_to": cutoff_to},
            "limit": limit,
            "with": {
                "analytics_data": True,
                "barcodes": True,
                "financial_data": True,
            },
        }
        if cursor:
            payload["cursor"] = cursor
        return await self.post("/v4/posting/fbs/unfulfilled/list", payload)

    async def fbs_recent(
        self,
        hours_back: int,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        since, to = utc_window(hours_back)
        payload: dict[str, Any] = {
            "dir": "ASC",
            "filter": {"since": since, "to": to},
            "limit": limit,
            "with": {
                "analytics_data": True,
                "barcodes": True,
                "financial_data": True,
            },
        }
        if cursor:
            payload["cursor"] = cursor
        return await self.post("/v4/posting/fbs/list", payload)

    async def fbs_get(self, posting_number: str) -> dict[str, Any]:
        return await self.post(
            "/v3/posting/fbs/get",
            {
                "posting_number": posting_number,
                "with": {
                    "analytics_data": True,
                    "barcodes": True,
                    "financial_data": True,
                    "translit": False,
                },
            },
        )

    async def returns_list(self, last_id: str | int | None = None, limit: int = 100) -> dict[str, Any]:
        payload: dict[str, Any] = {"limit": limit, "filter": {}}
        if last_id not in (None, "", 0, "0"):
            payload["last_id"] = last_id
        return await self.post("/v1/returns/list", payload)

    async def rfbs_returns_list(self, last_id: str | int | None = None, limit: int = 100) -> dict[str, Any]:
        payload: dict[str, Any] = {"limit": limit}
        if last_id not in (None, "", 0, "0"):
            payload["last_id"] = last_id
        return await self.post("/v2/returns/rfbs/list", payload)

    async def rfbs_return_get(self, return_id: str | int) -> dict[str, Any]:
        return await self.post(
            "/v2/returns/rfbs/get",
            {"return_id": return_id},
        )

    async def return_giveout_list(self, last_id: str | int | None = None, limit: int = 100) -> dict[str, Any]:
        payload: dict[str, Any] = {"limit": limit}
        if last_id not in (None, "", 0, "0"):
            payload["last_id"] = last_id
        return await self.post("/v1/return/giveout/list", payload)

    async def carriage_delivery_list(self) -> dict[str, Any]:
        date_from, date_to = date_window(days_back=14)
        return await self.post(
            "/v2/carriage/delivery/list",
            {"date": {"from": date_from, "to": date_to}, "limit": 1000},
        )

    async def pickup_history(self) -> dict[str, Any]:
        date_from, date_to = date_window(days_back=14)
        return await self.post(
            "/v1/warehouse/fbs/pickup/history/list",
            {"date": {"from": date_from, "to": date_to}, "limit": 100},
        )

    async def supply_order_list(self, last_id: str | int | None = None, limit: int = 100) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "limit": min(limit, 100),
            "sort_by": 1,
            "sort_dir": "DESC",
            "filter": {"states": list(range(1, 11))},
        }
        if last_id not in (None, "", 0, "0"):
            payload["last_id"] = last_id
        return await self.post("/v3/supply-order/list", payload)

    async def supply_order_get(
        self,
        order_ids: list[str | int],
    ) -> dict[str, Any]:
        return await self.post(
            "/v3/supply-order/get",
            {"order_ids": order_ids},
        )

    async def removal_from_stock_list(
        self,
        last_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        date_from, date_to = date_window(days_back=45)
        payload: dict[str, Any] = {
            "date_from": date_from,
            "date_to": date_to,
            "limit": min(limit, 100),
        }
        if last_id:
            payload["last_id"] = last_id
        return await self.post("/v1/removal/from-stock/list", payload)

    async def finance_decompensation(self, page: int = 1, page_size: int = 1000) -> dict[str, Any]:
        try:
            return await self.post(
                "/v1/finance/decompensation",
                {"date": month_key(), "language": "RU"},
            )
        except OzonAPIError as exc:
            if exc.status == 404:
                return {"result": {"items": []}}
            raise

    async def finance_accrual_by_day(self) -> dict[str, Any]:
        accruals: list[dict[str, Any]] = []
        for date in recent_dates(days_back=7):
            data = await self.post("/v1/finance/accrual/by-day", {"date": date})
            if isinstance(data, dict) and isinstance(data.get("accruals"), list):
                accruals.extend(data["accruals"])
            elif isinstance(data, dict):
                result = data.get("result")
                if isinstance(result, dict) and isinstance(result.get("accruals"), list):
                    accruals.extend(result["accruals"])
        return {"accruals": accruals}

    async def question_list(self, limit: int = 100) -> dict[str, Any]:
        try:
            return await self.post(
                "/v1/question/list",
                {
                    "limit": min(limit, 100),
                    "sort_dir": "DESC",
                    "filter": {"status": "NEW"},
                },
            )
        except OzonAPIError as exc:
            body = exc.body if isinstance(exc.body, dict) else {}
            message = str(body.get("message") or "").lower()
            if exc.status == 403 and body.get("code") == 7 and "premium plus" in message:
                return {"questions": []}
            raise

    async def notification_set(self, url: str, push_types: list[str]) -> dict[str, Any]:
        return await self.post(
            "/v1/notification/set",
            {"url": url, "types": push_types},
        )

    async def notification_list(self) -> dict[str, Any]:
        return await self.post("/v1/notification/list", {})

    async def notification_update(
        self,
        notification_id: str | int,
        url: str,
    ) -> dict[str, Any]:
        return await self.post(
            "/v1/notification/update",
            {"id": notification_id, "url": url},
        )

    async def notification_check(self, notification_id: str | int) -> dict[str, Any]:
        return await self.post("/v1/notification/check", {"notification_id": notification_id})

    async def notification_push_type_list(self) -> dict[str, Any]:
        return await self.post("/v1/notification/push-type/list", {})
