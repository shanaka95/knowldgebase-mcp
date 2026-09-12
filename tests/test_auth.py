"""The API key is the identity: if the Knowledge Base rejects it, so do we."""

from __future__ import annotations

import httpx
import respx

from kb_mcp.auth import KnowledgeBaseKeyVerifier

API = "https://kb.test/api/v1"


def _verifier() -> KnowledgeBaseKeyVerifier:
    return KnowledgeBaseKeyVerifier(api_url=API)


async def test_a_valid_key_resolves_to_its_owner() -> None:
    with respx.mock(base_url=API) as api:
        api.get("/users/me").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "u1",
                    "email": "you@example.com",
                    "full_name": "You",
                    "is_superuser": False,
                },
            )
        )
        token = await _verifier().verify_token("kb_good")
    assert token is not None
    assert token.subject == "u1"
    assert token.claims["email"] == "you@example.com"
    assert token.token == "kb_good", "tools reuse the key to act as the caller"


async def test_a_rejected_key_is_not_accepted() -> None:
    with respx.mock(base_url=API) as api:
        api.get("/users/me").mock(
            return_value=httpx.Response(403, json={"detail": "Invalid API key"})
        )
        assert await _verifier().verify_token("kb_revoked") is None


async def test_an_unreachable_knowledge_base_denies_rather_than_admits() -> None:
    """Failing closed matters more than availability here."""
    with respx.mock(base_url=API) as api:
        api.get("/users/me").mock(side_effect=httpx.ConnectError("down"))
        assert await _verifier().verify_token("kb_any") is None


async def test_a_verified_key_is_not_rechecked_on_every_call() -> None:
    verifier = _verifier()
    with respx.mock(base_url=API) as api:
        route = api.get("/users/me").mock(
            return_value=httpx.Response(200, json={"id": "u1", "email": "a@b.c"})
        )
        for _ in range(4):
            assert await verifier.verify_token("kb_good") is not None
        assert route.call_count == 1, "a burst of tool calls is one auth check"


async def test_forgetting_a_key_forces_a_recheck() -> None:
    verifier = _verifier()
    with respx.mock(base_url=API) as api:
        route = api.get("/users/me").mock(
            return_value=httpx.Response(200, json={"id": "u1", "email": "a@b.c"})
        )
        await verifier.verify_token("kb_good")
        verifier.forget("kb_good")
        await verifier.verify_token("kb_good")
        assert route.call_count == 2


async def test_a_rejection_clears_any_earlier_cache() -> None:
    """A key revoked in the app must stop working here on its next use."""
    verifier = _verifier()
    with respx.mock(base_url=API) as api:
        api.get("/users/me").mock(
            return_value=httpx.Response(200, json={"id": "u1", "email": "a@b.c"})
        )
        assert await verifier.verify_token("kb_x") is not None

    verifier.forget("kb_x")
    with respx.mock(base_url=API) as api:
        api.get("/users/me").mock(
            return_value=httpx.Response(403, json={"detail": "gone"})
        )
        assert await verifier.verify_token("kb_x") is None


async def test_tools_are_not_reachable_without_a_key() -> None:
    """The server is created with a verifier, so anonymous callers get nowhere."""
    from kb_mcp.server import mcp

    assert mcp.auth is not None
    assert isinstance(mcp.auth, KnowledgeBaseKeyVerifier)


async def test_formatting_drops_markup_but_keeps_the_words() -> None:
    from kb_mcp.formatting import plain

    assert plain("<p>Hello <b>there</b></p>") == "Hello there"
    assert plain("<p>" + "word " * 200 + "</p>", 40).endswith("…")
    assert plain("") == ""


def test_settings_default_to_the_internal_api() -> None:
    """Inside the deployment the API is a container name, not a public URL."""
    from kb_mcp.config import Settings

    assert "backend" in str(Settings().KB_API_URL)


def test_api_url_strips_a_trailing_slash() -> None:
    from kb_mcp.config import Settings

    s = Settings(KB_API_URL="https://example.com/api/v1/")  # type: ignore[arg-type]
    assert s.api_url == "https://example.com/api/v1"


def test_error_detail_survives_as_text() -> None:
    """A dict detail (the 409 conflict shape) must not print as a Python repr."""
    from kb_mcp.client import _detail

    response = httpx.Response(
        409,
        json={
            "detail": {
                "message": "Document was modified elsewhere",
                "current_version": 3,
            }
        },
    )
    assert _detail(response) == "Document was modified elsewhere"
