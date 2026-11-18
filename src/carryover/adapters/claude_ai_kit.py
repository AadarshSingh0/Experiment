"""Build a claude.ai *import kit* from a bundle.

claude.ai has no import API for projects, so the kit is a folder of
ready-to-use pieces plus a START_HERE.md checklist:

    START_HERE.md
    1_name_and_description.md
    2_custom_instructions.md          paste into the project's instructions
    3_upload_to_project_knowledge/    upload every file in here
        00_PROJECT_CONTEXT.md
        01_CHAT_SUMMARIES.md
        <original knowledge files>
        zz_chat_transcripts_partNN.md
    4_memory.md                       (only when there is memory to carry)
    5_first_message.md                first message for the new project
"""

from __future__ import annotations

import os
import re
import shutil
from typing import List

from carryover.bundle import Bundle, BundleError
from carryover.render import conversation_markdown
from carryover.summarize import conversation_header
from carryover.util import excerpt, safe_filename, short_day, unique_name

KNOWLEDGE_DIR = "3_upload_to_project_knowledge"
DEFAULT_CHUNK_CHARS = 300_000

def history_note(has_summaries: bool, has_transcripts: bool) -> str:
    lines = ["## Carried-over history",
             "This project was moved here from another account. The project knowledge holds its history:",
             "- `00_PROJECT_CONTEXT.md`: read this first. Goals, decisions, current state and open threads."]
    if has_summaries:
        lines.append("- `01_CHAT_SUMMARIES.md`: one summary per earlier chat, oldest first.")
    if has_transcripts:
        lines.append("- `zz_chat_transcripts_partNN.md`: the full earlier chats. "
                     "Search them when exact wording or details matter.")
    lines.append("Treat this as the project's own history and continue from where it left off.")
    return "\n".join(lines)


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")


def _strip_h1(md: str) -> str:
    lines = md.strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _first_user_text(conv: dict) -> str:
    for m in conv.get("messages") or []:
        if m.get("role") == "user" and (m.get("text") or "").strip():
            return m["text"]
    return ""


def _stub_context(bundle: Bundle) -> str:
    lines = [
        f"# Project context: {bundle.name}",
        "",
        "> Auto-generated without AI summaries. Run `carryover summarize` (or ask Claude",
        "> with the carryover skill) for a much richer handoff.",
        "",
        "## About",
        bundle.description.strip() or "(no description)",
        "",
    ]
    if bundle.project_memory:
        lines += ["## What Claude remembered about this project", bundle.project_memory, ""]
    lines += ["## Earlier chats (oldest first)"]
    for c in bundle.conversations:
        conv = bundle.conversation(c)
        lines.append(f"- **{c['title']}** ({short_day(c.get('created_at'))}, {c.get('messages', 0)} messages): "
                     f"{excerpt(conv.get('source_summary') or _first_user_text(conv), 240)}")
    if not bundle.conversations:
        lines.append("- (no chats were included)")
    return "\n".join(lines) + "\n"


def _summaries_doc(bundle: Bundle) -> str:
    out = [f"# Chat summaries: {bundle.name}", "",
           "One entry per chat from the previous account, oldest first. "
           "Full text is in the `zz_chat_transcripts_partNN.md` files.", ""]
    for c in bundle.conversations:
        head = conversation_header(c)
        out.append(f"## {head['title']}")
        body = bundle.summary(c)
        if body:
            out.append(_strip_h1(body))
        else:
            conv = bundle.conversation(c)
            out.append(f"_{head['dates']} · {head['messages']} messages · no AI summary yet_")
            out.append("")
            src = conv.get("source_summary")
            out.append(src if src else "Opened with: " + excerpt(_first_user_text(conv), 400))
        out.append("")
    return "\n".join(out)


def _transcript_parts(bundle: Bundle, chunk_chars: int) -> List[str]:
    blocks = [(c, conversation_markdown(bundle.conversation(c), heading_level=2)) for c in bundle.conversations]
    groups: List[list] = []
    current: list = []
    size = 0
    for c, md in blocks:
        if current and size + len(md) > chunk_chars:
            groups.append(current)
            current, size = [], 0
        current.append((c, md))
        size += len(md)
    if current:
        groups.append(current)
    parts = []
    for i, group in enumerate(groups, 1):
        toc = "\n".join(f"- {c['title']} ({short_day(c.get('created_at'))})" for c, _ in group)
        header = (f"# Chat transcripts, part {i} of {len(groups)}: {bundle.name}\n\n"
                  f"Earlier chats carried over from the previous account, oldest first.\n\n"
                  f"In this part:\n{toc}\n\n---\n\n")
        parts.append(header + "\n---\n\n".join(md for _, md in group))
    return parts


def build_kit(bundle: Bundle, out_dir: str, include_transcripts: bool = True,
              chunk_chars: int = DEFAULT_CHUNK_CHARS, context_note: bool = True,
              overwrite: bool = False) -> dict:
    if os.path.exists(out_dir):
        if not overwrite:
            raise BundleError(f"{out_dir} already exists (use --force to replace it)")
        if not os.path.isfile(os.path.join(out_dir, "START_HERE.md")):
            raise BundleError(f"refusing to replace {out_dir}: it is not an import kit")
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    kdir = os.path.join(out_dir, KNOWLEDGE_DIR)
    os.makedirs(kdir)
    warnings = []

    # 1. name + description
    _write(os.path.join(out_dir, "1_name_and_description.md"),
           f"# Project name\n\n{bundle.name}\n\n# Description\n\n{bundle.description.strip() or '(none)'}\n")

    # 2. custom instructions
    instructions = bundle.instructions.strip()
    has_history = bool(bundle.conversations or bundle.project_context)
    if context_note and has_history:
        note = history_note(bool(bundle.conversations), include_transcripts and bool(bundle.conversations))
        instructions = (instructions + "\n\n" if instructions else "") + note
    _write(os.path.join(out_dir, "2_custom_instructions.md"), instructions or "(This project had no custom instructions.)")

    # 3. knowledge
    taken = {"00_PROJECT_CONTEXT.md", "01_CHAT_SUMMARIES.md"}
    files = []
    context = bundle.project_context
    if not context:
        context = _stub_context(bundle)
        warnings.append("No AI summaries yet: PROJECT_CONTEXT.md is a basic stub. "
                        "Run `carryover summarize` first for a proper handoff.")
    elif bundle.missing_summaries():
        warnings.append(f"{len(bundle.missing_summaries())} chat(s) have no summary; "
                        "they appear as short excerpts in 01_CHAT_SUMMARIES.md.")
    _write(os.path.join(kdir, "00_PROJECT_CONTEXT.md"), context)
    files.append("00_PROJECT_CONTEXT.md")
    if bundle.conversations:
        _write(os.path.join(kdir, "01_CHAT_SUMMARIES.md"), _summaries_doc(bundle))
        files.append("01_CHAT_SUMMARIES.md")
    for k in bundle.knowledge:
        name = unique_name(safe_filename(k["path"].split("/", 1)[-1]), taken)
        if re.match(r"^(00_|01_|zz_chat_transcripts)", name):
            name = unique_name("kb_" + name, taken)
        shutil.copyfile(bundle.path(k["path"]), os.path.join(kdir, name))
        files.append(name)
    if include_transcripts and bundle.conversations:
        for i, text in enumerate(_transcript_parts(bundle, chunk_chars), 1):
            name = f"zz_chat_transcripts_part{i:02d}.md"
            _write(os.path.join(kdir, name), text)
            files.append(name)

    total_chars = sum(os.path.getsize(os.path.join(kdir, f)) for f in files)

    # 4. memory
    has_memory = bool(bundle.project_memory or bundle.account_memory)
    if has_memory:
        parts = ["# Memory to carry over", "",
                 "claude.ai builds memory from your chats, so the simplest way to carry it over is to",
                 "paste the message(s) below into a chat and ask Claude to remember them. You can review",
                 "and edit what Claude remembers in the new account's settings afterwards.", ""]
        if bundle.project_memory:
            parts += ["## Project memory: paste into a chat *inside the new project*", "", "---", "",
                      "Please remember the following about this project. It is carried over from my "
                      "previous account:", "", bundle.project_memory, "", "---", ""]
        if bundle.account_memory:
            parts += ["## Account memory: paste into a normal chat *outside any project*", "", "---", "",
                      "Please remember the following about me. It is carried over from my previous account:",
                      "", bundle.account_memory, "", "---", ""]
        _write(os.path.join(out_dir, "4_memory.md"), "\n".join(parts))

    # 5. first message
    first = ("We're continuing this project from my previous account. Please read "
             "00_PROJECT_CONTEXT.md in the project knowledge, then give me a short recap of "
             "where we left off and the open next steps.")
    _write(os.path.join(out_dir, "5_first_message.md"), first)

    # START_HERE
    steps = [
        f"# Move \"{bundle.name}\" into your new Claude account",
        "",
        f"Made by carryover from a {bundle.manifest.get('source', {}).get('platform', 'claude.ai')} export. "
        f"Contents: {len(bundle.knowledge)} knowledge file(s), {len(bundle.conversations)} chat(s)"
        f"{', memory' if has_memory else ''}.",
        "",
    ]
    if warnings:
        steps += ["> **Before you start**"] + [f"> - {w}" for w in warnings] + [""]
    steps += [
        "## Steps (about 5 minutes)",
        "",
        "1. Sign in to the **new** account at claude.ai and create a new project.",
        "   Use the name and description in `1_name_and_description.md`.",
        "2. Open the project's instructions and paste the whole of `2_custom_instructions.md`.",
        f"3. Upload **every file** in `{KNOWLEDGE_DIR}/` to the project knowledge"
        f" ({len(files)} files, about {total_chars // 4:,} tokens).",
    ]
    n = 4
    if has_memory:
        steps.append(f"{n}. Follow `4_memory.md` to carry over what Claude remembered.")
        n += 1
    steps += [
        f"{n}. Start a chat in the project and send the message in `5_first_message.md`.",
        "   Claude should recap the project. If something important is missing, add it to",
        "   `00_PROJECT_CONTEXT.md` and upload it again.",
        "",
        "## Good to know",
        "",
        "- Old chats come across as knowledge files, not as clickable chats. Claude can search and quote them.",
        "- Images and other binary uploads from old chats aren't in the claude.ai export. Re-upload any you still need.",
        "- If the knowledge is near the project's size limit, leave out the `zz_chat_transcripts_*` files:"
        " the context file and summaries carry the essentials.",
        "- Delete this kit when you're done. It contains your private chat history.",
    ]
    _write(os.path.join(out_dir, "START_HERE.md"), "\n".join(steps))
    return {"out_dir": out_dir, "knowledge_files": files, "warnings": warnings,
            "approx_tokens": total_chars // 4}
