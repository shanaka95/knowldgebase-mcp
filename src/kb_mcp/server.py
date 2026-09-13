"""MCP tools for the Knowledge Base.

Design notes that shape every tool below:

* **The caller's API key is the identity.** Nothing here can reach a space the
  key cannot reach, and a read-only key gets a clear refusal on any write.
* **Ids, not names.** Tools return ids so the next call is unambiguous; names
  are for the human reading the transcript.
* **Say what happened.** Writes report the resulting state (version, indexing)
  rather than a bare "ok", so an agent can decide what to do next without a
  follow-up call.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import ipaddress
import mimetypes
import re
import socket
import time
from typing import Annotated, Any, Literal
from uuid import uuid4

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.types import File
from pydantic import Field

from kb_mcp import formatting as fmt
from kb_mcp.auth import KnowledgeBaseKeyVerifier
from kb_mcp.client import KnowledgeBase, KnowledgeBaseError
from kb_mcp.config import settings

INSTRUCTIONS = """
Read and write a personal Knowledge Base: spaces (like "Personal" and "Office"),
folders inside them, and pages written in rich text.

Where to start:
- `search_pages` to find pages by keyword or meaning, `ask_knowledge_base` when
  you want a written answer with citations rather than a list to read yourself.
- `list_spaces` then `browse_space` to see what exists before creating anything,
  so new pages land in the right place instead of piling up at the root.
- `create_page` accepts markdown (easiest), plain text, or HTML.
- `upload_document` turns PDFs or images into normal, searchable pages and
  keeps the originals attached. Several files can go in one call, either as a
  page each or combined into one.
- Pages carry a *type* (Letter, Invoice, Runbook). Call `list_page_types` before
  filing something so you reuse the vocabulary already in use here.
- `share_page` gives named people access by email, inviting anyone who has no
  account yet. `share_page_by_link` makes a page readable by anyone at all -
  say so plainly before using it.
- `list_shared_with_you` shows what other people have shared - whole spaces and
  individual pages, kept apart because they are different grants. Those are not
  yours: editing one changes what its owner sees, and it can be withdrawn.
  `clone_page` takes a private copy that cannot be taken away.
- `share_space` hands over a whole space, including anything added to it later.
  It is a much bigger grant than `share_page`; say so before using it.

Worth knowing:
- A new or edited page takes a short while to become searchable; everything
  reports an `indexing` state, and `wait_for_indexing` blocks until it is ready.
- Every page belongs to exactly one space and optionally one folder.
""".strip()

mcp: FastMCP[Any] = FastMCP(
    name="Knowledge Base",
    version="1.0.0",
    instructions=INSTRUCTIONS,
    auth=KnowledgeBaseKeyVerifier(),
)


def _public_base() -> str:
    """The address a person would paste into a browser.

    PUBLIC_URL is set on a real deployment; failing that, fall back to the API
    host with its /api/v1 suffix removed, which is right for the single-origin
    layout this app ships with.
    """
    if settings.PUBLIC_URL is not None:
        return str(settings.PUBLIC_URL).rstrip("/").removesuffix("/mcp")
    return settings.api_url.removesuffix("/api/v1").rstrip("/")


def kb() -> KnowledgeBase:
    """The Knowledge Base as the caller, never as anyone else."""
    token = get_access_token()
    if token is None:  # pragma: no cover - the transport rejects these first
        raise ToolError(
            "Not authenticated. Send your Knowledge Base API key as a bearer token."
        )
    return KnowledgeBase(token.token)


def _fail(exc: KnowledgeBaseError) -> ToolError:
    """Turn an API error into something an agent can act on."""
    if exc.status in (401, 403):
        if "write scope" in exc.detail:
            return ToolError(
                "This API key is read-only. Create a read/write key in the "
                "Knowledge Base under Settings → API keys to make changes."
            )
        return ToolError(f"Not allowed: {exc.detail}")
    if exc.status == 404:
        return ToolError(
            f"Not found, or not visible to you: {exc.detail}. "
            "Use list_spaces or browse_space to get valid ids."
        )
    if exc.status == 409:
        return ToolError(f"Conflict: {exc.detail}")
    if exc.status == 413:
        return ToolError(f"That file is too large: {exc.detail}")
    if exc.status == 415:
        # The API already names the type and the accepted ones; prefixing it
        # would only repeat the words back at the caller.
        return ToolError(exc.detail)
    if exc.status == 422:
        return ToolError(f"Invalid request: {exc.detail}")
    return ToolError(f"Knowledge Base error {exc.status}: {exc.detail}")


async def _call(coro: Any) -> Any:
    try:
        return await coro
    except KnowledgeBaseError as exc:
        raise _fail(exc) from exc


# Ids and slugs are the only caller-supplied values that become part of a URL
# path. Everything the API mints is a UUID or a short slug, so this is what one
# looks like.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _ident(value: str, what: str) -> str:
    """Check an id before it is pasted into a request path.

    httpx resolves `..` while building the URL, so an id of
    `../namespaces/<uuid>` would silently turn a call about one page into a
    call about a whole space - with the caller's own credentials, against an
    endpoint the tool never meant to touch. The same goes for a `?`, which
    would append query parameters to somebody else's request. Refusing here is
    what keeps each tool's reach equal to its description.
    """
    if not _SAFE_ID.match(value):
        raise ToolError(
            f"That is not a valid {what}. Ids are letters, digits, hyphens and "
            "underscores; use list_spaces or browse_space to get real ones."
        )
    return value


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------


@mcp.tool
async def whoami() -> dict[str, Any]:
    """Who this API key belongs to, and whether it can write.

    Useful as a first call to confirm the connection works and to learn whose
    knowledge base you are acting in.
    """
    api = kb()
    user = await _call(api.whoami())
    # Probing the scope beats making the caller discover it by failing a real
    # edit. Writing to an id that cannot exist touches nothing: a read-only key
    # is refused before the handler runs, a read/write key gets "not found".
    can_write = True
    try:
        await api.put(f"/documents/{uuid4()}", json={"title": "scope probe"})
    except KnowledgeBaseError as exc:
        if exc.status == 403 and "write scope" in exc.detail:
            can_write = False
    out: dict[str, Any] = {
        "email": user.get("email"),
        "name": user.get("full_name"),
        "can_write": can_write,
    }
    # Only a public address is worth telling the caller; the container name this
    # server dials internally would be a dead link for them.
    if settings.PUBLIC_URL is not None:
        out["knowledge_base"] = (
            str(settings.PUBLIC_URL).removesuffix("/mcp").rstrip("/")
        )
    return out


@mcp.tool
async def list_spaces() -> dict[str, Any]:
    """List every space you can reach, with your role in each.

    A *space* is a top-level container, such as "Personal" or "Office". Pages
    live in a space and optionally in a folder inside it. Call this before
    creating a page so it lands somewhere sensible.

    `your_role` is one of viewer, editor or admin; you need editor or above to
    add or change anything in that space.
    """
    data = await _call(kb().get("/namespaces/", params={"limit": 100}))
    return {"spaces": [fmt.space(n) for n in data["data"]], "count": data["count"]}


@mcp.tool
async def browse_space(
    space_id: Annotated[str, Field(description="Space id from list_spaces")],
) -> dict[str, Any]:
    """Show the folders and pages in a space.

    Use this to decide where a new page belongs, or to find the id of a page you
    want to read or edit. Folders nest: `parent_folder_id` is null for the ones
    at the top of the space.

    Pages are listed without their content - call `get_page` for that.
    """
    tree = await _call(kb().get(f"/namespaces/{_ident(space_id, 'space id')}/tree"))
    return {
        "space": fmt.space(tree["namespace"]),
        "folders": [fmt.folder(f) for f in tree["folders"]],
        "pages": [fmt.page_summary(d) for d in tree["documents"]],
    }


@mcp.tool
async def recent_pages(
    limit: Annotated[int, Field(ge=1, le=50, description="How many to return")] = 10,
) -> dict[str, Any]:
    """The most recently updated pages across every space you can read.

    A quick way to see what has been worked on lately without browsing space by
    space.
    """
    data = await _call(kb().get("/documents/recent", params={"limit": limit}))
    return {"pages": [fmt.page_summary(d) for d in data["data"]]}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@mcp.tool
async def get_page(
    page_id: Annotated[str, Field(description="Page id")],
    as_html: Annotated[
        bool,
        Field(
            description="Return the stored HTML instead of plain text. Use this "
            "when you intend to edit and want to preserve formatting."
        ),
    ] = False,
) -> dict[str, Any]:
    """Read one page in full.

    Plain text by default, which is what you want for reading or answering
    questions. Ask for HTML when you are about to edit the page and want to keep
    its headings, lists and tables intact.

    Works for your own pages, pages shared with you, and pages shared by link -
    the last of those even when they belong to somebody else entirely.
    """
    doc_id = _ident(page_id, "page id")
    try:
        doc = await kb().get(f"/documents/{doc_id}")
    except KnowledgeBaseError as exc:
        if exc.status != 404:
            raise _fail(exc) from exc
        # Not yours, or not there at all. A page shared by link is readable by
        # anyone, so try that before reporting it missing.
        try:
            public = await kb().get(f"/public/documents/{doc_id}")
        except KnowledgeBaseError:
            raise _fail(exc) from exc
        return fmt.public_page(public)
    return fmt.page_full(doc, as_html=as_html)


@mcp.tool
async def search_pages(
    query: Annotated[str, Field(min_length=1, description="What to look for")],
    space_id: Annotated[
        str | None, Field(description="Restrict to one space; omit to search all")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=25, description="How many pages")] = 8,
    keyword: Annotated[
        bool, Field(description="Use exact-word (BM25) matching")
    ] = True,
    semantic: Annotated[
        bool, Field(description="Use meaning-based (vector) matching")
    ] = True,
) -> dict[str, Any]:
    """Find pages, by exact words and by meaning at once.

    Both methods run by default and their rankings are merged, which is usually
    what you want: keyword matching catches names, error codes and identifiers,
    while semantic matching catches a question phrased differently from the page.

    Turn one off deliberately:
    - `semantic=false` when you need a literal string and nothing like it.
    - `keyword=false` when the wording certainly differs from the page.

    Each result carries `matched_by`, showing which methods found it - a page
    several methods agree on is a stronger answer than one only one method liked.

    When `searched_with.reranked` is true, a model has read your query against
    each of the top results and put the best first, so trust the order: the first
    result is the most likely answer, not merely the most keyword-dense one.

    Returns pages, not answers. For a written answer use `ask_knowledge_base`.
    """
    if not keyword and not semantic:
        raise ToolError("Enable at least one of keyword or semantic matching.")
    params: dict[str, Any] = {
        "q": query,
        "limit": limit,
        "bm25": str(keyword).lower(),
        "vector": str(semantic).lower(),
    }
    if space_id:
        params["namespace_id"] = space_id
    data = await _call(kb().get("/search/retrieve", params=params))
    return {
        "query": query,
        "results": [fmt.search_hit(h) for h in data["data"]],
        "count": data["count"],
        "searched_with": {
            "keyword": data.get("used_bm25"),
            "semantic": data.get("used_vector"),
            "reranked": data.get("used_rerank"),
        },
    }


@mcp.tool
async def ask_knowledge_base(
    question: Annotated[
        str, Field(min_length=1, description="A question in plain language")
    ],
    space_id: Annotated[
        str | None, Field(description="Restrict to one space; omit to use all")
    ] = None,
    pages_to_read: Annotated[
        int,
        Field(
            ge=1,
            le=25,
            description=(
                "How many pages to shortlist. The best three of those are read "
                "in full and the answer is written from them, so raising this "
                "widens the search rather than lengthening the answer."
            ),
        ),
    ] = 10,
) -> dict[str, Any]:
    """Answer a question from the knowledge base, with citations.

    This searches, reads the best pages, and writes an answer grounded in them.
    The answer cites its sources as `[1]`, `[2]` matching the `citations` list,
    so every claim can be traced to a page.

    It answers only from what is stored. When the knowledge base has nothing on
    the topic it says so rather than guessing - treat that as a real answer, not
    a failure.

    `pages_considered` is how many pages the search turned up; `pages_used` is
    how many the answer was actually written from. The gap is not a problem: the
    shortlist is deliberately wide, and a reranker then keeps the pages that
    genuinely address the question.

    Slower than `search_pages` (several seconds), because a model writes the
    reply. Use `search_pages` when you only need to locate pages.
    """
    body: dict[str, Any] = {"q": question, "top_k": pages_to_read}
    if space_id:
        body["namespace_id"] = space_id
    data = await _call(
        kb().post("/ask/", json=body, timeout=settings.KB_LONG_TIMEOUT_SECONDS)
    )
    return {
        "question": question,
        "answer": data["answer"],
        "citations": [fmt.citation(c) for c in data.get("citations", [])],
        "pages_considered": data.get("searched", 0),
        "pages_used": data.get("used", 0),
        "reranked": data.get("reranked", False),
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


@mcp.tool
async def list_page_types() -> dict[str, Any]:
    """The kinds of page this knowledge base uses, most-used first.

    Types are free text, not a fixed set, so this is the way to find out what
    vocabulary is already in use. Reuse one that fits rather than inventing a
    near-duplicate: "Invoice" and "Invoices" become two categories that have to
    be searched separately forever.

    Entries with a count of zero are common suggestions nobody has used here yet.
    """
    data = await _call(kb().get("/documents/types"))
    return {
        "types": [
            {"name": t["name"], "pages": t.get("count", 0)} for t in data["data"]
        ],
        "note": "Any short string is a valid type; these are what is already in use.",
    }


@mcp.tool
async def create_page(
    title: Annotated[
        str, Field(min_length=1, max_length=300, description="Page title")
    ],
    content: Annotated[str, Field(description="The body, in the chosen format")],
    space_id: Annotated[str, Field(description="Space id from list_spaces")],
    folder_id: Annotated[
        str | None,
        Field(description="Folder id from browse_space; omit for the space root"),
    ] = None,
    format: Annotated[
        Literal["markdown", "text", "html"],
        Field(description="How to interpret `content`"),
    ] = "markdown",
    doc_type: Annotated[
        str | None,
        Field(
            max_length=60,
            description=(
                "What kind of page this is - Letter, Invoice, Report, Runbook. "
                "Free text: call list_page_types to see what this knowledge base "
                "already uses, and reuse an existing one where it fits rather "
                "than inventing a near-duplicate."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Create a page.

    `format` decides how the body is read:
    - **markdown** (recommended): headings, lists, tables, bold, code and links
      all survive into the page.
    - **text**: kept as plain paragraphs, nothing interpreted.
    - **html**: for content you have already marked up. It is sanitised, so
      scripts and event handlers are stripped while structure is kept.

    Requires editor access to the space. The page becomes searchable a short
    while after it is created - the returned `indexing` says where it is up to.
    """
    doc = await _call(
        kb().post(
            "/documents/",
            json={
                "namespace_id": space_id,
                "folder_id": folder_id,
                "title": title,
                "content": content,
                "content_format": format,
                "doc_type": doc_type,
            },
        )
    )
    return {
        "created": fmt.page_summary(doc),
        "note": "Searchable once indexing reports ready; use wait_for_indexing to block on it.",
    }


@mcp.tool
async def update_page(
    page_id: Annotated[str, Field(description="Page id")],
    content: Annotated[
        str | None,
        Field(description="New body. Omit to rename without touching the content."),
    ] = None,
    title: Annotated[str | None, Field(description="New title")] = None,
    format: Annotated[
        Literal["markdown", "text", "html"],
        Field(description="How to interpret `content`"),
    ] = "markdown",
    mode: Annotated[
        Literal["replace", "append"],
        Field(description="Replace the whole body, or add to the end of it"),
    ] = "replace",
    doc_type: Annotated[
        str | None,
        Field(
            max_length=60,
            description=(
                "Change what kind of page this is. Pass an empty string to "
                "remove the type; omit the argument to leave it as it is."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Change a page's content, title or type.

    `mode="append"` reads the current page and adds your content to the end,
    which is the safe choice when you are adding a note to something a person
    wrote. `mode="replace"` overwrites the body entirely.

    Editing the body re-indexes the page, so it is briefly out of date for
    search. Formatting-only changes, and changing only the type, do not bump the
    version or trigger re-indexing.
    """
    if content is None and title is None and doc_type is None:
        raise ToolError(
            "Give a new `content`, a new `title`, a new `doc_type`, or several."
        )

    api = kb()
    doc_id = _ident(page_id, "page id")
    payload: dict[str, Any] = {}

    if content is not None:
        if mode == "append":
            current = await _call(api.get(f"/documents/{doc_id}"))
            # Appending happens in HTML because that is what is stored; the new
            # part is converted first so markdown still renders.
            addition = content
            if format != "html":
                converted = await _call(
                    api.post(
                        "/documents/",
                        json={
                            "namespace_id": current["namespace_id"],
                            "title": "__append_scratch__",
                            "content": content,
                            "content_format": format,
                        },
                    )
                )
                # The scratch page exists only to be converted, and it is in the
                # caller's own space, so it has to go whatever happens next -
                # otherwise a bad conversion leaves "__append_scratch__" behind
                # permanently, indexed and searchable.
                try:
                    if "content_html" not in converted:
                        raise ToolError(
                            "The Knowledge Base did not return converted HTML "
                            "for the appended content, so nothing was changed."
                        )
                    addition = converted["content_html"]
                finally:
                    scratch = str(converted.get("id", ""))
                    if _SAFE_ID.match(scratch):
                        # A failure to tidy up must not replace the real error.
                        with contextlib.suppress(KnowledgeBaseError):
                            await api.delete(f"/documents/{scratch}")
            payload["content"] = (current.get("content_html") or "") + addition
            payload["content_format"] = "html"
        else:
            payload["content"] = content
            payload["content_format"] = format

    if title is not None:
        payload["title"] = title
    if doc_type is not None:
        # Sent through as given: the empty string is how the API is told to
        # clear it, which is why omitting the argument is a different thing.
        payload["doc_type"] = doc_type

    doc = await _call(api.put(f"/documents/{doc_id}", json=payload))
    return {"updated": fmt.page_summary(doc)}


@mcp.tool
async def create_folder(
    name: Annotated[
        str, Field(min_length=1, max_length=200, description="Folder name")
    ],
    space_id: Annotated[str, Field(description="Space id")],
    parent_folder_id: Annotated[
        str | None,
        Field(description="Nest inside this folder; omit for the space root"),
    ] = None,
) -> dict[str, Any]:
    """Create a folder in a space.

    Check `browse_space` first - a folder for this purpose may already exist, and
    duplicates make the tree harder to use rather than easier.
    """
    f = await _call(
        kb().post(
            "/folders/",
            json={
                "namespace_id": space_id,
                "name": name,
                "parent_id": parent_folder_id,
            },
        )
    )
    return {"created": fmt.folder(f)}


@mcp.tool
async def move_page(
    page_id: Annotated[str, Field(description="Page id")],
    space_id: Annotated[
        str | None,
        Field(description="Move to this space; omit to keep the current one"),
    ] = None,
    folder_id: Annotated[
        str | None,
        Field(description="Move into this folder; null moves it to the space root"),
    ] = None,
) -> dict[str, Any]:
    """Move a page to another folder, or to another space entirely.

    Give at least one destination. Passing a `space_id` with no `folder_id`
    puts the page at the root of that space, which is also how to take a page
    out of its folder without moving it anywhere else: pass the space it is
    already in.

    You need editor access to the destination space.
    """
    if space_id is None and folder_id is None:
        raise ToolError(
            "Give a destination: a `space_id`, a `folder_id`, or both. Passing "
            "neither would move the page to the root of its space, which is "
            "unlikely to be what was meant."
        )
    body: dict[str, Any] = {}
    if space_id is not None:
        body["namespace_id"] = space_id
    body["folder_id"] = folder_id
    doc = await _call(
        kb().post(f"/documents/{_ident(page_id, 'page id')}/move", json=body)
    )
    return {"moved": fmt.page_summary(doc)}


@mcp.tool
async def delete_page(
    page_id: Annotated[str, Field(description="Page id")],
) -> dict[str, Any]:
    """Delete a page permanently.

    There is no undo and no recycle bin. Read the page first if there is any
    doubt about which one this is.
    """
    doc_id = _ident(page_id, "page id")
    doc = await _call(kb().get(f"/documents/{doc_id}"))
    await _call(kb().delete(f"/documents/{doc_id}"))
    return {"deleted": {"id": page_id, "title": doc.get("title")}}


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------


@mcp.tool
async def list_shared_with_you() -> dict[str, Any]:
    """Pages other people have shared with this account, and spaces you joined.

    These are not yours. Editing one changes what its owner sees, and it can be
    taken away at any time - which is why `clone_page` exists.
    """
    data = await _call(kb().get("/documents/shared-with-me"))
    return {
        "pages": [fmt.page_summary(d) for d in data.get("documents", [])],
        "spaces": [fmt.space(n) for n in data.get("namespaces", [])],
    }


@mcp.tool
async def list_people_with_access(
    page_id: Annotated[str, Field(description="Page id")],
) -> dict[str, Any]:
    """Who can see this page: accounts it is shared with, and who is only invited.

    An invitation is not access yet - it becomes access when that address is
    confirmed on an account.
    """
    api = kb()
    doc_id = _ident(page_id, "page id")
    shares = await _call(api.get(f"/documents/{doc_id}/shares"))
    invitations = await _call(api.get(f"/documents/{doc_id}/invitations"))
    page = await _call(api.get(f"/documents/{doc_id}"))
    out: dict[str, Any] = {
        "people": [fmt.share(s) for s in shares.get("data", [])],
        "invited": [fmt.invitation(i) for i in invitations],
    }
    if page.get("public_slug"):
        out["public_link"] = f"{_public_base()}/p/{page['public_slug']}"
    return out


@mcp.tool
async def share_page(
    page_id: Annotated[str, Field(description="Page id")],
    emails: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=50,
            description="Email addresses to share the page with",
        ),
    ],
    can_edit: Annotated[
        bool, Field(description="Let them change the page, not only read it")
    ] = False,
    message: Annotated[
        str | None,
        Field(
            max_length=1000,
            description="A note from you, included in the email they receive",
        ),
    ] = None,
) -> dict[str, Any]:
    """Share a page with people, by email address.

    Addresses that already have an account get access immediately and an email
    saying so. Addresses that do not are **invited**: still no access, but an
    email telling them who shared what, with a link to create an account. The
    invitation becomes access once that address is confirmed.

    The reply separates `shared`, `invited` and `skipped`, so you can tell the
    person what actually happened rather than guessing. `skipped` explains each
    one - already had access, your own address, or the page's limit reached.

    Sharing is per page. It does not give anyone the space around it.
    """
    body: dict[str, Any] = {
        "emails": emails,
        "role": "editor" if can_edit else "viewer",
    }
    if message:
        body["message"] = message
    data = await _call(
        kb().post(f"/documents/{_ident(page_id, 'page id')}/shares/batch", json=body)
    )
    return {
        "shared": [fmt.share(s) for s in data.get("shared", [])],
        "invited": [fmt.invitation(i) for i in data.get("invited", [])],
        "skipped": data.get("skipped", []),
        "people_with_access": data.get("recipients"),
        "limit": data.get("max_recipients"),
    }


@mcp.tool
async def share_space(
    space_id: Annotated[str, Field(description="Space id from list_spaces")],
    emails: Annotated[
        list[str],
        Field(min_length=1, max_length=50, description="Email addresses to invite"),
    ],
    role: Annotated[
        Literal["viewer", "editor", "admin"],
        Field(description="What they may do in the space"),
    ] = "viewer",
    message: Annotated[
        str | None,
        Field(max_length=1000, description="A note from you, included in the email"),
    ] = None,
) -> dict[str, Any]:
    """Share a whole space with people, by email address.

    **This is a much bigger grant than `share_page`.** It covers everything in
    the space, including pages added later. Say so before doing it, and prefer
    sharing individual pages when that is what was actually asked for.

    Roles: `viewer` reads, `editor` reads and changes, `admin` can also share the
    space onwards. Addresses with no account are invited by email and join when
    that address is confirmed.

    Requires admin on the space.
    """
    body: dict[str, Any] = {"emails": emails, "role": role}
    if message:
        body["message"] = message
    data = await _call(
        kb().post(
            f"/namespaces/{_ident(space_id, 'space id')}/members/batch", json=body
        )
    )
    return {
        "shared": [fmt.share(m) for m in data.get("shared", [])],
        "invited": [fmt.invitation(i) for i in data.get("invited", [])],
        "skipped": data.get("skipped", []),
        "people_in_space": data.get("members"),
        "limit": data.get("max_members"),
    }


@mcp.tool
async def list_space_members(
    space_id: Annotated[str, Field(description="Space id")],
) -> dict[str, Any]:
    """Who is in a space, and who has been invited but not joined yet."""
    api = kb()
    space = _ident(space_id, "space id")
    members = await _call(api.get(f"/namespaces/{space}/members"))
    invitations = await _call(api.get(f"/namespaces/{space}/invitations"))
    return {
        "members": [fmt.share(m) for m in members.get("data", [])],
        "invited": [fmt.invitation(i) for i in invitations],
    }


@mcp.tool
async def remove_from_space(
    space_id: Annotated[str, Field(description="Space id")],
    user_id: Annotated[str, Field(description="User id from list_space_members")],
) -> dict[str, Any]:
    """Remove somebody from a space. They lose everything in it immediately."""
    await _call(
        kb().delete(
            f"/namespaces/{_ident(space_id, 'space id')}"
            f"/members/{_ident(user_id, 'user id')}"
        )
    )
    return {"removed": user_id, "space_id": space_id}


@mcp.tool
async def unshare_page(
    page_id: Annotated[str, Field(description="Page id")],
    user_id: Annotated[str, Field(description="User id from list_people_with_access")],
) -> dict[str, Any]:
    """Take away one person's access to a page. They lose it immediately."""
    await _call(
        kb().delete(
            f"/documents/{_ident(page_id, 'page id')}"
            f"/shares/{_ident(user_id, 'user id')}"
        )
    )
    return {"removed": user_id, "page_id": page_id}


@mcp.tool
async def share_page_by_link(
    page_id: Annotated[str, Field(description="Page id")],
) -> dict[str, Any]:
    """Make a page readable by anyone who has its link.

    **No sign-in, no account, no limit on who passes it on.** Use it for
    something meant to be public, and prefer `share_page` for anything that
    should reach named people only.

    Calling it twice returns the same link. `stop_sharing_by_link` withdraws it,
    and sharing again afterwards produces a different link, so the old one stays
    dead.
    """
    data = await _call(kb().post(f"/documents/{_ident(page_id, 'page id')}/public"))
    return {
        "url": data["url"],
        "slug": data["slug"],
        "warning": "Anyone with this link can read the page without signing in.",
    }


@mcp.tool
async def stop_sharing_by_link(
    page_id: Annotated[str, Field(description="Page id")],
) -> dict[str, Any]:
    """Withdraw a page's public link. Anyone still holding it gets nothing."""
    await _call(kb().delete(f"/documents/{_ident(page_id, 'page id')}/public"))
    return {"page_id": page_id, "public": False}


@mcp.tool
async def read_public_page(
    link_or_id: Annotated[
        str,
        Field(
            description=(
                "A public link (https://…/p/abc123), its slug, or the page id "
                "of a page that is shared by link"
            )
        ),
    ],
) -> dict[str, Any]:
    """Read a page somebody shared by link, whether or not it belongs to you.

    This is the one way to read a page outside your own knowledge base, and it
    works only while that page is actually shared by link. A page that was never
    published, or whose link was withdrawn, is not found here.
    """
    identifier = link_or_id.strip().rstrip("/").rsplit("/", 1)[-1]
    if not identifier:
        raise ToolError("That does not look like a link or an id.")
    identifier = _ident(identifier, "public link or id")
    return fmt.public_page(await _call(kb().get(f"/public/documents/{identifier}")))


@mcp.tool
async def clone_page(
    page_id: Annotated[str, Field(description="Page id to copy")],
    space_id: Annotated[str, Field(description="Space to copy it into")],
    folder_id: Annotated[
        str | None, Field(description="Folder inside that space")
    ] = None,
    title: Annotated[
        str | None,
        Field(max_length=300, description="Title for the copy; defaults to '… (copy)'"),
    ] = None,
) -> dict[str, Any]:
    """Copy a page you can read into a space you can write to.

    The copy is yours. It is a separate page from the moment it exists: editing
    it does not touch the original, the original's shares do not follow it, and
    nobody else can see it until you share it yourself.

    This is how to keep something that was shared with you - a page shared by
    somebody else can be changed or withdrawn at any time, and a copy cannot.
    Images inside the page are copied too, so the copy is not left pointing at
    files only the original's owner can read.
    """
    body: dict[str, Any] = {"namespace_id": space_id}
    if folder_id:
        body["folder_id"] = folder_id
    if title:
        body["title"] = title
    doc = await _call(
        kb().post(f"/documents/{_ident(page_id, 'page id')}/clone", json=body)
    )
    return {
        "created": fmt.page_summary(doc),
        "note": "This copy is yours and private until you share it.",
    }


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

# What the API accepts in one batch; refusing here means a rejected call costs
# no memory rather than decoding every file first only to be told no.
MAX_UPLOAD_FILES = 20
# The API's own per-file ceiling. Matching it keeps the refusal local.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_FETCH_BYTES = MAX_UPLOAD_BYTES
# A handful of hops is a real redirect chain; more is a loop or a trick.
MAX_FETCH_REDIRECTS = 3


def _is_reachable_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether an address belongs to the internet rather than to this network.

    This server runs beside the Knowledge Base, its database, its object store
    and an unauthenticated vector store, and on a cloud host the link-local
    address answers with instance credentials. A URL the caller chose must not
    be able to reach any of them.
    """
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _check_fetch_target(url: httpx.URL) -> None:
    if url.scheme not in ("http", "https"):
        raise ToolError(
            "Only http and https URLs can be fetched. Send the file as "
            "`content_base64` instead."
        )
    host = url.host
    if not host:
        raise ToolError("That URL has no host.")

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (OSError, socket.gaierror) as exc:
            raise ToolError(f"Could not look up {host}.") from exc
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                # An address this process cannot even parse is not one it
                # should be dialling.
                raise ToolError(f"Could not look up {host}.") from None

    if not addresses or not all(_is_reachable_address(a) for a in addresses):
        raise ToolError(
            f"{host} is inside the network this server runs in, so it will not "
            "be fetched. Use a public http(s) URL, or send the file as "
            "`content_base64`."
        )


async def _fetch_file(url: str) -> bytes:
    """Fetch a caller-supplied URL, with every hop checked and a size ceiling.

    Redirects are followed by hand because the check has to run again on each
    one: validating only the URL the caller typed is no protection when the
    server it names answers with a redirect to an internal address.
    """
    target = httpx.URL(url)
    async with httpx.AsyncClient(
        timeout=settings.KB_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        for _ in range(MAX_FETCH_REDIRECTS + 1):
            await _check_fetch_target(target)
            async with client.stream("GET", target) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ToolError(f"{target} redirected to nowhere.")
                    target = target.join(location)
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_FETCH_BYTES:
                        raise ToolError(
                            f"That file is too large: more than "
                            f"{MAX_FETCH_BYTES // (1024 * 1024)} MB."
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
    raise ToolError(f"{url} redirected too many times.")


@mcp.tool
async def upload_document(
    filename: Annotated[
        str,
        Field(
            description=(
                "File name with its extension, e.g. invoice.pdf. With several "
                "files this names the first; the rest are numbered after it."
            )
        ),
    ],
    space_id: Annotated[str, Field(description="Space the new page should live in")],
    content_base64: Annotated[
        str | None, Field(description="File bytes, base64 encoded")
    ] = None,
    files_base64: Annotated[
        list[str] | None,
        Field(
            max_length=MAX_UPLOAD_FILES,
            description=(
                "Several files at once, base64 encoded, in reading order. Each "
                "becomes its own page unless `combine` is true. At most "
                "20 per call."
            ),
        ),
    ] = None,
    filenames: Annotated[
        list[str] | None,
        Field(description="Names for `files_base64`, in the same order"),
    ] = None,
    combine: Annotated[
        bool,
        Field(
            description=(
                "Make one page out of every file instead of one page each. Use "
                "it when the files are parts of a single document."
            )
        ),
    ] = False,
    doc_type: Annotated[
        str | None,
        Field(
            max_length=60,
            description=(
                "What kind of document this is - Invoice, Letter, Contract. "
                "Applies to every file in the upload."
            ),
        ),
    ] = None,
    url: Annotated[
        str | None,
        Field(
            description=(
                "Or fetch the file from this public http(s) URL instead. "
                "Addresses inside this server's own network are refused."
            )
        ),
    ] = None,
    title: Annotated[
        str | None,
        Field(description="Page title; by default the document's own heading is used"),
    ] = None,
    folder_id: Annotated[
        str | None, Field(description="Folder to put the page in")
    ] = None,
    prompt: Annotated[
        str | None,
        Field(
            description=(
                "Extra instruction for the model reading the file, e.g. 'keep the "
                "tables as tables' or 'this is a handwritten note'. Optional."
            )
        ),
    ] = None,
    wait: Annotated[
        bool, Field(description="Wait for parsing to finish and return the page")
    ] = True,
) -> dict[str, Any]:
    """Turn a PDF or an image into a normal, editable page.

    A vision model reads the file - including scans and photographs - and
    reproduces its headings, paragraphs, lists and tables as page content. The
    original file stays attached to the page and can be downloaded later.

    Supply the file either as `content_base64` or as a public `url` to fetch.
    Accepted types: PDF, PNG, JPEG, WebP, GIF, TIFF, BMP; up to 50 MB each and
    20 files per call.

    For several files use `files_base64` with matching `filenames`. By default
    each becomes its own page, named after its own content; with `combine=true`
    they become a single page in the order given, which is what a document that
    arrived as a set of scans needs.

    Leave `title` empty to have each page named after its own opening heading,
    which is almost always better than the filename.

    Parsing takes roughly a few seconds per page. With `wait=true` this returns
    the finished page; with `wait=false` it returns an `import_id` to poll with
    `check_import`.
    """
    sources = [bool(content_base64), bool(files_base64), bool(url)]
    if not any(sources):
        raise ToolError("Provide `content_base64`, `files_base64`, or `url`.")
    if sum(sources) > 1:
        raise ToolError(
            "Provide one of `content_base64`, `files_base64` or `url`, not several."
        )
    if files_base64 and filenames and len(filenames) != len(files_base64):
        raise ToolError(
            f"{len(files_base64)} files but {len(filenames)} filenames: they must match."
        )
    payloads: list[bytes] = []
    names: list[str] = []

    if url:
        try:
            payloads = [await _fetch_file(url)]
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not fetch {url}: {exc}") from exc
        names = [filename]
    elif files_base64:
        for i, encoded in enumerate(files_base64):
            _check_encoded_size(encoded, f"File {i + 1} in `files_base64`")
            try:
                payloads.append(base64.b64decode(encoded, validate=True))
            except (binascii.Error, ValueError) as exc:
                raise ToolError(
                    f"File {i + 1} in `files_base64` is not valid base64."
                ) from exc
        names = list(filenames) if filenames else _numbered(filename, len(payloads))
    else:
        _check_encoded_size(content_base64 or "", "`content_base64`")
        try:
            payloads = [base64.b64decode(content_base64 or "", validate=True)]
        except (binascii.Error, ValueError) as exc:
            raise ToolError("`content_base64` is not valid base64.") from exc
        names = [filename]

    for i, body in enumerate(payloads):
        if not body:
            raise ToolError(f"File {i + 1} ({names[i]}) is empty.")

    form: dict[str, Any] = {"namespace_id": space_id}
    if folder_id:
        form["folder_id"] = folder_id
    if title:
        form["title"] = title
    if prompt:
        form["prompt"] = prompt
    if doc_type:
        form["doc_type"] = doc_type

    if len(payloads) == 1 and not files_base64:
        job = await _call(
            kb().post(
                "/imports/",
                data=form,
                files={"file": (names[0], payloads[0], _guess_type(names[0]))},
                timeout=settings.KB_LONG_TIMEOUT_SECONDS,
            )
        )
        jobs = [job]
    else:
        form["combine"] = str(bool(combine)).lower()
        data = await _call(
            kb().post(
                "/imports/batch",
                data=form,
                files=[
                    ("files", (name, body, _guess_type(name)))
                    for name, body in zip(names, payloads, strict=True)
                ],
                timeout=settings.KB_LONG_TIMEOUT_SECONDS,
            )
        )
        jobs = data["data"]

    if not wait:
        return {
            "imports": [fmt.import_job(j) for j in jobs],
            "note": "Poll each with check_import.",
        }

    # One deadline for the whole call, not one per file: twenty files each
    # allowed the full wait would hold this request for hours.
    deadline = time.monotonic() + settings.MAX_WAIT_SECONDS
    finished = [await _await_import(j["id"], deadline) for j in jobs]
    unfinished = [j for j in finished if j["status"] != "done"]
    pages = []
    for job in finished:
        if job["status"] == "done" and job.get("document_id"):
            doc = await _call(kb().get(f"/documents/{job['document_id']}"))
            pages.append(
                {
                    **fmt.page_summary(doc),
                    "preview": fmt.plain(doc.get("content_html", ""), 400),
                }
            )

    out: dict[str, Any] = {"created": pages, "originals_kept": True}
    if unfinished:
        out["failed"] = [fmt.import_job(j) for j in unfinished]
        out["note"] = "Some files did not parse; retry_import can try again."
    return out


# ---------------------------------------------------------------------------
# Getting the originals back out
# ---------------------------------------------------------------------------

# A channel will refuse a very large attachment anyway, and the file has to pass
# through a tool result to get there, so cap what can be pulled back in one go.
MAX_FETCH_BYTES = 25 * 1024 * 1024


@mcp.tool
async def list_page_files(
    page_id: Annotated[str, Field(description="Page id")],
) -> dict[str, Any]:
    """The original files attached to a page: the scans, PDFs and photographs it was made from.

    A page created by `upload_document` keeps whatever was uploaded. Use this to
    find out what is available before fetching it - the reply gives each file's
    id, name, type and size, and size is worth checking because a channel will
    reject a large attachment.

    Use it when someone asks for a document itself rather than for what it says:
    "send me the tenancy agreement", "forward that invoice".
    """
    doc_id = _ident(page_id, "page id")
    try:
        data = await kb().get("/attachments/", params={"document_id": doc_id})
    except KnowledgeBaseError as exc:
        raise _fail(exc) from exc
    files = [
        {
            "file_id": row["id"],
            "filename": row["filename"],
            "content_type": row["content_type"],
            "size_bytes": row["size"],
        }
        for row in data.get("data", [])
    ]
    if not files:
        return {
            "files": [],
            "note": "This page has no attached originals. It was probably written "
            "rather than uploaded, so the page text is all there is.",
        }
    return {"files": files}


@mcp.tool
async def get_page_file(
    file_id: Annotated[
        str, Field(description="File id, from list_page_files")
    ],
) -> Any:
    """Fetch one original file so it can be passed on to the person who asked.

    Returns the file itself, not a link. Your client saves it somewhere local
    and tells you the path; send that file on however your channel does it.

    Fetch a file only when someone wants the document itself. To answer a
    question about what a document *says*, use `ask_knowledge_base` or
    `get_page` - they are far cheaper than moving the bytes around.
    """
    attachment_id = _ident(file_id, "file id")
    try:
        meta = await kb().get(f"/attachments/{attachment_id}")
    except KnowledgeBaseError as exc:
        raise _fail(exc) from exc

    size = int(meta.get("size") or 0)
    if size > MAX_FETCH_BYTES:
        raise ToolError(
            f"{meta.get('filename', 'That file')} is "
            f"{size // (1024 * 1024)} MB, over the {MAX_FETCH_BYTES // (1024 * 1024)} MB "
            "limit for sending a file back. Tell the person it is too large to "
            "send and point them at the page in PlusGPT instead."
        )

    try:
        content = await kb().get(
            f"/attachments/{attachment_id}/download", raw=True
        )
    except KnowledgeBaseError as exc:
        raise _fail(exc) from exc

    return File(
        data=content,
        name=str(meta.get("filename") or "document"),
        format=_format_hint(str(meta.get("content_type") or "")),
    )


def _format_hint(content_type: str) -> str | None:
    """Turn a MIME type into the short format tag ``File`` expects.

    ``File`` builds ``application/<format>``, which is right for pdf and wrong
    for images, so hand it nothing when the guess would be worse than the
    extension already on the name.
    """
    subtype = content_type.partition("/")[2].strip().lower()
    return subtype if content_type.startswith("application/") and subtype else None


def _check_encoded_size(encoded: str, what: str) -> None:
    """Refuse an oversized payload before it is decoded.

    Base64 is four characters per three bytes, so the encoded length says how
    big the file is without materialising it.
    """
    if len(encoded) > (MAX_UPLOAD_BYTES // 3 + 1) * 4:
        raise ToolError(
            f"{what} is too large: the limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )


def _numbered(filename: str, count: int) -> list[str]:
    """Names for files that were sent without any, kept in order."""
    stem, _, suffix = filename.rpartition(".")
    if not stem:
        stem, suffix = filename, "bin"
    return [f"{stem}-{i + 1}.{suffix}" for i in range(count)]


def _guess_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


async def _await_import(import_id: str, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        job = await _call(kb().get(f"/imports/{import_id}"))
        if job["status"] in ("done", "failed", "cancelled"):
            return job  # type: ignore[no-any-return]
        await asyncio.sleep(3)
    return await _call(kb().get(f"/imports/{import_id}"))  # type: ignore[no-any-return]


@mcp.tool
async def check_import(
    import_id: Annotated[str, Field(description="Import id from upload_document")],
) -> dict[str, Any]:
    """How an upload is progressing, and the page id once it is done."""
    job = await _call(kb().get(f"/imports/{_ident(import_id, 'import id')}"))
    return fmt.import_job(job)


@mcp.tool
async def list_imports(
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """List your recent file uploads and what became of them.

    Each entry shows the filename, its status, how many pages were parsed and the
    id of the page that was created. Use this to find an upload you started
    earlier, to spot ones that failed, or to recover a page id you did not keep.
    """
    data = await _call(kb().get("/imports/", params={"limit": limit}))
    return {"imports": [fmt.import_job(j) for j in data["data"]]}


@mcp.tool
async def retry_import(
    import_id: Annotated[str, Field(description="Import id")],
) -> dict[str, Any]:
    """Retry a failed or cancelled upload without sending the file again.

    The original file is still stored, so this re-runs parsing only. Use it after
    `check_import` or `list_imports` reports a failure; a successful retry creates
    the page as the first attempt would have.
    """
    job = await _call(kb().post(f"/imports/{_ident(import_id, 'import id')}/retry"))
    return fmt.import_job(job)


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


@mcp.tool
async def check_indexing(
    page_id: Annotated[str, Field(description="Page id")],
) -> dict[str, Any]:
    """Whether a page is searchable yet.

    A page is indexed shortly after it is written or edited. Until that finishes
    it can be read directly but will not appear in `search_pages` or be used by
    `ask_knowledge_base`.
    """
    doc_id = _ident(page_id, "page id")
    return fmt.indexing(await _call(kb().get(f"/documents/{doc_id}/embeddings")))


@mcp.tool
async def wait_for_indexing(
    page_id: Annotated[str, Field(description="Page id")],
    timeout_seconds: Annotated[
        int, Field(ge=5, le=540, description="Give up after this long")
    ] = 180,
) -> dict[str, Any]:
    """Block until a page is searchable, then return its indexing state.

    Use after writing a page you are about to search for or ask about. Returns
    as soon as it is ready, or when the timeout is reached - check `searchable`
    on the result rather than assuming.
    """
    api = kb()
    doc_id = _ident(page_id, "page id")
    deadline = time.monotonic() + timeout_seconds
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = await _call(api.get(f"/documents/{doc_id}/embeddings"))
        if status.get("embedding_status") in ("ready", "failed") and not status.get(
            "is_stale"
        ):
            break
        await asyncio.sleep(3)
    return fmt.indexing(status)


@mcp.tool
async def reindex_page(
    page_id: Annotated[str, Field(description="Page id")],
) -> dict[str, Any]:
    """Index a page again.

    Worth doing only when indexing previously failed, or a page looks stale in
    search. Ordinary edits re-index by themselves.
    """
    await _call(
        kb().post(f"/documents/{_ident(page_id, 'page id')}/embeddings/regenerate")
    )
    return {"queued": True, "page_id": page_id}


def main() -> None:
    mcp.run(
        transport="http",
        host=settings.HOST,
        port=settings.PORT,
        path=settings.MCP_PATH,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
