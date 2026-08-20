from __future__ import annotations

from typing import Any

import aiohttp

from .config import MVideoConfig, Settings
from .utils import utc_window


class MVideoAPIError(RuntimeError):
    def __init__(self, path: str, status: int, response: Any) -> None:
        self.path = path
        self.status = status
        self.response = response
        code = response.get("code") if isinstance(response, dict) else None
        message = response.get("message") if isinstance(response, dict) else None
        super().__init__(
            f"M.Video API {path} failed: status={status}, "
            f"code={code or 'unknown'}, message={message or 'unknown error'}"
        )


class MVideoClient:
    def __init__(
        self,
        settings: Settings,
        account: MVideoConfig,
        session: aiohttp.ClientSession,
    ) -> None:
        self.base_url = settings.mvideo_base_url.rstrip("/")
        self.account = account
        self.session = session

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self.session.get(
            f"{self.base_url}{path}",
            params=params,
            headers={
                "api-key": self.account.api_key,
                "Accept": "application/json",
            },
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400 or not isinstance(data, dict):
                raise MVideoAPIError(path, resp.status, data)
            return data

    async def fbs_new_reserves(self) -> dict[str, Any]:
        return await self.get("/v2/fbs/reserves/new")

    async def fbs_reserves(
        self,
        hours_back: int,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        since, now = utc_window(hours_back, hours_forward=0)
        return await self.get(
            "/v2/fbs/reserves",
            {
                "limit": limit,
                "next": offset,
                "dateFrom": since,
                "dateTo": now,
            },
        )
