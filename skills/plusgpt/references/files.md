# Handing files to PlusGPT

The rule in one line: **pass the bytes, do not read the file yourself.**

A vision model on the other side reads the document — printed text, scans,
photographs, handwriting, tables — and reproduces its headings, paragraphs, lists
and tables as an editable page. The original file stays attached to that page and
can be downloaded again at any time. Anything you transcribe by hand is a worse
copy with no original behind it.

Parse a document yourself **only** when the user explicitly asks you to.

## When you were given a path, not bytes

A file sent over a messaging channel usually reaches you as a local path. You
cannot read it — that is deliberate — so pass the path along:

```
send_file_to_knowledge_base(
  path="/…/cache/documents/tenancy.pdf",   # exactly as you were given it
  space_id="…",
  folder_id="…",
  doc_type="Contract",
)
```

It reads the file and hands it to the knowledge base, which parses it and keeps
the original. Only files from the current conversation can be read this way; any
other path is refused, so do not try to widen it.

The two failures worth naming, because both waste somebody's time:

- **Asking them to re-send it.** You already have the path. Use it.
- **Asking them to paste the contents.** The knowledge base reads documents far
  better than a person can retype one.

## What is accepted

| | |
|---|---|
| Formats | PDF, PNG, JPEG (`.jpg`/`.jpeg`), WebP, GIF, BMP, TIFF |
| Size | up to 50 MB per file |
| Pages | up to 100 per PDF |
| Files per call | up to 20 |

The type is worked out from the **filename extension**, so always pass a filename
with the right extension. `scan` is guessed wrongly; `scan.png` is not.

A refused file says so plainly:

```
Unsupported file type 'text/plain' for 'notes.txt'.
Upload a PDF, PNG, JPEG, WebP, GIF, BMP or TIFF file.
```

### Anything else

- **Text you can already read** — a note, an email body, a `.md`, `.txt`, `.csv`
  — does not need uploading at all. Use `create_page` with the text.
- **A binary office format** — `.docx`, `.xlsx`, `.pptx`, `.pages` — ask the user
  to export it as PDF. Do not retype it, and do not attempt to decode it.
- **An archive** — ask them to send the files inside it.

## One file

```
upload_document(
  filename="tenancy-agreement.pdf",
  space_id="…",                 # from list_spaces
  folder_id="…",                # from browse_space; omit for the space root
  content_base64="JVBERi0xLjcK…",
  doc_type="Contract",          # reuse a type from list_page_types
  wait=True,
)
```

`wait=True` returns the finished page, with a preview of what was read. Use it
unless the file is very long; then `wait=False` gives an `import_id` to poll with
`check_import`.

## Several files

```
upload_document(
  filename="scan-1.png",                     # names the first
  space_id="…",
  files_base64=[<bytes1>, <bytes2>, <bytes3>],
  filenames=["scan-1.png", "scan-2.png", "scan-3.png"],
  combine=False,
  wait=True,
)
```

`filenames` must be the same length as `files_base64` — a mismatch is refused
rather than guessed at, because pairing the wrong name to the wrong file is worse
than failing. Omit `filenames` and they are numbered after `filename`.

### combine: the one decision that matters

| | |
|---|---|
| `combine=False` (default) | each file becomes its own page, named from its own content |
| `combine=True` | all files become **one** page, in the order given |

Use `combine=True` for a single document that arrived in pieces: a contract
photographed page by page, a report split across two PDFs, a letter and its
appendix. Use the default for a batch of unrelated things: this month's receipts,
a folder of payslips.

If the user's intent is not obvious from what they said, ask. "Are these three
pages of one contract, or three separate documents?" is a cheap question; merging
three receipts into one page is expensive to undo.

With `combine=True` a `title` applies to the resulting page. With `combine=False`
a shared title would name every page identically, so it is ignored and each page
is named from its own content.

## Titles

Leave `title` out and the page is named after the document's own opening heading —
usually better than a filename. If the document has no heading, the filename stem
is used.

Give a `title` when the filename is meaningless (`scan_0007.pdf`, `IMG_4821.jpg`)
and you know what the thing is. "Tenancy agreement, Berlin flat" beats
`scan_0007`.

## doc_type

One short label for what kind of thing this is: Contract, Invoice, Payslip,
Letter, Receipt, Service record, Certificate. It applies to every file in the
call.

Call `list_page_types` first and **reuse an existing one where it fits**. Types
are free text, so "Invoice" and "Invoices" will happily become two categories
that then have to be searched separately forever.

## prompt

An optional instruction to the model reading the file. Usually unnecessary. Worth
it when the document is unusual:

- `"This is handwritten."`
- `"Keep the tables as tables; the numbers matter more than the prose."`
- `"This is a two-column layout; read each column top to bottom."`

## After the upload

A page is not searchable the instant it is created — it is chunked, summarised
and embedded first, which takes a short while. The reply reports `indexing`.

- `wait_for_indexing(page_id=…)` blocks until it is ready. Use it if you are about
  to search for what you just uploaded.
- `check_indexing(page_id=…)` asks without blocking.

If you upload and then immediately ask a question about it, wait first — otherwise
the answer will honestly tell you it has nothing on the topic.

## When it goes wrong

| What you see | What it means |
|---|---|
| `Unsupported file type …` | Not a PDF or image. Convert, or use `create_page`. |
| `too large` | Over 50 MB. Ask for a smaller export or split it. |
| status `failed` on an import | Parsing did not finish. `retry_import(import_id)` re-runs it **without** re-sending the file. |
| `No text could be extracted` | A blank or unreadable scan. Ask for a better copy rather than guessing at the content. |
| `read-only` | The key cannot write. The user needs a read/write key from Settings → API keys. |

`list_imports` shows recent uploads and how they turned out, which is how you
recover a page id you did not keep.

## Getting an original back

The file a page was made from stays attached to it.

```
list_page_files(page_id="...")
  -> {"files": [{"file_id": "...", "filename": "tenancy.pdf",
                 "content_type": "application/pdf", "size_bytes": 184320}]}

get_page_file(file_id="...")
  -> the file itself; your client saves it and reports the path
```

Check `size_bytes` first: anything over 25 MB is refused, and most channels
refuse far less. A page written by hand rather than uploaded has no files, and
`list_page_files` says so.

Fetch a file only when the person wants the document. To answer a question
about its contents, `ask_knowledge_base` costs a fraction as much.

## The `url` parameter

`upload_document` can fetch a public http(s) URL instead of taking bytes. It is
there for genuinely public documents.

It refuses anything on a private or internal address — deliberately, because the
server sits next to a database and a vector store, and a tool that fetches
arbitrary URLs from inside a network is a way to read them. If a file is on the
user's machine, read it and pass `content_base64`; do not try to route it through
a local URL.
