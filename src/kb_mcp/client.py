"""Thin async client for the Knowledge Base REST API.

Every call carries the caller's own API key, so the API's own permission rules
decide what is visible. This module adds no authority of its own.
"""

from __future__ import annotations

from typing import Any

import httpx

from kb_mcp.config import settings


class KnowledgeBaseError(RuntimeError):
    """An API call failed in a way the caller should hear about verbatim."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or response.reason_phrase
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):  # pydantic validation errors
        return "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', [])[1:])}: {e.get('msg')}"
            for e in detail
        )[:400]
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)[:400]
    return str(detail or response.text)[:400]


class KnowledgeBase:
    """One caller's view of the Knowledge Base."""

    def __init__(self, token: str, base_url: str | None = None) -> None:
        self.token = token
        self.base_url = (base_url or settings.api_url).rstrip("/")

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=timeout or settings.KB_TIMEOUT_SECONDS,
            follow_redirects=True,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        data: dict[str, Any] | None = None,
        timeout: float | None = None,
        raw: bool = False,
    ) -> Any:
        async with self._client(timeout) as client:
            response = await client.request(
                method, path, json=json, params=params, files=files, data=data
            )
        if response.status_code >= 400:
            raise KnowledgeBaseError(response.status_code, _detail(response))
        if raw:
            return response.content
        if not response.content:
            return None
        return response.json()

    # --- convenience wrappers ------------------------------------------------

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, **kwargs)

    async def whoami(self) -> dict[str, Any]:
        user: dict[str, Any] = await self.get("/users/me")
        return user
