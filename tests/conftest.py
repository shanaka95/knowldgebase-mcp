"""Test fixtures: a fake Knowledge Base and an in-process MCP client."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client

from kb_mcp import server as srv
from kb_mcp.client import KnowledgeBase
from kb_mcp.server import mcp

API = "https://kb.test/api/v1"
TOKEN = "kb_testkey"


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    """Intercept every call the server makes to the Knowledge Base."""
    with respx.mock(base_url=API, assert_all_called=False) as router:
        yield router


@pytest.fixture(autouse=True)
def _as_caller() -> Iterator[None]:
    """Run tools as a caller holding TOKEN.

    The HTTP transport normally supplies this from the bearer token; in-process
    there is no transport, so the accessor is replaced directly.
    """
    original = srv.kb
    srv.kb = lambda: KnowledgeBase(TOKEN, API)
    yield
    srv.kb = original


@pytest.fixture
async def client() -> AsyncIterator[Client[Any]]:
    async with Client(mcp) as c:
        yield c


async def call(client: Client[Any], tool: str, **kwargs: Any) -> Any:
    return (await client.call_tool(tool, kwargs)).data


# --- canned API responses ---------------------------------------------------


def space(name: str = "Office", **over: Any) -> dict[str, Any]:
    return {
        "id": over.get("id", "11111111-1111-1111-1111-111111111111"),
        "name": name,
        "slug": name.lower(),
        "description": None,
        "owner_id": "u1",
        "my_role": over.get("my_role", "admin"),
        "document_count": over.get("document_count", 2),
        "member_count": 1,
    }


def page(**over: Any) -> dict[str, Any]:
    base = {
        "id": "22222222-2222-2222-2222-222222222222",
        "namespace_id": "11111111-1111-1111-1111-111111111111",
        "folder_id": None,
        "title": "Runbook",
        "doc_type": None,
        "version": 1,
        "content_html": "<h1>Runbook</h1><p>Promote the standby.</p>",
        "content_text": "Runbook\n\nPromote the standby.",
        "summary": None,
        "updated_at": "2026-09-12T10:00:00Z",
        "embedding_status": "ready",
        "is_stale": False,
        "embedding_version": 1,
        "chunk_count": 0,
    }
    base.update(over)
    return base


def error(status: int, detail: Any) -> httpx.Response:
    return httpx.Response(status, json={"detail": detail})
