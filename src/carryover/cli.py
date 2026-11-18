"""Command-line interface.

    carryover inspect   EXPORT                  list projects (and chats with --chats)
    carryover pack      EXPORT --project NAME   export -> bundle
    carryover status    BUNDLE                  what is in a bundle, which summaries are missing
    carryover template  conversation|project    print a summary template
    carryover summarize BUNDLE --api            fill summaries via the Claude API
    carryover unpack    BUNDLE                  bundle -> claude.ai import kit

No "Export data" button (e.g. an organization account)?
    carryover handoff-prompt                    prompt to paste into the old project
    carryover from-manual --name NAME ...       hand-copied pieces -> bundle
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from carryover import __version__
from carryover.adapters.claude_ai import PLATFORM, ClaudeExport, ExportError
from carryover.adapters.claude_ai_kit import DEFAULT_CHUNK_CHARS, build_kit
from carryover.bundle import Bundle, BundleError, write_bundle, zip_dir
from carryover.summarize import (CONVERSATION_TEMPLATE, DEFAULT_EFFORT, DEFAULT_MODEL,
                                 PROJECT_CONTEXT_TEMPLATE, SummarizeError, make_api_caller,
                                 summarize_bundle)
from carryover.manual import HANDOFF_PROMPT, manual_chats, manual_project
from carryover.util import excerpt, parse_day, short_day, slugify


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


# --------------------------------------------------------------------------- #
# inspect
# --------------------------------------------------------------------------- #

def cmd_inspect(args) -> int:
    exp = ClaudeExport(args.export)
    projects = exp.projects
    if args.json:
        out = {
            "projects": [{
                "id": p.id, "name": p.name, "description": p.description,
                "knowledge_files": len(p.docs), "has_instructions": bool(p.instructions.strip()),
                "has_memory": bool(p.memory), "starter": p.is_starter,
                "updated_at": p.updated_at,
            } for p in projects],
            "conversations": len(exp.conversations),
            "conversations_linked_to_projects": exp.has_project_links,
            "has_memory_file": exp.has_memory_file,
        }
        if args.chats or args.search or args.title:
            out["chats"] = [_chat_row(c) for c in _filter_chats(exp, args)]
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if not (args.chats or args.search or args.title):
        print(f"Projects in {os.path.basename(args.export)} ({len(projects)}):\n")
        for p in projects:
            flags = []
            if p.instructions.strip():
                flags.append("instructions")
            if p.memory:
                flags.append("memory")
            if p.is_starter:
                flags.append("starter")
            print(f"  {p.name}")
            print(f"      id {p.id} · {len(p.docs)} knowledge file(s)"
                  f"{' · ' + ', '.join(flags) if flags else ''} · updated {short_day(p.updated_at) or '?'}")
        print(f"\nChats in export: {len(exp.conversations)}")
        if not exp.has_project_links:
            print("Note: this export does not say which chats belong to which project.\n"
                  "Find them with:  carryover inspect EXPORT --search \"keyword\"  (or --title REGEX)")
        if not exp.has_memory_file:
            print("Note: no memories.json in this export, so there is no memory to carry over.")
        return 0

    chats = _filter_chats(exp, args)
    for c in chats:
        r = _chat_row(c)
        print(f"{r['id']}  {r['started']}  {r['messages']:>4} msgs  {r['title']}")
        if args.verbose and r["opening"]:
            print(f"{'':38}  {r['opening']}")
    print(f"\n{len(chats)} chat(s)", file=sys.stderr)
    return 0


def _filter_chats(exp: ClaudeExport, args):
    if args.search or args.title:
        return exp.select_conversations(title_patterns=args.title or [], search_terms=args.search or [],
                                        skip_empty=False)
    return exp.select_conversations(all_chats=True, skip_empty=False)


def _chat_row(c) -> dict:
    opening = next((m.text for m in c.messages if m.role == "user" and m.text.strip()), "")
    return {"id": c.id, "title": c.title, "started": short_day(c.created_at) or "undated",
            "last_activity": short_day(c.updated_at), "messages": len(c.messages),
            "project_id": c.project_id, "opening": excerpt(opening, 100)}


# --------------------------------------------------------------------------- #
# pack
# --------------------------------------------------------------------------- #

def cmd_pack(args) -> int:
    exp = ClaudeExport(args.export, include_thinking=args.include_thinking)
    project = exp.find_project(args.project)
    since = parse_day(args.since) if args.since else None
    until = parse_day(args.until, end_of_day=True) if args.until else None
    chat_ids = list(args.chat or [])
    if args.chats_file:
        with open(args.chats_file, encoding="utf-8") as fh:
            chat_ids += [ln.split()[0] for ln in fh if ln.strip() and not ln.startswith("#")]
    convs = exp.select_conversations(
        project=project, ids=chat_ids, title_patterns=args.chat_title or [],
        search_terms=args.chat_search or [], all_chats=args.all_chats, since=since, until=until,
    )
    if not convs and not (chat_ids or args.chat_title or args.chat_search or args.all_chats):
        print("Note: no chats selected. The export doesn't link chats to projects, so choose them with\n"
              "  --chat-search KEYWORD, --chat-title REGEX, --chat ID, --chats-file FILE or --all-chats.\n"
              "Packing instructions, knowledge and memory only.", file=sys.stderr)

    out = args.output or f"{slugify(project.name)}.carryover"
    account_memory = exp.account_memory if args.include_account_memory else ""
    write_bundle(
        out, project, convs,
        source={"platform": PLATFORM, "export_file": os.path.basename(args.export)},
        account_memory=account_memory,
        options={"include_thinking": args.include_thinking,
                 "include_account_memory": bool(account_memory)},
        overwrite=args.force,
    )
    print(f"Bundle written: {out}")
    print(f"  {len(project.docs)} knowledge file(s), {len(convs)} chat(s), "
          f"project memory: {'yes' if project.memory else 'no'}, "
          f"account memory: {'yes' if account_memory else 'no'}")
    if args.zip:
        zpath = out.rstrip("/\\") + ".zip"
        zip_dir(out, zpath)
        print(f"  zipped: {zpath}")
    if convs:
        print(f"Next: carryover summarize {out} --api   (or let Claude write them with the skill)")
    print(f"Then: carryover unpack {out}")
    return 0


# --------------------------------------------------------------------------- #
# status / template
# --------------------------------------------------------------------------- #

def cmd_status(args) -> int:
    with Bundle(args.bundle) as b:
        missing = b.missing_summaries()
        info = {
            "bundle": os.path.abspath(b.root),
            "project": b.name,
            "source": b.manifest.get("source", {}),
            "knowledge_files": len(b.knowledge),
            "chats": len(b.conversations),
            "summaries_done": len(b.conversations) - len(missing),
            "project_context_done": bool(b.project_context),
            "missing": [{"title": c["title"], "transcript": c["transcript"], "write_summary_to": c["summary"],
                         "messages": c.get("messages", 0)} for c in missing],
        }
        if args.json:
            print(json.dumps(info, indent=2, ensure_ascii=False))
            return 0
        print(f"Project: {b.name}  ({info['bundle']})")
        print(f"  knowledge files: {info['knowledge_files']}  chats: {info['chats']}  "
              f"summaries: {info['summaries_done']}/{info['chats']}  "
              f"PROJECT_CONTEXT.md: {'done' if info['project_context_done'] else 'missing'}")
        if missing:
            print("\nChats still needing a summary (transcript -> summary file):")
            for m in info["missing"]:
                print(f"  {m['transcript']} -> {m['write_summary_to']}")
        return 0


def cmd_template(args) -> int:
    if args.kind == "conversation":
        print(CONVERSATION_TEMPLATE)
    else:
        print(PROJECT_CONTEXT_TEMPLATE)
    return 0


# --------------------------------------------------------------------------- #
# summarize
# --------------------------------------------------------------------------- #

def cmd_summarize(args) -> int:
    if not args.api:
        print("Summaries can be written two ways:\n"
              "  1. carryover summarize BUNDLE --api   (uses the Claude API; pip install 'carryover[api]')\n"
              "  2. Ask Claude with the carryover skill to write them; `carryover status BUNDLE` lists them.\n",
              file=sys.stderr)
        return cmd_status(argparse.Namespace(bundle=args.bundle, json=False))
    with Bundle(args.bundle) as b:
        if b.is_zip:
            return _err("summarize needs an unzipped bundle directory")
        call = make_api_caller(model=args.model, effort=args.effort, use_fallback=not args.no_fallback)
        result = summarize_bundle(b, call, force=args.force, workers=args.workers,
                                  project_context=not args.skip_project_context)
    print(f"Done: {result['summarized']} summarized, {len(result['failed'])} failed"
          f"{', PROJECT_CONTEXT.md written' if result['project_context'] else ''}.")
    return 1 if result["failed"] else 0


# --------------------------------------------------------------------------- #
# unpack
# --------------------------------------------------------------------------- #

def cmd_unpack(args) -> int:
    with Bundle(args.bundle) as b:
        out = args.output or f"{slugify(b.name)}-import-kit"
        result = build_kit(b, out, include_transcripts=not args.no_transcripts,
                           chunk_chars=args.chunk_chars, context_note=not args.no_context_note,
                           overwrite=args.force)
    print(f"Import kit written: {out}")
    print(f"  {len(result['knowledge_files'])} file(s) to upload, about {result['approx_tokens']:,} tokens")
    for w in result["warnings"]:
        print(f"  warning: {w}")
    if args.zip:
        zpath = out.rstrip("/\\") + ".zip"
        zip_dir(out, zpath)
        print(f"  zipped: {zpath}")
    print(f"Open {os.path.join(out, 'START_HERE.md')} and follow the steps in your new account.")
    return 0


# --------------------------------------------------------------------------- #
# no-export mode
# --------------------------------------------------------------------------- #

def cmd_handoff_prompt(args) -> int:
    print(HANDOFF_PROMPT)
    return 0


def _maybe_file(value):
    """Accept either a path to a file or literal text."""
    if value and os.path.isfile(value):
        with open(value, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return value or ""


def cmd_from_manual(args) -> int:
    handoff = _maybe_file(args.handoff)
    if args.handoff and not handoff.strip():
        return _err(f"{args.handoff} is empty")
    project = manual_project(
        name=args.name, description=_maybe_file(args.description),
        instructions=_maybe_file(args.instructions), knowledge=args.knowledge or [],
        memory=_maybe_file(args.memory),
    )
    chats = manual_chats(args.chat or [])
    out = args.output or f"{slugify(args.name)}.carryover"
    write_bundle(out, project, chats, source={"platform": "manual"}, project_context=handoff,
                 options={"mode": "manual"}, overwrite=args.force)
    print(f"Bundle written: {out}")
    print(f"  {len(project.docs)} knowledge file(s), {len(chats)} saved chat(s), "
          f"handoff document: {'yes' if handoff.strip() else 'no'}, memory: {'yes' if project.memory else 'no'}")
    if not handoff.strip():
        print("  tip: run `carryover handoff-prompt`, paste it into the old project, and pass the answer "
              "with --handoff", file=sys.stderr)
    print(f"Next: carryover unpack {out}")
    return 0


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="carryover", description="Move an AI project between accounts.")
    p.add_argument("--version", action="version", version=f"carryover {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect", help="list projects and chats in a claude.ai export")
    s.add_argument("export", help="claude.ai export .zip (or its unzipped folder)")
    s.add_argument("--chats", action="store_true", help="list chats instead of projects")
    s.add_argument("--search", action="append", metavar="TEXT", help="chats whose title or text contains TEXT")
    s.add_argument("--title", action="append", metavar="REGEX", help="chats whose title matches REGEX")
    s.add_argument("-v", "--verbose", action="store_true", help="show each chat's opening message")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("pack", help="turn one project from an export into a bundle")
    s.add_argument("export")
    s.add_argument("--project", required=True, help="project name or id (see `inspect`)")
    s.add_argument("-o", "--output", help="bundle directory (default: <project>.carryover)")
    g = s.add_argument_group("which chats to include (combined; the export has no chat-to-project link)")
    g.add_argument("--chat", action="append", metavar="ID", help="chat id (or 8+ character prefix)")
    g.add_argument("--chats-file", metavar="FILE", help="file with one chat id per line")
    g.add_argument("--chat-title", action="append", metavar="REGEX", help="chats whose title matches")
    g.add_argument("--chat-search", action="append", metavar="TEXT", help="chats mentioning TEXT")
    g.add_argument("--all-chats", action="store_true", help="every chat in the export")
    g.add_argument("--since", metavar="YYYY-MM-DD", help="only chats active on or after this day")
    g.add_argument("--until", metavar="YYYY-MM-DD", help="only chats started on or before this day")
    s.add_argument("--include-account-memory", action="store_true",
                   help="also carry account-wide memory (personal; off by default)")
    s.add_argument("--include-thinking", action="store_true", help="keep Claude's thinking text in transcripts")
    s.add_argument("--zip", action="store_true", help="also write <bundle>.zip")
    s.add_argument("--force", action="store_true", help="replace an existing bundle")
    s.set_defaults(func=cmd_pack)

    s = sub.add_parser("status", help="show a bundle's contents and missing summaries")
    s.add_argument("bundle")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("template", help="print a summary template")
    s.add_argument("kind", choices=["conversation", "project"])
    s.set_defaults(func=cmd_template)

    s = sub.add_parser("summarize", help="write chat summaries and PROJECT_CONTEXT.md")
    s.add_argument("bundle")
    s.add_argument("--api", action="store_true", help="use the Claude API")
    s.add_argument("--model", default=DEFAULT_MODEL)
    s.add_argument("--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    s.add_argument("--workers", type=int, default=4, help="chats summarized in parallel")
    s.add_argument("--force", action="store_true", help="redo summaries that already exist")
    s.add_argument("--skip-project-context", action="store_true")
    s.add_argument("--no-fallback", action="store_true", help="disable server-side refusal fallback")
    s.set_defaults(func=cmd_summarize)

    s = sub.add_parser("unpack", help="build an import kit for the target account")
    s.add_argument("bundle", help="bundle directory or .zip")
    s.add_argument("--target", default="claude-ai", choices=["claude-ai"])
    s.add_argument("-o", "--output", help="kit directory (default: <project>-import-kit)")
    s.add_argument("--no-transcripts", action="store_true", help="leave full transcripts out of the kit")
    s.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS,
                   help="max characters per transcript file")
    s.add_argument("--no-context-note", action="store_true",
                   help="keep custom instructions exactly as they were")
    s.add_argument("--zip", action="store_true", help="also write <kit>.zip")
    s.add_argument("--force", action="store_true", help="replace an existing kit")
    s.set_defaults(func=cmd_unpack)

    s = sub.add_parser("handoff-prompt", help="print the prompt to paste into the old project (no-export mode)")
    s.set_defaults(func=cmd_handoff_prompt)

    s = sub.add_parser("from-manual", help="build a bundle from hand-copied pieces (no-export mode)")
    s.add_argument("--name", required=True, help="project name")
    s.add_argument("--description", help="description text, or a file containing it")
    s.add_argument("--instructions", help="file with the copied custom instructions (or the text)")
    s.add_argument("--handoff", help="file with Claude's answer to `carryover handoff-prompt`")
    s.add_argument("--knowledge", action="append", metavar="PATH",
                   help="knowledge file or folder (repeatable)")
    s.add_argument("--memory", help="file with memory text to carry over (or the text)")
    s.add_argument("--chat", action="append", metavar="PATH",
                   help="a chat you copied into a .md/.txt file, or a folder of them (repeatable)")
    s.add_argument("-o", "--output", help="bundle directory (default: <name>.carryover)")
    s.add_argument("--force", action="store_true", help="replace an existing bundle")
    s.set_defaults(func=cmd_from_manual)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ExportError, BundleError, SummarizeError, ValueError, OSError, re.error) as exc:
        return _err(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
