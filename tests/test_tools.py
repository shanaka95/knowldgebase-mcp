"""What each tool sends to the Knowledge Base, and what it gives back."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.conftest import call, error, page, space


async def test_every_tool_is_described(client: Client[Any]) -> None:
    """An agent picks tools by reading them, so a bare name is not enough."""
    tools = await client.list_tools()
    assert len(tools) >= 18
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) > 60, f"{tool.name} is described too thinly"


# --- orientation ------------------------------------------------------------


async def test_list_spaces_reports_your_role(client: Client[Any], api: Any) -> None:
    api.get("/namespaces/").mock(
        return_value=httpx.Response(
            200, json={"data": [space(my_role="editor")], "count": 1}
        )
    )
    out = await call(client, "list_spaces")
    assert out["spaces"][0]["name"] == "Office"
    assert out["spaces"][0]["your_role"] == "editor"


async def test_browse_space_separates_folders_from_pages(
    client: Client[Any], api: Any
) -> None:
    api.get("/namespaces/11111111-1111-1111-1111-111111111111/tree").mock(
        return_value=httpx.Response(
            200,
            json={
                "namespace": space(),
                "folders": [{"id": "f1", "name": "Runbooks", "parent_id": None}],
                "documents": [page()],
            },
        )
    )
    out = await call(
        client, "browse_space", space_id="11111111-1111-1111-1111-111111111111"
    )
    assert out["folders"] == [
        {"id": "f1", "name": "Runbooks", "parent_folder_id": None}
    ]
    assert out["pages"][0]["title"] == "Runbook"
    assert "content" not in out["pages"][0], "browsing must not haul page bodies around"


# --- reading ----------------------------------------------------------------


async def test_get_page_returns_text_by_default(client: Client[Any], api: Any) -> None:
    api.get("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json=page())
    )
    out = await call(client, "get_page", page_id="22222222-2222-2222-2222-222222222222")
    assert out["content_format"] == "text"
    assert "<h1>" not in out["content"]


async def test_get_page_can_return_html_for_editing(
    client: Client[Any], api: Any
) -> None:
    api.get("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json=page())
    )
    out = await call(
        client, "get_page", page_id="22222222-2222-2222-2222-222222222222", as_html=True
    )
    assert out["content_format"] == "html"
    assert "<h1>" in out["content"]


async def test_search_explains_why_each_page_matched(
    client: Client[Any], api: Any
) -> None:
    api.get("/search/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "document_id": "d1",
                        "title": "Runbook",
                        "namespace_id": "n1",
                        "namespace_name": "Office",
                        "namespace_slug": "office",
                        "score": 0.0821,
                        "snippet": "Promote the <mark>standby</mark>",
                        "embedding_status": "ready",
                        "matched_chunk_title": "Steps",
                        "sources": [
                            {
                                "method": "bm25",
                                "target": "document",
                                "rank": 1,
                                "score": 9.0,
                                "contribution": 0.016,
                            },
                            {
                                "method": "vector",
                                "target": "summary",
                                "rank": 2,
                                "score": 0.8,
                                "contribution": 0.016,
                            },
                        ],
                    }
                ],
                "count": 1,
                "used_bm25": True,
                "used_vector": True,
            },
        )
    )
    out = await call(client, "search_pages", query="promote standby")
    hit = out["results"][0]
    assert hit["matched_by"] == ["bm25:document", "vector:summary"]
    assert hit["matched_section"] == "Steps"
    assert "<mark>" not in hit["snippet"], "markup would only confuse the reader"


async def test_search_refuses_to_disable_both_methods(client: Client[Any]) -> None:
    with pytest.raises(ToolError, match="at least one"):
        await call(client, "search_pages", query="x", keyword=False, semantic=False)


async def test_ask_hands_over_whole_pages_and_writes_no_answer(
    client: Client[Any], api: Any
) -> None:
    """The caller writes the reply, so it gets the pages rather than a summary.

    Answering here and letting the calling model rewrite that answer pays for
    two generations to say one thing, and the second is a retelling of the
    first rather than of the page.
    """
    long_page = "Notice is three months. " * 400
    context = api.post("/ask/context").mock(
        return_value=httpx.Response(
            200,
            json={
                "question": "what is the notice period?",
                "documents": [
                    {
                        "index": 1,
                        "document_id": "d1",
                        "title": "Employment Contract",
                        "namespace_id": "n1",
                        "namespace_slug": "office",
                        "namespace_name": "Office",
                        "text": long_page,
                        "cited": False,
                        "score": 0.9,
                    }
                ],
                "searched": 10,
                "used": 1,
                "passages": 1,
                "reranked": True,
                "truncated": False,
            },
        )
    )
    answering = api.post("/ask/").mock(return_value=httpx.Response(500))

    out = await call(client, "ask_knowledge_base", question="what is the notice period?")

    assert context.called, "it reads the pages"
    assert not answering.called, "and does not pay for an answer it will not use"
    assert "answer" not in out, "the caller writes it"
    assert len(out["documents"]) == 1
    assert out["documents"][0]["text"] == long_page.strip(), (
        "the whole page, not a preview of it"
    )
    assert out["pages_considered"] == 10 and out["pages_used"] == 1


async def test_ask_returns_the_answer_with_its_citations(
    client: Client[Any], api: Any
) -> None:
    api.post("/ask/").mock(
        return_value=httpx.Response(
            200,
            json={
                "question": "how do I fail over?",
                "answer": "Promote the standby [1].",
                "citations": [
                    {
                        "index": 1,
                        "document_id": "d1",
                        "title": "Runbook",
                        "namespace_id": "n1",
                        "namespace_slug": "office",
                        "namespace_name": "Office",
                        "text": "Promote the standby.",
                        "chunk_title": "Steps",
                        "cited": True,
                        "score": 0.08,
                    },
                    {
                        "index": 2,
                        "document_id": "d2",
                        "title": "Other",
                        "namespace_id": "n1",
                        "namespace_slug": "office",
                        "namespace_name": "Office",
                        "text": "Unrelated.",
                        "chunk_title": None,
                        "cited": False,
                        "score": 0.03,
                    },
                ],
                "searched": 4,
                "used": 2,
                "passages": 2,
            },
        )
    )
    out = await call(
        client,
        "ask_knowledge_base",
        question="how do I fail over?",
        write_answer=True,
    )
    assert out["answer"] == "Promote the standby [1]."
    used = [c for c in out["citations"] if c["used_in_answer"]]
    assert len(used) == 1 and used[0]["number"] == 1
    assert len(out["citations"]) == 2, "what was read but unused is still worth seeing"


# --- writing ----------------------------------------------------------------


async def test_create_page_passes_the_chosen_format(
    client: Client[Any], api: Any
) -> None:
    route = api.post("/documents/").mock(return_value=httpx.Response(200, json=page()))
    await call(
        client,
        "create_page",
        title="Runbook",
        content="# Runbook",
        space_id="11111111-1111-1111-1111-111111111111",
        format="markdown",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["content_format"] == "markdown"
    assert sent["content"] == "# Runbook"


async def test_append_keeps_what_was_already_there(
    client: Client[Any], api: Any
) -> None:
    """Appending must not lose the existing page, which someone else may have written."""
    api.get("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json=page())
    )
    # markdown is converted through a scratch page, which is then removed
    api.post("/documents/").mock(
        return_value=httpx.Response(
            200, json=page(id="scratch", content_html="<p>New note.</p>")
        )
    )
    api.delete("/documents/scratch").mock(return_value=httpx.Response(200, json={}))
    put = api.put("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json=page(version=2))
    )

    await call(
        client,
        "update_page",
        page_id="22222222-2222-2222-2222-222222222222",
        content="New note.",
        mode="append",
    )
    sent = json.loads(put.calls[0].request.content)
    assert sent["content"].startswith("<h1>Runbook</h1>"), "the original survives"
    assert sent["content"].endswith("<p>New note.</p>"), "the addition is at the end"
    assert sent["content_format"] == "html"


async def test_replace_overwrites_without_reading_first(
    client: Client[Any], api: Any
) -> None:
    put = api.put("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json=page(version=2))
    )
    await call(
        client,
        "update_page",
        page_id="22222222-2222-2222-2222-222222222222",
        content="Replaced.",
        mode="replace",
    )
    sent = json.loads(put.calls[0].request.content)
    assert sent["content"] == "Replaced."


async def test_update_needs_something_to_change(client: Client[Any]) -> None:
    with pytest.raises(ToolError, match="new `content`"):
        await call(client, "update_page", page_id="d1")


async def test_delete_reports_what_it_removed(client: Client[Any], api: Any) -> None:
    api.get("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json=page())
    )
    api.delete("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json={"message": "ok"})
    )
    out = await call(
        client, "delete_page", page_id="22222222-2222-2222-2222-222222222222"
    )
    assert out["deleted"]["title"] == "Runbook", "so the transcript shows what was lost"


# --- files ------------------------------------------------------------------


async def test_upload_needs_a_file(client: Client[Any]) -> None:
    with pytest.raises(ToolError, match="content_base64"):
        await call(client, "upload_document", filename="a.pdf", space_id="n1")


async def test_upload_rejects_two_sources_at_once(client: Client[Any]) -> None:
    with pytest.raises(ToolError, match="not several"):
        await call(
            client,
            "upload_document",
            filename="a.pdf",
            space_id="n1",
            content_base64="eA==",
            url="https://example.com/a.pdf",
        )


async def test_upload_rejects_invalid_base64(client: Client[Any]) -> None:
    with pytest.raises(ToolError, match="not valid base64"):
        await call(
            client,
            "upload_document",
            filename="a.pdf",
            space_id="n1",
            content_base64="not base64!!",
        )


async def test_upload_can_return_before_parsing_finishes(
    client: Client[Any], api: Any
) -> None:
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
    out = await call(
        client,
        "upload_document",
        filename="a.pdf",
        space_id="n1",
        content_base64=base64.b64encode(b"%PDF-1.7").decode(),
        wait=False,
    )
    assert out["imports"][0]["import_id"] == "i1"
    assert out["imports"][0]["status"] == "queued"


# --- failures the caller must understand ------------------------------------


async def test_a_read_only_key_is_told_what_to_do(
    client: Client[Any], api: Any
) -> None:
    api.post("/documents/").mock(return_value=error(403, "API key lacks write scope"))
    with pytest.raises(ToolError, match="read-only"):
        await call(client, "create_page", title="t", content="c", space_id="n1")


async def test_a_missing_id_points_at_how_to_find_a_real_one(
    client: Client[Any], api: Any
) -> None:
    api.get("/documents/nope").mock(return_value=error(404, "Document not found"))
    # Reading falls back to a public link before giving up, so that path is
    # closed here too.
    api.get("/public/documents/nope").mock(return_value=error(404, "Not found"))
    with pytest.raises(ToolError, match="list_spaces or browse_space"):
        await call(client, "get_page", page_id="nope")


async def test_a_page_shared_by_link_is_readable_even_when_it_is_not_yours(
    client: Client[Any], api: Any
) -> None:
    """The whole point of a public link: it works without owning the page."""
    api.get("/documents/abc123").mock(return_value=error(404, "Document not found"))
    api.get("/public/documents/abc123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "d9",
                "slug": "abc123",
                "title": "Menu",
                "doc_type": "Note",
                "content_html": "<p>Soup of the day.</p>",
                "updated_at": "2026-09-12T10:00:00Z",
                "shared_by": "Sam",
            },
        )
    )
    out = await call(client, "get_page", page_id="abc123")
    assert out["title"] == "Menu"
    assert out["access"] == "public link"
    assert "Soup of the day." in out["content"]


async def test_an_oversized_file_says_so(client: Client[Any], api: Any) -> None:
    api.post("/imports/").mock(return_value=error(413, "File exceeds the 50 MB limit"))
    with pytest.raises(ToolError, match="too large"):
        await call(
            client,
            "upload_document",
            filename="a.pdf",
            space_id="n1",
            content_base64=base64.b64encode(b"x" * 10).decode(),
            wait=False,
        )


async def test_validation_errors_name_the_offending_field(
    client: Client[Any], api: Any
) -> None:
    api.post("/documents/").mock(
        return_value=error(
            422,
            [
                {
                    "loc": ["body", "title"],
                    "msg": "String should have at least 1 character",
                }
            ],
        )
    )
    with pytest.raises(ToolError, match="title"):
        await call(client, "create_page", title="x", content="c", space_id="n1")


async def test_an_unsupported_file_type_is_reported_once(
    client: Client[Any], api: Any
) -> None:
    """The API names the type and the accepted ones; do not repeat it back."""
    api.post("/imports/").mock(
        return_value=error(415, "Unsupported file type 'text/plain'. Upload a PDF.")
    )
    with pytest.raises(ToolError) as caught:
        await call(
            client,
            "upload_document",
            filename="a.txt",
            space_id="n1",
            content_base64=base64.b64encode(b"hello").decode(),
            wait=False,
        )
    assert str(caught.value).count("Unsupported file type") == 1


async def test_upload_passes_a_parsing_prompt_when_given(
    client: Client[Any], api: Any
) -> None:
    route = api.post("/imports/").mock(
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
    await call(
        client,
        "upload_document",
        filename="a.pdf",
        space_id="n1",
        content_base64=base64.b64encode(b"%PDF-1.7").decode(),
        prompt="Keep the tables as tables.",
        wait=False,
    )
    body = route.calls[0].request.content.decode("utf-8", "replace")
    assert "Keep the tables as tables." in body


# --- page types -------------------------------------------------------------


async def test_page_types_report_what_is_already_in_use(
    client: Client[Any], api: Any
) -> None:
    api.get("/documents/types").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"name": "Invoice", "count": 12},
                    {"name": "Letter", "count": 0},
                ],
                "count": 2,
            },
        )
    )
    out = await call(client, "list_page_types")
    assert out["types"][0] == {"name": "Invoice", "pages": 12}
    assert out["types"][1]["pages"] == 0, "a suggestion nobody has used yet"


async def test_a_new_page_can_be_filed_under_a_type(
    client: Client[Any], api: Any
) -> None:
    route = api.post("/documents/").mock(
        return_value=httpx.Response(200, json=page(doc_type="Letter"))
    )
    out = await call(
        client,
        "create_page",
        title="Termination",
        content="Body.",
        space_id="n1",
        doc_type="Letter",
    )
    assert json.loads(route.calls[0].request.content)["doc_type"] == "Letter"
    assert out["created"]["type"] == "Letter"


async def test_the_type_can_be_changed_without_touching_the_body(
    client: Client[Any], api: Any
) -> None:
    route = api.put("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json=page(doc_type="Report"))
    )
    await call(
        client,
        "update_page",
        page_id="22222222-2222-2222-2222-222222222222",
        doc_type="Report",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"doc_type": "Report"}, (
        "nothing else was sent, so nothing else changes"
    )


async def test_an_empty_type_is_how_it_is_cleared(
    client: Client[Any], api: Any
) -> None:
    """Distinct from omitting it, which leaves the type alone."""
    route = api.put("/documents/22222222-2222-2222-2222-222222222222").mock(
        return_value=httpx.Response(200, json=page())
    )
    await call(
        client,
        "update_page",
        page_id="22222222-2222-2222-2222-222222222222",
        doc_type="",
    )
    assert json.loads(route.calls[0].request.content)["doc_type"] == ""


# --- several files at once --------------------------------------------------


def _queued(job_id: str, filename: str, **over: Any) -> dict[str, Any]:
    base = {
        "id": job_id,
        "filename": filename,
        "status": "queued",
        "pages_done": 0,
        "pages_total": 0,
        "document_id": None,
    }
    base.update(over)
    return base


async def test_several_files_go_to_the_batch_endpoint(
    client: Client[Any], api: Any
) -> None:
    route = api.post("/imports/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [_queued("i1", "a.pdf"), _queued("i2", "b.pdf")],
                "count": 2,
            },
        )
    )
    out = await call(
        client,
        "upload_document",
        filename="a.pdf",
        space_id="n1",
        files_base64=[
            base64.b64encode(b"%PDF-1.7 one").decode(),
            base64.b64encode(b"%PDF-1.7 two").decode(),
        ],
        filenames=["a.pdf", "b.pdf"],
        doc_type="Invoice",
        wait=False,
    )
    body = route.calls[0].request.content.decode("utf-8", "replace")
    assert "Invoice" in body
    assert "combine" in body
    assert len(out["imports"]) == 2


async def test_combining_is_passed_through(client: Client[Any], api: Any) -> None:
    route = api.post("/imports/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    _queued(
                        "i1",
                        "a.pdf +1 more",
                        file_count=2,
                        filenames=["a.pdf", "b.pdf"],
                    )
                ],
                "count": 1,
            },
        )
    )
    out = await call(
        client,
        "upload_document",
        filename="a.pdf",
        space_id="n1",
        files_base64=[
            base64.b64encode(b"%PDF-1.7 one").decode(),
            base64.b64encode(b"%PDF-1.7 two").decode(),
        ],
        filenames=["a.pdf", "b.pdf"],
        combine=True,
        wait=False,
    )
    assert "combine" in route.calls[0].request.content.decode("utf-8", "replace")
    assert len(out["imports"]) == 1
    assert out["imports"][0]["files"] == ["a.pdf", "b.pdf"]


async def test_names_are_invented_when_none_are_given(
    client: Client[Any], api: Any
) -> None:
    route = api.post("/imports/batch").mock(
        return_value=httpx.Response(
            200, json={"data": [_queued("i1", "scan-1.pdf")], "count": 1}
        )
    )
    await call(
        client,
        "upload_document",
        filename="scan.pdf",
        space_id="n1",
        files_base64=[
            base64.b64encode(b"%PDF-1.7 one").decode(),
            base64.b64encode(b"%PDF-1.7 two").decode(),
        ],
        wait=False,
    )
    body = route.calls[0].request.content.decode("utf-8", "replace")
    assert "scan-1.pdf" in body and "scan-2.pdf" in body


async def test_mismatched_names_are_refused(client: Client[Any]) -> None:
    """Silently pairing the wrong name to the wrong file would be worse."""
    with pytest.raises(ToolError, match="they must match"):
        await call(
            client,
            "upload_document",
            filename="a.pdf",
            space_id="n1",
            files_base64=[base64.b64encode(b"x").decode()],
            filenames=["a.pdf", "b.pdf"],
            wait=False,
        )


async def test_an_empty_file_in_a_batch_is_named(client: Client[Any]) -> None:
    with pytest.raises(ToolError, match="File 2"):
        await call(
            client,
            "upload_document",
            filename="a.pdf",
            space_id="n1",
            files_base64=[base64.b64encode(b"%PDF").decode(), ""],
            filenames=["a.pdf", "b.pdf"],
            wait=False,
        )


# --- sharing ----------------------------------------------------------------


async def test_sharing_reports_who_got_access_and_who_was_invited(
    client: Client[Any], api: Any
) -> None:
    route = api.post("/documents/d1/shares/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "shared": [
                    {
                        "id": "s1",
                        "document_id": "d1",
                        "user": {
                            "id": "u2",
                            "email": "known@example.com",
                            "full_name": "Known Person",
                        },
                        "role": "editor",
                        "created_at": None,
                    }
                ],
                "invited": [
                    {
                        "id": "i1",
                        "email": "stranger@example.com",
                        "role": "editor",
                        "expires_at": "2026-09-26T00:00:00Z",
                        "created_at": None,
                    }
                ],
                "skipped": [
                    {"email": "me@example.com", "reason": "That is your own address"}
                ],
                "recipients": 2,
                "max_recipients": 50,
            },
        )
    )
    out = await call(
        client,
        "share_page",
        page_id="d1",
        emails=["known@example.com", "stranger@example.com", "me@example.com"],
        can_edit=True,
        message="Have a look.",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["role"] == "editor"
    assert sent["message"] == "Have a look."

    assert out["shared"][0]["email"] == "known@example.com"
    assert out["invited"][0]["email"] == "stranger@example.com"
    assert out["skipped"][0]["reason"] == "That is your own address"
    assert out["limit"] == 50


async def test_sharing_defaults_to_read_only(client: Client[Any], api: Any) -> None:
    route = api.post("/documents/d1/shares/batch").mock(
        return_value=httpx.Response(
            200, json={"shared": [], "invited": [], "skipped": []}
        )
    )
    await call(client, "share_page", page_id="d1", emails=["a@b.c"])
    assert json.loads(route.calls[0].request.content)["role"] == "viewer"


async def test_a_public_link_comes_with_a_warning(
    client: Client[Any], api: Any
) -> None:
    """An agent handing this to somebody should be able to say what it means."""
    api.post("/documents/d1/public").mock(
        return_value=httpx.Response(
            200, json={"slug": "abc123", "url": "https://kb.test/p/abc123"}
        )
    )
    out = await call(client, "share_page_by_link", page_id="d1")
    assert out["url"] == "https://kb.test/p/abc123"
    assert "without signing in" in out["warning"]


async def test_a_public_link_can_be_withdrawn(client: Client[Any], api: Any) -> None:
    route = api.delete("/documents/d1/public").mock(
        return_value=httpx.Response(200, json={"message": "ok"})
    )
    out = await call(client, "stop_sharing_by_link", page_id="d1")
    assert route.call_count == 1
    assert out["public"] is False


async def test_a_public_page_can_be_read_from_its_full_url(
    client: Client[Any], api: Any
) -> None:
    api.get("/public/documents/abc123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "d9",
                "slug": "abc123",
                "title": "Menu",
                "content_html": "<p>Soup.</p>",
            },
        )
    )
    out = await call(
        client, "read_public_page", link_or_id="https://plusgpt.io/p/abc123"
    )
    assert out["title"] == "Menu"


async def test_cloning_says_the_copy_is_private(client: Client[Any], api: Any) -> None:
    route = api.post("/documents/d1/clone").mock(
        return_value=httpx.Response(200, json=page(title="Runbook (copy)"))
    )
    out = await call(client, "clone_page", page_id="d1", space_id="n2", title="Mine")
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"namespace_id": "n2", "title": "Mine"}
    assert "private" in out["note"]


async def test_shared_pages_are_marked_as_somebody_else_s(
    client: Client[Any], api: Any
) -> None:
    api.get("/documents/shared-with-me").mock(
        return_value=httpx.Response(
            200,
            json={
                "documents": [page(my_role="editor")],
                "namespaces": [space()],
            },
        )
    )
    out = await call(client, "list_shared_with_you")
    assert out["pages"][0]["shared_with_you"] is True
    assert out["pages"][0]["your_role"] == "editor"


async def test_sharing_a_space_reports_the_same_three_outcomes(
    client: Client[Any], api: Any
) -> None:
    route = api.post("/namespaces/n1/members/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "shared": [
                    {
                        "id": "m1",
                        "user": {
                            "id": "u2",
                            "email": "known@example.com",
                            "full_name": "Known",
                        },
                        "role": "editor",
                    }
                ],
                "invited": [
                    {
                        "id": "i1",
                        "email": "stranger@example.com",
                        "role": "editor",
                        "expires_at": "2026-09-26T00:00:00Z",
                        "target": "space",
                    }
                ],
                "skipped": [],
                "members": 2,
                "max_members": 50,
            },
        )
    )
    out = await call(
        client,
        "share_space",
        space_id="n1",
        emails=["known@example.com", "stranger@example.com"],
        role="editor",
        message="Everything is in here.",
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["role"] == "editor"
    assert sent["message"] == "Everything is in here."
    assert out["shared"][0]["email"] == "known@example.com"
    assert out["invited"][0]["email"] == "stranger@example.com"
    assert out["limit"] == 50


async def test_sharing_a_space_defaults_to_read_only(
    client: Client[Any], api: Any
) -> None:
    """The wider the grant, the more it should have to be asked for."""
    route = api.post("/namespaces/n1/members/batch").mock(
        return_value=httpx.Response(
            200, json={"shared": [], "invited": [], "skipped": []}
        )
    )
    await call(client, "share_space", space_id="n1", emails=["a@b.c"])
    assert json.loads(route.calls[0].request.content)["role"] == "viewer"


async def test_a_space_somebody_shared_is_marked_as_theirs(
    client: Client[Any], api: Any
) -> None:
    api.get("/namespaces/").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    space(name="Mine"),
                    space(name="Theirs", id="n2") | {"shared_with_you": True},
                ],
                "count": 2,
            },
        )
    )
    out = await call(client, "list_spaces")
    mine = [s for s in out["spaces"] if s["name"] == "Mine"][0]
    theirs = [s for s in out["spaces"] if s["name"] == "Theirs"][0]
    assert "shared_with_you" not in mine
    assert theirs["shared_with_you"] is True
