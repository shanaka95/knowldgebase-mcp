"""Things that must not be possible, each one a bug that was found here.

Every test in this file failed before the fix beside it, so it is a record of
what went wrong rather than a restatement of what the code does.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kb_mcp import server as srv
from kb_mcp.auth import KnowledgeBaseKeyVerifier
from kb_mcp.config import settings
from tests.conftest import call, page

API = "https://kb.test/api/v1"


# --- ids must not be able to change which endpoint is called ----------------


async def test_a_page_id_cannot_escape_into_another_endpoint(
    client: Client[Any], api: Any
) -> None:
    """`..` in a page id turned `delete_page` into "delete a whole space".

    httpx resolves dot segments while building the URL, so a page id of
    `../namespaces/<id>` left the tool sending DELETE /api/v1/namespaces/<id>,
    which the API happily served as the caller. The tool says it deletes one
    page; it has to be unable to do anything else.
    """
    space = api.delete("/namespaces/11111111-1111-1111-1111-111111111111").mock(
        return_value=httpx.Response(200, json={"message": "gone"})
    )
    api.get("/documents/").mock(return_value=httpx.Response(200, json=page()))

    with pytest.raises(ToolError, match="valid page id"):
        await call(
            client,
            "delete_page",
            page_id="../namespaces/11111111-1111-1111-1111-111111111111",
        )
    assert not space.called, "a page id must never reach the namespaces endpoint"


async def test_an_id_cannot_smuggle_a_query_string(
    client: Client[Any], api: Any
) -> None:
    """Anything that changes the request, not only path segments, is refused."""
    route = api.get(path__startswith="/documents/").mock(
        return_value=httpx.Response(200, json=page())
    )
    with pytest.raises(ToolError, match="valid page id"):
        await call(client, "get_page", page_id="22222222?force=1")
    assert not route.called


async def test_a_public_identifier_is_validated_too(client: Client[Any]) -> None:
    with pytest.raises(ToolError, match="valid"):
        await call(client, "read_public_page", link_or_id="abc%2f..%2fusers")


async def test_a_space_id_cannot_escape_either(client: Client[Any], api: Any) -> None:
    route = api.get(path__startswith="/namespaces/").mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(ToolError, match="valid space id"):
        await call(client, "browse_space", space_id="../../users/me/..")
    assert not route.called


# --- fetching a URL must not reach the network this server sits in ----------


async def test_upload_refuses_the_cloud_metadata_endpoint(
    client: Client[Any],
) -> None:
    """169.254.169.254 is one hop from instance credentials."""
    with pytest.raises(ToolError, match="inside the network|Only http"):
        await call(
            client,
            "upload_document",
            filename="a.pdf",
            space_id="n1",
            url="http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            wait=False,
        )


async def test_upload_refuses_loopback_and_private_addresses(
    client: Client[Any],
) -> None:
    for target in (
        "http://127.0.0.1:8000/mcp",
        "http://10.0.0.5/secrets",
        "http://[::1]:6333/collections",
    ):
        with pytest.raises(ToolError, match="inside the network"):
            await call(
                client,
                "upload_document",
                filename="a.pdf",
                space_id="n1",
                url=target,
                wait=False,
            )


async def test_upload_refuses_an_internal_container_name(
    client: Client[Any],
) -> None:
    """Inside the deployment `qdrant:6333` answers without any credential."""
    with pytest.raises(ToolError, match="inside the network|Could not look up"):
        await call(
            client,
            "upload_document",
            filename="a.pdf",
            space_id="n1",
            url="http://qdrant:6333/collections",
            wait=False,
        )


async def test_upload_refuses_a_non_http_scheme(client: Client[Any]) -> None:
    with pytest.raises(ToolError, match="http and https"):
        await call(
            client,
            "upload_document",
            filename="a.pdf",
            space_id="n1",
            url="file:///etc/passwd",
            wait=False,
        )


async def test_a_redirect_cannot_carry_the_fetch_inside(client: Client[Any]) -> None:
    """Checking only the URL the caller typed is not enough."""
    with respx.mock:
        respx.get("http://93.184.216.34/a.pdf").mock(
            return_value=httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        )
        with pytest.raises(ToolError, match="inside the network"):
            await call(
                client,
                "upload_document",
                filename="a.pdf",
                space_id="n1",
                url="http://93.184.216.34/a.pdf",
                wait=False,
            )


async def test_a_fetched_file_is_not_read_without_limit(
    client: Client[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller-chosen URL decided how much memory this process used."""
    monkeypatch.setattr(srv, "MAX_FETCH_BYTES", 1024)
    with respx.mock:
        respx.get("http://93.184.216.34/big.pdf").mock(
            return_value=httpx.Response(200, content=b"x" * 20_000)
        )
        with pytest.raises(ToolError, match="too large"):
            await call(
                client,
                "upload_document",
                filename="big.pdf",
                space_id="n1",
                url="http://93.184.216.34/big.pdf",
                wait=False,
            )


async def test_a_public_url_is_still_fetched(client: Client[Any], api: Any) -> None:
    """The guard must not break the feature it is guarding."""
    api.post("/imports/").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "i1",
                "filename": "a.pdf",
                "status": "queued",
                "pages_done": 0,
                "pages_total": 0,
                "document_id": None,
            },
        )
    )
    with respx.mock(assert_all_called=False) as fetch:
        fetch.get("http://93.184.216.34/a.pdf").mock(
            return_value=httpx.Response(200, content=b"%PDF-1.7 body")
        )
        fetch.route(host="kb.test").pass_through()
        out = await call(
            client,
            "upload_document",
            filename="a.pdf",
            space_id="n1",
            url="http://93.184.216.34/a.pdf",
            wait=False,
        )
    assert out["imports"][0]["import_id"] == "i1"


# --- one caller must not be able to pin the process -------------------------


async def test_more_files_than_the_api_accepts_are_refused_before_decoding(
    client: Client[Any],
) -> None:
    """The API takes 20 per batch; decoding 200 into memory first is the bug."""
    with pytest.raises(ToolError, match="at most"):
        await call(
            client,
            "upload_document",
            filename="scan.pdf",
            space_id="n1",
            files_base64=[base64.b64encode(b"%PDF").decode()] * 200,
            wait=False,
        )


async def test_an_oversized_payload_is_refused_before_decoding(
    client: Client[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "MAX_UPLOAD_BYTES", 512)
    with pytest.raises(ToolError, match="too large"):
        await call(
            client,
            "upload_document",
            filename="a.pdf",
            space_id="n1",
            content_base64=base64.b64encode(b"x" * 4096).decode(),
            wait=False,
        )


class _Clock:
    """A stand-in for `time` so waiting can be measured without waiting."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


async def test_waiting_on_a_batch_shares_one_deadline(
    client: Client[Any], api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each file got its own full timeout, so ten files meant ten times the wait.

    With `MAX_WAIT_SECONDS` at its default of 540 and the API's batch limit of
    20 files, one call could hold a worker for three hours.
    """
    clock = _Clock()
    monkeypatch.setattr(srv, "time", clock)
    monkeypatch.setattr(settings, "MAX_WAIT_SECONDS", 30.0)

    async def advance(seconds: float) -> None:
        clock.now += seconds

    monkeypatch.setattr(asyncio, "sleep", advance)

    queued = [
        {
            "id": f"i{n}",
            "filename": f"{n}.pdf",
            "status": "queued",
            "pages_done": 0,
            "pages_total": 0,
            "document_id": None,
        }
        for n in range(1, 4)
    ]
    api.post("/imports/batch").mock(
        return_value=httpx.Response(200, json={"data": queued, "count": 3})
    )
    for job in queued:
        api.get(f"/imports/{job['id']}").mock(
            return_value=httpx.Response(200, json=job)
        )

    await call(
        client,
        "upload_document",
        filename="scan.pdf",
        space_id="n1",
        files_base64=[base64.b64encode(b"%PDF").decode()] * 3,
        wait=True,
    )
    assert clock.now <= 30.0, "three files must not mean three times the caller's wait"


# --- the scratch page an append borrows must always be cleaned up -----------


async def test_the_append_scratch_page_is_removed_when_conversion_is_unusable(
    client: Client[Any], api: Any
) -> None:
    """Appending converts markdown by creating a throwaway page and deleting it.

    Anything that went wrong between the two left `__append_scratch__` sitting
    in the caller's space for good - indexed, searchable and never explained.
    """
    api.get("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json=page())
    )
    # The conversion came back without the HTML the append needs.
    api.post("/documents/").mock(
        return_value=httpx.Response(200, json={"id": "scratch", "title": "x"})
    )
    removed = api.delete("/documents/scratch").mock(
        return_value=httpx.Response(200, json={})
    )
    api.put("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json=page(version=2))
    )

    with pytest.raises(ToolError):
        await call(
            client,
            "update_page",
            page_id="22222222-2222-2222-2222-222222222222",
            content="New note.",
            mode="append",
        )
    assert removed.called, "the scratch page must not outlive the failure"


# --- moving a page needs somewhere to move it to ----------------------------


async def test_move_page_refuses_to_move_nowhere(client: Client[Any], api: Any) -> None:
    """With neither argument given it quietly moved the page to the space root."""
    route = api.post("/documents/22222222-2222-2222-2222-222222222222/move").mock(
        return_value=httpx.Response(200, json=page())
    )
    with pytest.raises(ToolError, match="Give a destination"):
        await call(client, "move_page", page_id="22222222-2222-2222-2222-222222222222")
    assert not route.called


# --- what the verifier keeps, and what it says out loud ---------------------


async def test_the_verifier_does_not_keep_raw_keys_in_memory() -> None:
    """A live API key is a credential; the cache held every one it had seen."""
    verifier = KnowledgeBaseKeyVerifier(api_url=API)
    with respx.mock(base_url=API) as api:
        api.get("/users/me").mock(
            return_value=httpx.Response(200, json={"id": "u1", "email": "a@b.c"})
        )
        await verifier.verify_token("kb_plaintext_secret")
    assert "kb_plaintext_secret" not in verifier._cache
    assert not any("kb_plaintext_secret" in k for k in verifier._cache)


async def test_the_verifier_cache_is_bounded() -> None:
    """Nothing ever left the cache, not even after it had expired."""
    verifier = KnowledgeBaseKeyVerifier(api_url=API)
    with respx.mock(base_url=API) as api:
        api.get("/users/me").mock(
            return_value=httpx.Response(200, json={"id": "u1", "email": "a@b.c"})
        )
        for n in range(srv_cache_limit() + 50):
            await verifier.verify_token(f"kb_{n}")
    assert len(verifier._cache) <= srv_cache_limit()


def srv_cache_limit() -> int:
    from kb_mcp.auth import MAX_CACHED_KEYS

    return MAX_CACHED_KEYS


async def test_a_failed_verification_does_not_log_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The API's error body is echoed back; it can contain the key presented."""
    verifier = KnowledgeBaseKeyVerifier(api_url=API)
    with respx.mock(base_url=API) as api:
        api.get("/users/me").mock(
            return_value=httpx.Response(
                500, json={"detail": "could not check key kb_supersecret"}
            )
        )
        with caplog.at_level(logging.WARNING, logger="kb_mcp.auth"):
            assert await verifier.verify_token("kb_supersecret") is None
    assert "kb_supersecret" not in caplog.text


async def test_two_callers_never_see_each_other_s_identity() -> None:
    """The cache is shared between every request, so its key has to be exact."""
    verifier = KnowledgeBaseKeyVerifier(api_url=API)
    with respx.mock(base_url=API) as api:

        def who(request: httpx.Request) -> httpx.Response:
            key = request.headers["authorization"].removeprefix("Bearer ")
            return httpx.Response(200, json={"id": key, "email": f"{key}@x.test"})

        api.get("/users/me").mock(side_effect=who)
        first, second = await asyncio.gather(
            verifier.verify_token("kb_alice"), verifier.verify_token("kb_bob")
        )
    assert first is not None and second is not None
    assert first.subject == "kb_alice"
    assert second.subject == "kb_bob"
    assert first.token == "kb_alice" and second.token == "kb_bob"


# --- the README is the only description of what this endpoint can do --------


async def test_the_readme_lists_every_tool(client: Client[Any]) -> None:
    """Sharing was missing from it entirely, including world-readable links."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    missing = [t.name for t in await client.list_tools() if f"`{t.name}`" not in readme]
    assert not missing, f"undocumented tools on a public endpoint: {missing}"
