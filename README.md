# carryover

Move an AI project, with its instructions, knowledge files, memory and chat
history, from one account to another.

v1 supports **claude.ai → claude.ai**: for example, moving a project from a
personal account to a work account. Everything goes through a neutral
[bundle format](docs/BUNDLE_FORMAT.md), so other platforms can be added later
as adapters.

It comes in two forms:

- a **CLI** (`carryover`) that needs only the Python standard library, and
- a **Claude skill** that lets Claude run the whole move for you, including
  writing the chat summaries.

## How it works

```
old account                       neutral bundle                         new account
───────────                       ──────────────                         ───────────
Settings → Export data  ──pack──▶  instructions, knowledge,  ──unpack──▶  import kit:
(zip)                              memory, chats (JSON + MD)              START_HERE.md
                                         │                                instructions to paste
                                    summarize                             files to upload
                                         ▼                                memory to re-seed
                                   per-chat summaries +                   first message
                                   PROJECT_CONTEXT.md
```

claude.ai has no project import API, so the last step is a 5-minute manual
upload guided by `START_HERE.md`.

| What | How it arrives in the new account |
|---|---|
| Custom instructions | Pasted verbatim, plus a short note pointing to the carried-over history |
| Knowledge files | Uploaded as-is (text extracted by the export) |
| Project memory | A ready-made message asking Claude to remember it |
| Account memory | Same, only with `--include-account-memory` |
| Chats | `PROJECT_CONTEXT.md` handoff, one summary per chat, and the full transcripts as searchable knowledge |

## Quick start (CLI)

```bash
pip install -e .            # add [api] to summarize with the Claude API
```

1. In the **old** account, go to claude.ai → Settings → Privacy → **Export data**
   and download the zip from the email.

   > **Got a small `.json` file instead of a zip?** Newer exports are split. That
   > file is a *manifest* listing several parts (`projects-000.zip`,
   > `conversations-000.zip`, `memories-000.zip`, …). Open each link inside it in a
   > browser signed in to that account (links expire after 24 hours and may work
   > only once). Save the parts in the same folder as the manifest, then pass the
   > manifest or that folder to carryover instead of a single zip.
2. See what's in it:

   ```bash
   carryover inspect data-2026-11-18.zip
   ```

3. Find the project's chats. The claude.ai export doesn't record which chats
   belong to a project, so search for them:

   ```bash
   carryover inspect data-2026-11-18.zip --search "thesis" --title "^Ch(apter)? ?\d" -v
   ```

4. Pack the project with those chats:

   ```bash
   carryover pack data-2026-11-18.zip --project "Thesis Writing" --chat-search thesis -o thesis.carryover
   ```

5. Summarize the chats, either with the Claude API:

   ```bash
   carryover summarize thesis.carryover --api
   ```

   or have Claude write the summaries with the skill (see below).
   `carryover status thesis.carryover` shows what's missing.
6. Build the import kit and follow its `START_HERE.md` in the new account:

   ```bash
   carryover unpack thesis.carryover --zip
   ```

### No "Export data" button?

If your account belongs to an organization (Team, Enterprise or Education),
the export button only appears when an owner turns on *"Allow members to
export their own data"*. Without it, use no-export mode:

1. Print the handoff prompt:

   ```bash
   carryover handoff-prompt
   ```

2. Paste it into a **new chat inside the old project**. Claude writes a handoff
   document covering goals, decisions, state, per-chat summaries and memory.
   Save the answer as `handoff.md`. Claude can cover past chats only if chat
   search or memory is enabled for that account. Otherwise it works from the
   project's instructions and knowledge, and says so.
3. By hand, copy the project's instructions into `instructions.md` and
   download its knowledge files into a folder.
   Optionally, save important chats as `.md` files by copy-pasting them.
4. Build the bundle and the kit:

   ```bash
   carryover from-manual --name "Thesis" --instructions instructions.md \
       --handoff handoff.md --knowledge files/ --chat saved-chats/ -o thesis.carryover
   ```

   ```bash
   carryover unpack thesis.carryover --zip
   ```

Moving from a work or university account to a personal one? Check your
organization's rules on taking its data elsewhere first.

### Commands

| Command | Purpose |
|---|---|
| `inspect EXPORT [--chats] [--search TEXT] [--title REGEX] [-v] [--json]` | List projects, or find chats |
| `pack EXPORT --project NAME [chat selectors] [-o DIR] [--zip]` | Export → bundle |
| `status BUNDLE [--json]` | Bundle contents and missing summaries |
| `template conversation\|project` | Print a summary template |
| `summarize BUNDLE --api [--model] [--effort] [--workers] [--force]` | Fill summaries with the Claude API |
| `unpack BUNDLE [-o DIR] [--no-transcripts] [--chunk-chars N] [--zip]` | Bundle → claude.ai import kit |
| `handoff-prompt` | Prompt to paste into the old project (no-export mode) |
| `from-manual --name N [--instructions F] [--handoff F] [--knowledge P] [--memory F] [--chat P]` | Hand-copied pieces → bundle |

Chat selectors can be combined: `--chat ID`, `--chats-file FILE`,
`--chat-title REGEX`, `--chat-search TEXT`, `--all-chats`, and the date filters
`--since/--until YYYY-MM-DD`.

`summarize --api` defaults to `claude-opus-5-5` at `medium` effort, with
server-side refusal fallback on. It reads credentials from `ANTHROPIC_API_KEY`
or an `ant auth login` profile. Change these with `--model`, `--effort` and
`--no-fallback`.

## The Claude skill

```bash
python3 scripts/build_skill.py              # → dist/carryover-skill.zip
python3 scripts/build_skill.py --install    # → ~/.claude/skills/carryover (Claude Code)
```

- **claude.ai**: upload `dist/carryover-skill.zip` under Settings → Capabilities → Skills.
  Then, in a chat, upload your export zip and say *"move my Thesis project to my
  other account"*.
- **Claude Code**: use `--install`, then ask the same thing in any session.

With the skill, Claude finds the project's chats with you, packs them, writes
the summaries itself (no API key needed), and hands you the finished import kit.

## Privacy

Exports, bundles and kits contain your private chat history.

- Account details (email, phone) from `users.json` are never copied.
- Account-wide memory is left out unless you pass `--include-account-memory`.
- `.gitignore` keeps exports, bundles and kits out of git. Delete them when you're done.

## Limits

- Old chats become searchable knowledge files, not real chats in the new account.
- Images and other binary uploads aren't in the claude.ai export, so they can't be carried over.
- Very large histories can exceed project knowledge limits. Use `unpack --no-transcripts`;
  the summaries carry the essentials.
- The export format isn't formally documented. The reader is tolerant, but
  please open an issue if your export doesn't parse.

## Development

```bash
pip install -e '.[dev,api]'
pytest
```

Layout: `src/carryover/` holds the CLI and library, with platform adapters in
`adapters/`. The skill is in `skill/carryover/`, tests in `tests/` and the format
spec in `docs/`.
