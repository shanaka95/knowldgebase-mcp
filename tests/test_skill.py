"""The bundled skill is instructions other agents will follow literally.

A skill that names a tool which no longer exists, or omits one that does, sends
an agent down a path that fails. These keep it honest as the server changes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastmcp import Client

SKILL = Path(__file__).resolve().parent.parent / "skills" / "plusgpt"


def skill_text() -> str:
    return "\n".join(p.read_text() for p in sorted(SKILL.rglob("*.md")))


def test_the_skill_exists_where_the_readme_says_it_does() -> None:
    assert (SKILL / "SKILL.md").is_file()
    assert (SKILL / "references" / "files.md").is_file()
    assert (SKILL / "references" / "writing.md").is_file()


def test_the_skill_has_the_frontmatter_a_loader_needs() -> None:
    head = (SKILL / "SKILL.md").read_text().split("---")[1]
    assert re.search(r"^name:\s*plusgpt\s*$", head, re.M)
    description = re.search(r"^description:\s*(.+)$", head, re.M)
    assert description, "a skill with no description is never selected"
    # Long enough to say when it applies, which is how a model picks it.
    assert len(description.group(1)) > 120


async def test_the_skill_mentions_every_tool(client: Client[Any]) -> None:
    missing = [
        t.name for t in await client.list_tools() if f"`{t.name}`" not in skill_text()
    ]
    assert not missing, f"the skill does not mention: {missing}"


async def test_the_skill_names_no_tool_that_does_not_exist(
    client: Client[Any],
) -> None:
    """A skill that tells an agent to call something imaginary wastes a turn."""
    real = {t.name for t in await client.list_tools()}
    prefixes = (
        "list_",
        "get_",
        "create_",
        "update_",
        "delete_",
        "share_",
        "unshare_",
        "upload_",
        "check_",
        "wait_",
        "read_",
        "move_",
        "browse_",
        "recent_",
        "retry_",
        "reindex_",
        "stop_",
        "clone_",
        "remove_",
    )
    named = set(re.findall(r"`([a-z][a-z_]{3,})`", skill_text()))
    invented = {n for n in named if n.startswith(prefixes) and n not in real}
    assert not invented, f"the skill names tools that do not exist: {sorted(invented)}"


async def test_the_skill_only_promises_parameters_upload_document_has(
    client: Client[Any],
) -> None:
    spec = next(t for t in await client.list_tools() if t.name == "upload_document")
    params = set(spec.input_schema.get("properties", {}))
    promised = {
        "filename",
        "space_id",
        "content_base64",
        "files_base64",
        "filenames",
        "combine",
        "doc_type",
        "url",
        "title",
        "folder_id",
        "prompt",
        "wait",
    }
    assert promised <= params, (
        f"the skill promises what does not exist: {promised - params}"
    )


def test_the_skill_states_the_accepted_file_types() -> None:
    """An agent that guesses at this uploads a .docx and gets a refusal."""
    text = skill_text()
    for fmt in ("PDF", "PNG", "JPEG", "WebP", "GIF", "BMP", "TIFF"):
        assert fmt in text, f"{fmt} is accepted but the skill does not say so"


def test_the_skill_says_not_to_parse_files_itself() -> None:
    """The whole point: the server reads the document, the agent does not."""
    # Collapsed, because prose wraps and a line break should not fail this.
    text = re.sub(r"\s+", " ", skill_text().lower())
    assert "do not read it, transcribe it, or convert it yourself" in text
    assert "only parse a document yourself if the user explicitly asks" in text


def test_the_skill_does_not_claim_task_checkboxes_work() -> None:
    """They do not: `- [ ]` renders as literal text, which surprises people."""
    text = skill_text()
    assert "not** supported" in text or "not supported" in text
    assert "- [ ]" in text, (
        "the unsupported syntax should be shown, so it is recognised"
    )
