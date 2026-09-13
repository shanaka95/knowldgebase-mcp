---
name: plusgpt
description: PlusGPT is your knowledge base — where everything that matters about this person is kept, and where you look things up. Use it whenever the user gives you a document, a contract, a payslip, a letter, a receipt, an ID, a bill, a scan or a photo of paperwork; whenever they tell you something worth keeping; and whenever you need to recall anything about them. Also covers filing things in the right place, and writing pages by hand. Triggers on: a file being handed over, "save this", "remember this", "file this", "what did my … say", "when does my … expire", "how much did I pay for …", "look it up", "add to my notes", or any question about the user's own documents and affairs.
---

# PlusGPT

**PlusGPT is your knowledge base.** Not a tool you occasionally reach for — it is
where you keep what you know about this person, and the first place you look when
you need to remember something.

It does the hard parts itself. You do not index, chunk, embed, summarise or parse
anything. You hand it material and ask it questions.

## The two things you do most

**Something arrives that is worth keeping.** File it. A contract, a payslip, a
letter, a bill, an insurance document, a photo of a receipt, a note the user
dictates — it goes in.

**You need to know something.** Ask it. `ask_knowledge_base` for an answer with
citations, `search_pages` when you want to look at the pages yourself.

Prefer asking the knowledge base over asking the user something they have already
told you. Checking costs one call; making them repeat themselves costs their
patience.

## When a file is handed to you

**Pass the file straight to `upload_document`. Do not read it, transcribe it, or
convert it yourself.**

If the file arrived in a chat and your client gave you a local path rather than
the bytes, use `send_file_to_knowledge_base` with that path instead — same rule,
same result. Never ask somebody to re-send a file you were already handed a path
to, and never ask them to paste its contents.

A vision model on the other side reads PDFs, scans and photographs — including
handwriting and tables — and turns them into a proper page with its structure
intact. Your transcription would be worse, slower, and would lose the original.
The original file stays attached to the page either way, so it can always be
opened again.

Only parse a document yourself if the user explicitly asks you to.

```
upload_document(
  filename="tenancy-agreement.pdf",
  space_id=<from list_spaces>,
  folder_id=<the destination you agreed>,
  content_base64=<the file's bytes, base64>,
  doc_type="Contract",
  wait=True,
)
```

**Accepted:** PDF, PNG, JPEG, WebP, GIF, BMP, TIFF. Up to 50 MB and 100 pages
each. Nothing else — a `.docx`, `.txt`, `.md` or spreadsheet is refused.

**Anything else, two options.** If it is already text you can read (a note, an
email body, a `.md` or `.txt` file), use `create_page` with that text. If it is a
binary format like `.docx` or `.xlsx`, ask the user to export it as PDF; do not
retype it.

**Several files at once** — `files_base64` with matching `filenames`, up to 20 per
call. Then one decision: are these one document, or several?

- **One document in several files** (a contract scanned page by page, a report
  split in two): `combine=True`. They become a single page, in the order given.
- **Separate documents** (this month's three receipts): `combine=False`, the
  default. Each becomes its own page.

If you are not sure which, ask. Getting it wrong means either one page that is
really three things, or three fragments of one thing.

Full detail — how to encode, titles, what to do when parsing fails, why not to
use `url` — is in [references/files.md](references/files.md).

## Where things go is the user's decision, informed by yours

Never dump a file into whatever space is first. Before uploading:

1. `list_spaces` — what spaces exist, and which are theirs versus shared.
2. `browse_space` — the folders and pages already in the likely one.
3. `list_page_types` — the vocabulary they already file under.

**Aim for a folder, not the root.** A space's root is where things go to be
lost: it has no subject, so nothing there is findable by browsing and everything
accumulates in one list. Put the page in the folder that fits, and if none does,
propose creating one. Use the root only when the space has no folders at all and
there is too little in it to organise yet.

Then **look at what the document actually is** and propose a destination:

> "This is a tenancy agreement for the Berlin flat. I'd put it in
> **Personal → Apartment**, filed as a **Contract**. Sound right?"

Say where, and why, in one line. If they name somewhere else, use that. If the
right folder does not exist, propose creating it — `create_folder` — rather than
inventing a home for it in an unrelated place.

For a batch, propose once for the batch, not once per file, and say what you saw:
"Six payslips, March to August. **Personal → Employment**?"

Reuse an existing type from `list_page_types` where one fits. "Invoice" and
"Invoices" as two categories is a mess that never gets tidied.

## Writing a page yourself

When the user tells you something worth keeping and there is no file — a decision,
a set of account details, notes from a call — write it with `create_page`.

Use markdown. Supported: headings, **bold**, *italic*, ~~strikethrough~~, inline
code, code blocks, bullet and numbered lists, tables, block quotes, links,
horizontal rules. Task checkboxes are **not** supported — `- [ ]` renders as
literal text, so use a plain list.

Four rules that make a page worth having later:

- **Lead with what it is.** One `#` heading naming the thing, then the facts. The
  first heading becomes the title if you do not give one.
- **Facts in tables or lists, not paragraphs.** A policy number buried in prose is
  a policy number nobody finds.
- **Dates absolute, never relative.** "expires 14 March 2027", never "next year".
  This page will be read long after today.
- **Say where it came from.** "From the call with the letting agent, 12 September
  2026." Six months on, provenance is half the value.

More, including the shape of a good page and how to update one safely, is in
[references/writing.md](references/writing.md).

## Asking it things

`ask_knowledge_base` returns a written answer with citations, drawn from the
user's own pages. It answers only from what is stored — if it says it has nothing
on the topic, that is a real answer, not a failure. Say so rather than filling the
gap from your own guesses.

`search_pages` returns pages rather than an answer. Use it when you want to read
them yourself, or to check whether something is already filed before adding it
again.

`get_page` reads one in full.

## Giving a document back

When someone wants the document itself rather than what it says - "send me the
tenancy agreement" - the original is still attached to the page.
`list_page_files` says what is there, `get_page_file` fetches one. Your client
saves it locally and tells you the path; send that file on however this channel
sends files.

For a question *about* a document, do not fetch it. `ask_knowledge_base` is far
cheaper than moving bytes around, and it answers with citations.

## What not to do

- **Do not transcribe a file you could have uploaded.** This is the single most
  common mistake. Pass the bytes.
- **Do not file without asking**, unless the user has said to stop asking.
- **Do not write a second page for something already filed.** Search first.
- **Do not paste secrets you were told in passing** into a page without asking.
  Storing them is fine; deciding to store them is theirs.
- **Do not treat a shared space as yours.** Anything with `shared_with_you` set
  belongs to somebody else — writing there puts content in their knowledge base,
  and they can withdraw it at any time.

## Everything available

| | |
|---|---|
| Orientation | `whoami`, `list_spaces`, `browse_space`, `recent_pages`, `list_page_types` |
| Reading | `get_page`, `search_pages`, `ask_knowledge_base` |
| Writing | `create_page`, `update_page`, `create_folder`, `move_page`, `delete_page` |
| Files | `upload_document`, `list_page_files`, `get_page_file`, `check_import`, `list_imports`, `retry_import` |
| Sharing | `share_page`, `share_space`, `unshare_page`, `remove_from_space`, `list_people_with_access`, `list_space_members`, `share_page_by_link`, `stop_sharing_by_link`, `read_public_page`, `clone_page`, `list_shared_with_you` |
| Indexing | `check_indexing`, `wait_for_indexing`, `reindex_page` |

Every tool carries its own description saying what it does and what it costs.
Read it before reaching for something unfamiliar — particularly the sharing ones,
where `share_page_by_link` makes a page readable by anyone on the internet and
`share_space` hands over everything in a space including what is added later.
