"""Shaping API responses into something an agent can use without wading.

The REST API returns everything about a resource. An agent needs the few fields
it will act on, plus the ids it needs to make the next call, so these helpers
drop the rest. Every returned object keeps its ``id`` for exactly that reason.
"""

from __future__ import annotations

import re
from typing import Any

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def plain(html: str, limit: int | None = None) -> str:
    """Strip markup for previews. Not for storage - the API keeps the HTML."""
    text = _WS.sub(" ", _TAG.sub(" ", html or "")).strip()
    if limit and len(text) > limit:
        return text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def space(ns: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": ns["id"],
        "name": ns["name"],
        "slug": ns.get("slug"),
        "description": ns.get("description"),
        "your_role": ns.get("my_role"),
        "pages": ns.get("document_count", 0),
    }
    # Somebody else's space, shared with this account. Worth saying: writing
    # into it puts content in another person's knowledge base.
    if ns.get("shared_with_you"):
        out["shared_with_you"] = True
    return out


def folder(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f["id"],
        "name": f["name"],
        "parent_folder_id": f.get("parent_id"),
    }


def page_summary(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": doc["id"],
        "title": doc["title"],
        # What kind of thing this is: Letter, Invoice, Runbook. Free text, so
        # treat it as a label rather than a value from a fixed set.
        "type": doc.get("doc_type"),
        "space_id": doc.get("namespace_id"),
        "folder_id": doc.get("folder_id"),
        "updated_at": doc.get("updated_at"),
        "version": doc.get("version"),
        "indexing": doc.get("embedding_status"),
        "searchable": doc.get("embedding_status") == "ready"
        and not doc.get("is_stale", False),
    }
    # Whether this page is the caller's own or somebody else's, and what they
    # may do with it. An agent about to edit should know which it is: editing
    # a page shared with you changes what its owner sees.
    role = doc.get("my_role")
    if role:
        out["shared_with_you"] = True
        out["your_role"] = role
    if doc.get("public_slug"):
        out["public_link"] = True
    return out


def page_full(doc: dict[str, Any], *, as_html: bool = False) -> dict[str, Any]:
    out = page_summary(doc)
    out["content"] = doc.get("content_html") if as_html else doc.get("content_text")
    out["content_format"] = "html" if as_html else "text"
    if doc.get("summary"):
        out["summary"] = doc["summary"]
    source = doc.get("source_attachment")
    if source:
        out["imported_from"] = {
            "attachment_id": source.get("id"),
            "filename": source.get("filename"),
            "size_bytes": source.get("size"),
        }
    return out


def search_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """One search result, including *why* it matched.

    ``matched_by`` is what lets an agent judge a result: a page found by several
    methods is a stronger answer than one a single method liked.
    """
    return {
        "page_id": hit["document_id"],
        "title": hit["title"],
        "type": hit.get("doc_type"),
        "space": hit.get("namespace_name"),
        "space_id": hit.get("namespace_id"),
        "score": round(float(hit.get("score", 0.0)), 5),
        "snippet": plain(hit.get("snippet", ""), 400),
        "matched_section": hit.get("matched_chunk_title"),
        "matched_by": sorted(
            {f"{s['method']}:{s['target']}" for s in hit.get("sources", [])}
        ),
    }


def citation(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": c["index"],
        "page_id": c["document_id"],
        "title": c["title"],
        "space": c.get("namespace_name"),
        "section": c.get("chunk_title"),
        "used_in_answer": bool(c.get("cited")),
        "excerpt": plain(c.get("text", ""), 600),
    }


def share(row: dict[str, Any]) -> dict[str, Any]:
    user = row.get("user") or {}
    return {
        "user_id": user.get("id"),
        "email": user.get("email"),
        "name": user.get("full_name"),
        "role": row.get("role"),
    }


def invitation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "invitation_id": row.get("id"),
        "email": row.get("email"),
        "role": row.get("role"),
        "expires_at": row.get("expires_at"),
    }


def public_page(doc: dict[str, Any]) -> dict[str, Any]:
    """A page read through a public link, by anyone.

    Deliberately thin, and marked as such, because that is all a public link
    carries: no space, no folder, no neighbours.
    """
    return {
        "page_id": doc.get("id"),
        "title": doc.get("title"),
        "type": doc.get("doc_type"),
        "content": plain(doc.get("content_html", "")),
        "updated_at": doc.get("updated_at"),
        "shared_by": doc.get("shared_by"),
        "access": "public link",
    }


def import_job(job: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "import_id": job["id"],
        "filename": job.get("filename"),
        "status": job.get("status"),
        "pages_parsed": f"{job.get('pages_done', 0)}/{job.get('pages_total', 0)}",
        "page_id": job.get("document_id"),
    }
    if job.get("doc_type"):
        out["type"] = job["doc_type"]
    # Several files becoming one page: worth saying, because the caller gets one
    # page id back for what they sent as many files.
    if (job.get("file_count") or 1) > 1:
        out["files"] = job.get("filenames", [])
    if job.get("error"):
        out["error"] = job["error"]
    return out


def indexing(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": status.get("embedding_status"),
        "searchable": status.get("embedding_status") == "ready"
        and not status.get("is_stale", False),
        "out_of_date": bool(status.get("is_stale")),
        "sections": status.get("chunk_count", 0),
        "summary": status.get("summary"),
        "error": status.get("embedding_error"),
    }
