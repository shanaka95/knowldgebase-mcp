# Writing pages by hand

For anything that did not arrive as a file: a decision the user made, account
details they read out, notes from a call, a summary you were asked to keep.

If it *did* arrive as a file, upload the file instead. See
[files.md](files.md).

## Formats

`create_page` takes `format="markdown"` (use this), `"text"`, or `"html"`.

Markdown is the right default. It is converted and sanitised on arrival, so the
structure survives and nothing dangerous does.

### What survives

Headings (`#` to `######`), **bold**, *italic*, ~~strikethrough~~, `inline code`,
fenced code blocks, bullet and numbered lists, tables, block quotes, links,
images, horizontal rules.

### What does not

- **Task checkboxes.** `- [ ] thing` renders as the literal text "[ ] thing".
  Use a plain list, or a table with a Status column.
- **Raw HTML you would not want on a page.** Scripts, event handlers and
  `javascript:` links are stripped. Nothing is lost that should have been there.
- **Footnotes, definition lists, and other extensions.** Stick to the list above.

## What makes a page worth having in a year

**Lead with what it is.** One `#` heading naming the thing. It becomes the page
title if you do not pass one.

```markdown
# Tenancy agreement — Kastanienallee 14, Berlin
```

**Put the facts where they can be found.** A table or a list, not a paragraph.
Prose is for explanation; the numbers are the point.

```markdown
| | |
|---|---|
| Landlord | Hausverwaltung Müller GmbH |
| Monthly rent | 1,180 EUR cold |
| Deposit | 3,540 EUR |
| Notice period | 3 months |
| Runs from | 1 October 2026 |
```

**Dates absolute, always.** "expires 14 March 2027", never "next year" or "in six
months". Whoever reads this has no idea when it was written.

**Say where it came from.** One line at the end.

```markdown
*From the call with the letting agent, 12 September 2026.*
```

**One page, one subject.** Two unrelated things on one page means searching finds
the page for the wrong reason and the reader has to hunt. Two pages cost nothing.

**Do not invent.** If the user said the deposit is "about three and a half
thousand", write "about 3,500 EUR (approximate — confirm against the contract)".
Do not round it into a number that looks certain.

## doc_type

Give every page a type: Contract, Invoice, Payslip, Letter, Note, Meeting notes,
Certificate, Receipt.

Call `list_page_types` first and reuse what is already in use. It is free text, so
nothing stops you creating "Contracts" alongside "Contract" — and then neither
category is complete.

## Updating a page

`update_page` has two modes, and the difference matters.

- **`mode="append"`** reads the page and adds to the end. This is the safe one,
  and the right one when you are adding to something a person wrote.
- **`mode="replace"`** overwrites the body entirely. Only when you are certain the
  new content supersedes all of it.

Changing only `doc_type` or `title` does not re-index the page and does not bump
its version. Changing the body does both.

When adding to a page over time, append with a dated heading so the history stays
readable:

```markdown
## 12 September 2026

Agent confirmed the deposit is held with Deutsche Kautionskasse.
```

## Before writing, search

`search_pages` for what you are about to file. If a page for it exists, append to
it. A knowledge base with three pages about the same tenancy is worse than one,
because now every question about it returns three partial answers.

## A worked example

The user says: *"I just got off the phone with the insurer — my policy number is
HG-4471-902, it renews on the 3rd of April, and the excess is 250 euros."*

```
create_page(
  title="Home insurance — Hanseatic",
  space_id=<Personal>,
  folder_id=<Insurance>,
  doc_type="Insurance",
  format="markdown",
  content="""# Home insurance — Hanseatic

| | |
|---|---|
| Policy number | HG-4471-902 |
| Renews | 3 April 2027 |
| Excess | 250 EUR |

*From a call with the insurer, 13 September 2026.*
""",
)
```

Short, findable, and every fact is where someone would look for it.
