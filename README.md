# PlusGPT MCP server

An [MCP](https://modelcontextprotocol.io) server that lets an agent read, write,
search and question a [PlusGPT](https://github.com/shanaka95/knowldgebase) knowledge base
instance. Built with [FastMCP](https://gofastmcp.com) and served over streamable
HTTP.

The server stores no credentials. Each caller authenticates with their own
Knowledge Base API key, and every request is made as that user, so an agent can
reach exactly what its key can reach and nothing else.

## Tools

**Orientation**

| Tool | What it does |
| --- | --- |
| `whoami` | Who the key belongs to and whether it can write |
| `list_spaces` | Every space you can see, with your role in each |
| `browse_space` | Folders and page titles in a space, without page bodies |
| `recent_pages` | What you touched most recently, across spaces |

**Reading**

| Tool | What it does |
| --- | --- |
| `get_page` | Full page, as text by default or HTML for editing |
| `search_pages` | Keyword and semantic search fused, then reranked |
| `ask_knowledge_base` | A written answer with citations, the way the Ask page works |
| `list_page_types` | The kinds of page in use, so filing reuses existing vocabulary |

**Writing**

| Tool | What it does |
| --- | --- |
| `create_page` | New page from markdown, plain text or HTML |
| `update_page` | Replace a page or append to it |
| `create_folder` | New folder, optionally nested |
| `move_page` | Move a page to another folder or space |
| `delete_page` | Delete a page, reporting what was removed |

**Sharing**

Everything here hands something to somebody else, so each tool says plainly how
far the grant reaches.

| Tool | What it does |
| --- | --- |
| `share_page` | Give named people access to one page by email, inviting anyone without an account |
| `share_space` | Hand over a whole space, including pages added to it later - a far bigger grant than `share_page` |
| `share_page_by_link` | Make a page readable by **anyone with the link**, with no sign-in |
| `stop_sharing_by_link` | Withdraw that link, so the old one stays dead |
| `unshare_page` | Take one person's access to a page away |
| `remove_from_space` | Remove somebody from a space entirely |
| `list_people_with_access` | Who can see a page, and who is only invited |
| `list_space_members` | Who is in a space, and who has not joined yet |
| `list_shared_with_you` | Pages and spaces other people have shared with this account |
| `read_public_page` | Read a page shared by link, whether or not it is yours |
| `clone_page` | Take a private copy of a page somebody shared, so it cannot be withdrawn |

**Files**

| Tool | What it does |
| --- | --- |
| `upload_document` | Turn PDFs or images into pages, keeping the originals attached |
| `list_page_files` | The originals attached to a page: name, type and size |
| `get_page_file` | Fetch one original back, to pass on to whoever asked |
| `check_import` | Progress of one upload |
| `list_imports` | Recent uploads and how they turned out |
| `retry_import` | Re-parse a failed upload without resending the file |

**Indexing**

| Tool | What it does |
| --- | --- |
| `check_indexing` | Whether a page is searchable yet |
| `wait_for_indexing` | Block until a page is searchable |
| `reindex_page` | Rebuild chunks, summary and embeddings |

Every tool carries a description written for an agent to read, including what to
pass, what comes back, and which tool to reach for next.

### What a key can reach

There is no authority here beyond the caller's own key: every request is made
with it, and the Knowledge Base's own permission rules decide the answer. Two
limits are this server's own, because they are not the API's to enforce:

- Ids are checked before they become part of a request path, so a tool cannot
  be steered onto an endpoint other than the one it describes.
- `upload_document`'s `url` is fetched only from public addresses, and only up
  to 50 MB. The server sits on the same private network as the database, the
  object store and the vector store; a caller-chosen URL must not reach them.

### Pages have a type

Every page can be filed as a *type* - Letter, Invoice, Runbook, whatever this
knowledge base actually uses. It is free text, not a fixed set, so
`list_page_types` exists to show the vocabulary already in use: reusing
"Invoice" beats inventing "Invoices" and splitting the category in two.
`create_page`, `update_page` and `upload_document` all accept one.

### Several files at once

`upload_document` takes either one file (`content_base64`) or many
(`files_base64` with matching `filenames`). By default each file becomes its own
page named after its own content. With `combine=true` they become a single page
in the order given, which is what a document that arrived as a set of scans
needs. One `doc_type` applies to the whole upload.

## Connecting a client

The endpoint is `https://plusgpt.io/mcp` and the transport is streamable HTTP.
Authenticate with a Knowledge Base API key, created in the app under
**Settings → API keys**, sent as a bearer token:

```json
{
  "mcpServers": {
    "plusgpt": {
      "type": "http",
      "url": "https://plusgpt.io/mcp",
      "headers": { "Authorization": "Bearer kb_your_key_here" }
    }
  }
}
```

Give the key the **write** scope if the agent should create or edit pages. A
read-only key still searches and asks; writes are refused with a message saying
so.

## The skill

Connecting the server tells an agent *what it can call*. It does not tell it
*how to behave* - and the two mistakes that matter most are behavioural: reading
a PDF itself instead of passing the bytes, and filing a document wherever
without asking. `skills/plusgpt/` is a skill that settles both.

```
skills/plusgpt/
  SKILL.md              what PlusGPT is, when to use it, the whole tool list
  references/files.md   handing over files: formats, limits, combine, failures
  references/writing.md writing pages by hand: markdown support, page quality
```

Copy the directory into wherever the agent loads skills from - for Claude Code
that is `.claude/skills/plusgpt/` in a project or `~/.claude/skills/plusgpt/`
for every project:

```bash
cp -R skills/plusgpt ~/.claude/skills/
```

It is plain markdown with YAML frontmatter, so any agent that reads skills can
use it as-is. Its central instruction is that the agent passes files straight to
`upload_document` rather than transcribing them, because a vision model on the
server side reads scans, photographs and handwriting better than a transcription
does - and keeps the original attached to the page.

`tests/test_skill.py` checks the skill against the running server: every tool is
mentioned, no invented tool is named, every promised `upload_document` parameter
exists, and the accepted formats match the ones the server really takes. If a
tool is renamed and the skill is not updated, that test fails.

## Running it

With Docker, pointed at a Knowledge Base you can reach:

```bash
docker run --rm -p 8000:8000 \
  -e KB_API_URL=https://plusgpt.io/api/v1 \
  shanaka95/knowledgebase-mcp:latest
```

Alongside the Knowledge Base stack, as a service in the same compose file:

```yaml
mcp:
  image: shanaka95/knowledgebase-mcp:latest
  environment:
    KB_API_URL: http://backend:8000/api/v1
  restart: unless-stopped
```

For development:

```bash
uv sync
uv run python -m kb_mcp          # serves http://127.0.0.1:8000/mcp
uv run pytest tests -q
uv run ruff check src tests && uv run mypy
```

## Configuration

Every setting is an environment variable; see `.env.example`. The one that
matters is `KB_API_URL`, which is a container name inside a deployment and a
public URL from anywhere else.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KB_API_URL` | `http://backend:8000/api/v1` | Knowledge Base REST API |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Where this server listens |
| `MCP_PATH` | `/mcp` | Mount path |
| `KB_TIMEOUT_SECONDS` | `120` | Ordinary requests |
| `KB_LONG_TIMEOUT_SECONDS` | `600` | Answering and document parsing |
| `AUTH_CACHE_SECONDS` | `300` | How long a verified key is trusted |
| `MAX_WAIT_SECONDS` | `540` | Cap on blocking waits |

## How authentication works

1. The client sends `Authorization: Bearer kb_...`.
2. The server calls `GET /users/me` on the Knowledge Base with that key.
3. A rejected key, or an unreachable Knowledge Base, denies the request.
4. A verified key is cached briefly so a burst of tool calls is one auth check.
5. The same key is then used for every API call the tools make.

Revoking a key in the app stops it working here within `AUTH_CACHE_SECONDS`.
