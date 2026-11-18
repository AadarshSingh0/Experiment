# carryover bundle format (v1)

A bundle is a plain folder, optionally zipped, that holds one AI project in a
platform-neutral way. Readers are adapters that turn an official export into a
bundle. Writers turn a bundle into whatever the target platform needs. Everything
is UTF-8 text, so a bundle is easy to inspect, diff and edit by hand.

```
manifest.json
project/instructions.md        custom instructions / system prompt (verbatim)
project/description.md
memory/project.md              memory scoped to this project (optional)
memory/account.md              account-wide memory (optional, opt-in)
knowledge/<file>               knowledge files as text
conversations/<stem>.json      normalised chat (schema below)
conversations/<stem>.md        readable transcript of the same chat
summaries/<stem>.md            per-chat handoff summary (optional)
summaries/PROJECT_CONTEXT.md   whole-project handoff (optional)
```

`<stem>` is `YYYY-MM-DD_<title-slug>_<first 8 chars of id>`.

## manifest.json

```json
{
  "format": "carryover.bundle",
  "format_version": 1,
  "generator": "carryover 0.1.0",
  "created_at": "2026-11-18T09:00:00Z",
  "source": {"platform": "claude.ai", "export_file": "data-2026-11-18.zip"},
  "project": {
    "name": "Thesis Writing",
    "source_id": "uuid-in-source-platform",
    "created_at": "...", "updated_at": "...",
    "instructions": "project/instructions.md",
    "description": "project/description.md"
  },
  "memory": {"project": "memory/project.md", "account": "memory/account.md"},
  "knowledge": [
    {"path": "knowledge/notes.pdf.txt", "original_name": "notes.pdf", "chars": 1234, "created_at": "..."}
  ],
  "conversations": [
    {
      "id": "uuid", "title": "Chapter 3 structure",
      "created_at": "...", "updated_at": "...", "messages": 14,
      "json": "conversations/<stem>.json",
      "transcript": "conversations/<stem>.md",
      "summary": "summaries/<stem>.md"
    }
  ],
  "project_context": "summaries/PROJECT_CONTEXT.md",
  "options": {"include_thinking": false, "include_account_memory": false}
}
```

A summary path is listed even before the file exists. A missing file just means
the chat hasn't been summarized yet. `memory` keys appear only when present.

## Conversation JSON

```json
{
  "id": "uuid",
  "title": "Chapter 3 structure",
  "created_at": "...", "updated_at": "...",
  "source_summary": "summary the source platform stored, if any",
  "messages": [
    {
      "role": "user | assistant",
      "created_at": "...",
      "text": "message text (artifact contents inlined as [tool: title] blocks)",
      "attachments": [{"name": "draft.txt", "type": "txt", "content": "extracted text"}],
      "files": ["figure.png"]
    }
  ]
}
```

`attachments` carry extracted text. `files` lists binary uploads by name only,
because exports do not include their bytes.

## Rules for adapters

- Never copy account identity (email, phone, account ids) into a bundle.
- Account-wide memory is opt-in.
- Keep instructions and knowledge verbatim. Put summaries only in `summaries/`.
- Write tolerant readers: ignore unknown fields and default missing ones.
