import json
import os
import zipfile

import pytest

from carryover.adapters.claude_ai import ClaudeExport, ExportError
from carryover.bundle import Bundle, BundleError
from carryover.cli import main
from carryover.summarize import (conversation_prompts, merge_prompt, split_text,
                                 summarize_bundle)

from conftest import PROJECT_ID


def walk_text(root):
    out = []
    for dirpath, _d, files in os.walk(root):
        for f in files:
            with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                out.append(fh.read())
    return "\n".join(out)


# --------------------------------------------------------------------------- export reading

def test_reads_zip_and_dir(export_zip, export_dir):
    for path in (export_zip, export_dir):
        exp = ClaudeExport(path)
        assert [p.name for p in exp.projects] == ["Thesis Writing", "Cooking"]
        assert len(exp.conversations) == 4
        assert exp.project_memory(PROJECT_ID).startswith("Thesis deadline")
        assert exp.account_memory == "User is a PhD student in Bangalore."
        assert not exp.has_project_links


def test_find_project(export_zip):
    exp = ClaudeExport(export_zip)
    assert exp.find_project("thesis writing").id == PROJECT_ID
    assert exp.find_project(PROJECT_ID).name == "Thesis Writing"
    assert exp.find_project("11111111-aaaa").name == "Thesis Writing"
    assert exp.find_project("cook").name == "Cooking"
    with pytest.raises(ExportError):
        exp.find_project("nope")


def test_message_parsing(export_zip):
    exp = ClaudeExport(export_zip)
    conv = exp.conversations[0]
    reply = conv.messages[1]
    assert "three sections" in reply.text
    assert "secret reasoning" not in reply.text  # thinking dropped by default
    assert "3.1 Background" in reply.text  # artifact content kept
    assert reply.files == ["figure.png"]
    assert conv.messages[0].attachments[0]["name"] == "ch3_draft.txt"
    assert "secret reasoning" in ClaudeExport(export_zip, include_thinking=True).conversations[0].messages[1].text


def test_selection(export_zip):
    exp = ClaudeExport(export_zip)
    thesis = exp.find_project("Thesis")
    titles = lambda cs: [c.title for c in cs]  # noqa: E731
    assert exp.select_conversations(project=thesis) == []  # no links in export
    assert titles(exp.select_conversations(search_terms=["thesis"])) == [
        "Thesis: chapter 3 structure", "Literature review GNN"]
    assert titles(exp.select_conversations(title_patterns=["^pasta"])) == ["Pasta recipe"]
    assert titles(exp.select_conversations(ids=["c0000003"])) == ["Literature review GNN"]
    assert len(exp.select_conversations(all_chats=True)) == 3  # empty chat skipped
    from carryover.util import parse_day
    assert titles(exp.select_conversations(all_chats=True, since=parse_day("2025-04-01"))) == [
        "Pasta recipe", "Literature review GNN"]
    assert titles(exp.select_conversations(all_chats=True, until=parse_day("2025-03-31", True))) == [
        "Thesis: chapter 3 structure"]


def test_project_links_used_when_present(tmp_path):
    from conftest import sample_export
    data = sample_export()
    data["conversations.json"][2]["project_uuid"] = PROJECT_ID
    for name, val in data.items():
        (tmp_path / name).write_text(json.dumps(val))
    exp = ClaudeExport(str(tmp_path))
    assert exp.has_project_links
    assert [c.title for c in exp.select_conversations(project=exp.find_project("Thesis"))] == [
        "Literature review GNN"]


def test_not_an_export(tmp_path):
    (tmp_path / "x.json").write_text("{}")
    with pytest.raises(ExportError):
        ClaudeExport(str(tmp_path))


# --------------------------------------------------------------------------- pack / bundle

def pack(export, out, *extra):
    return main(["pack", export, "--project", "Thesis", "-o", out, *extra])


def test_pack_bundle(export_zip, tmp_path):
    out = str(tmp_path / "thesis.carryover")
    assert pack(export_zip, out, "--chat-search", "thesis") == 0
    b = Bundle(out)
    assert b.name == "Thesis Writing"
    assert b.instructions == "Always write in British English. Be concise."
    assert b.project_memory.startswith("Thesis deadline")
    assert b.account_memory == ""  # opt-in only
    names = sorted(k["path"] for k in b.knowledge)
    assert names == ["knowledge/advisor_notes.pdf.txt", "knowledge/outline-2.md", "knowledge/outline.md"]
    assert [c["title"] for c in b.conversations] == ["Thesis: chapter 3 structure", "Literature review GNN"]
    md = b.read(b.conversations[0]["transcript"])
    assert "````" in md  # fence lengthened around content containing ```
    assert "figure.png" in md
    # never leak account PII
    blob = walk_text(out)
    assert "secret@example.com" not in blob and "+910000000000" not in blob
    assert len(b.missing_summaries()) == 2


def test_pack_account_memory_and_zip(export_zip, tmp_path):
    out = str(tmp_path / "b")
    assert pack(export_zip, out, "--include-account-memory", "--zip") == 0
    with Bundle(out + ".zip") as b:
        assert b.is_zip
        assert b.account_memory == "User is a PhD student in Bangalore."
        assert b.conversations == []
        with pytest.raises(BundleError):
            b.write("summaries/x.md", "nope")


def test_pack_refuses_overwrite(export_zip, tmp_path):
    out = str(tmp_path / "b")
    assert pack(export_zip, out) == 0
    assert pack(export_zip, out) == 2
    assert pack(export_zip, out, "--force") == 0
    other = tmp_path / "not-a-bundle"
    other.mkdir()
    assert pack(export_zip, str(other), "--force") == 2


# --------------------------------------------------------------------------- summarize

class FakeCaller:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if "<project>" in prompt:
            return "# Project context: Thesis Writing\n\n## Overview\nA thesis.\n"
        return "# Summary\n\n## What this chat was about\nDiscussed thesis work.\n"


def test_summarize_with_fake_api(export_zip, tmp_path):
    out = str(tmp_path / "b")
    pack(export_zip, out, "--chat-search", "thesis")
    fake = FakeCaller()
    with Bundle(out) as b:
        res = summarize_bundle(b, fake, workers=2, log=lambda s: None)
        assert res == {"summarized": 2, "failed": [], "project_context": True}
        assert b.missing_summaries() == []
        assert b.project_context.startswith("# Project context")
        # transcripts are passed as data, wrapped in tags
        assert any("<transcript>" in p and "message passing" in p for p in fake.prompts)
        assert "Always write in British English" in fake.prompts[-1]
        # idempotent: nothing left to do
        res2 = summarize_bundle(b, fake, log=lambda s: None)
        assert res2["summarized"] == 0 and res2["project_context"] is False


def test_long_transcripts_are_chunked_and_merged():
    text = "\n".join(f"--- User (t) ---\nline {i} " + "x" * 50 for i in range(200))
    parts = split_text(text, 2000)
    assert len(parts) > 1 and "".join(parts) == text
    header = {"title": "T", "dates": "d", "messages": 1, "transcript": "t.md"}
    prompts = conversation_prompts(text, header, chunk_chars=2000)
    assert len(prompts) == len(parts) and "part 1 of" in prompts[0]
    assert "Merge them" in merge_prompt(["a", "b"], header)


def test_api_caller_uses_fallback_and_effort():
    pytest.importorskip("anthropic")
    from carryover.summarize import make_api_caller

    captured = {}

    class Block:
        type = "text"
        text = "summary"

    class Msg:
        stop_reason = "end_turn"
        content = [Block()]

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return Msg()

    class Client:
        class beta:
            class messages:
                @staticmethod
                def stream(**kw):
                    captured.update(kw)
                    return Stream()

    call = make_api_caller(client=Client())
    assert call("hi") == "summary\n"
    assert captured["model"] == "claude-opus-5-5"
    assert captured["fallbacks"] == "default"
    assert captured["output_config"] == {"effort": "medium"}
    captured.clear()
    make_api_caller(model="claude-haiku-5-5", client=Client())("hi")
    assert "fallbacks" not in captured


# --------------------------------------------------------------------------- unpack

def test_unpack_without_summaries(export_zip, tmp_path):
    out = str(tmp_path / "b")
    kit = str(tmp_path / "kit")
    pack(export_zip, out, "--chat-search", "thesis", "--include-account-memory")
    assert main(["unpack", out, "-o", kit]) == 0
    kdir = os.path.join(kit, "3_upload_to_project_knowledge")
    files = sorted(os.listdir(kdir))
    assert files == ["00_PROJECT_CONTEXT.md", "01_CHAT_SUMMARIES.md", "advisor_notes.pdf.txt",
                     "outline-2.md", "outline.md", "zz_chat_transcripts_part01.md"]
    start = open(os.path.join(kit, "START_HERE.md")).read()
    assert "No AI summaries yet" in start
    instr = open(os.path.join(kit, "2_custom_instructions.md")).read()
    assert instr.startswith("Always write in British English.")
    assert "00_PROJECT_CONTEXT.md" in instr
    mem = open(os.path.join(kit, "4_memory.md")).read()
    assert "Thesis deadline" in mem and "PhD student" in mem
    stub = open(os.path.join(kdir, "00_PROJECT_CONTEXT.md")).read()
    assert "Thesis: chapter 3 structure" in stub


def test_unpack_with_summaries_and_chunking(export_zip, tmp_path):
    out = str(tmp_path / "b")
    pack(export_zip, out, "--chat-search", "thesis")
    with Bundle(out) as b:
        summarize_bundle(b, FakeCaller(), log=lambda s: None)
    kit = str(tmp_path / "kit")
    assert main(["unpack", out, "-o", kit, "--chunk-chars", "100", "--zip"]) == 0
    kdir = os.path.join(kit, "3_upload_to_project_knowledge")
    assert "zz_chat_transcripts_part02.md" in os.listdir(kdir)
    assert open(os.path.join(kdir, "00_PROJECT_CONTEXT.md")).read().startswith("# Project context")
    summaries = open(os.path.join(kdir, "01_CHAT_SUMMARIES.md")).read()
    assert "Discussed thesis work." in summaries
    assert "No AI summaries" not in open(os.path.join(kit, "START_HERE.md")).read()
    assert zipfile.is_zipfile(kit + ".zip")


def test_unpack_no_chats_no_transcripts(export_zip, tmp_path):
    out = str(tmp_path / "b")
    pack(export_zip, out)
    kit = str(tmp_path / "kit")
    assert main(["unpack", out, "-o", kit, "--no-context-note"]) == 0
    instr = open(os.path.join(kit, "2_custom_instructions.md")).read()
    assert "Carried-over" not in instr
    assert not any(f.startswith("zz_") for f in os.listdir(os.path.join(kit, "3_upload_to_project_knowledge")))


# --------------------------------------------------------------------------- cli smoke

def test_cli_inspect(export_zip, capsys):
    assert main(["inspect", export_zip]) == 0
    out = capsys.readouterr().out
    assert "Thesis Writing" in out and "does not say which chats" in out
    assert main(["inspect", export_zip, "--search", "pasta"]) == 0
    assert "Pasta recipe" in capsys.readouterr().out
    assert main(["inspect", export_zip, "--json", "--chats"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["chats"]) == 4 and data["projects"][0]["has_memory"]
    assert main(["inspect", export_zip, "--title", "("]) == 2  # bad regex -> clean error


def test_cli_status_and_template(export_zip, tmp_path, capsys):
    out = str(tmp_path / "b")
    pack(export_zip, out, "--all-chats")
    capsys.readouterr()
    assert main(["status", out, "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["chats"] == 3 and st["summaries_done"] == 0 and len(st["missing"]) == 3
    assert main(["template", "project"]) == 0
    assert "## Open threads" in capsys.readouterr().out


# --------------------------------------------------------------------------- no-export mode

def test_handoff_prompt(capsys):
    assert main(["handoff-prompt"]) == 0
    out = capsys.readouterr().out
    assert "## Chat summaries" in out and "## Open threads" in out
    assert "{name}" not in out and "{count}" not in out


def test_from_manual(tmp_path):
    (tmp_path / "instr.md").write_text("Be concise.")
    (tmp_path / "handoff.md").write_text("# Project context: Thesis\n\n## Overview\nGNN thesis.\n")
    kdir = tmp_path / "files"
    kdir.mkdir()
    (kdir / "outline.md").write_text("# Outline")
    (kdir / "paper.pdf").write_bytes(b"%PDF-1.4 \x00\xff binary")
    (kdir / ".DS_Store").write_bytes(b"junk")
    chats = tmp_path / "chats"
    chats.mkdir()
    (chats / "chapter_3_plan.md").write_text("User: plan ch3\nClaude: three sections")
    out = str(tmp_path / "b")
    assert main(["from-manual", "--name", "Thesis", "--instructions", str(tmp_path / "instr.md"),
                 "--handoff", str(tmp_path / "handoff.md"), "--knowledge", str(kdir),
                 "--memory", "Deadline in December", "--chat", str(chats), "-o", out]) == 0
    with Bundle(out) as b:
        assert b.instructions == "Be concise."
        assert b.project_context.startswith("# Project context: Thesis")
        assert sorted(k["original_name"] for k in b.knowledge) == ["outline.md", "paper.pdf"]
        assert b.project_memory == "Deadline in December"
        assert b.conversations[0]["title"] == "chapter 3 plan"
    kit = str(tmp_path / "kit")
    assert main(["unpack", out, "-o", kit]) == 0
    kd = os.path.join(kit, "3_upload_to_project_knowledge")
    with open(os.path.join(kd, "paper.pdf"), "rb") as fh:
        assert fh.read() == b"%PDF-1.4 \x00\xff binary"  # binary kept intact
    assert open(os.path.join(kd, "00_PROJECT_CONTEXT.md")).read().startswith("# Project context: Thesis")
    assert "Saved chat" in open(os.path.join(kd, "zz_chat_transcripts_part01.md")).read()
    assert "No AI summaries" not in open(os.path.join(kit, "START_HERE.md")).read()
    assert "00_PROJECT_CONTEXT.md" in open(os.path.join(kit, "2_custom_instructions.md")).read()


def test_from_manual_handoff_only(tmp_path):
    (tmp_path / "h.md").write_text("# Project context: X\n")
    out = str(tmp_path / "b")
    assert main(["from-manual", "--name", "X", "--handoff", str(tmp_path / "h.md"), "-o", out]) == 0
    kit = str(tmp_path / "kit")
    assert main(["unpack", out, "-o", kit]) == 0
    instr = open(os.path.join(kit, "2_custom_instructions.md")).read()
    assert "00_PROJECT_CONTEXT.md" in instr and "01_CHAT_SUMMARIES" not in instr
    assert main(["from-manual", "--name", "Y", "--knowledge", str(tmp_path / "missing"),
                 "-o", str(tmp_path / "c")]) == 2


# --------------------------------------------------------------------------- split (manifest) exports

def _write_split_export(folder, include=("projects", "conversations", "memories", "light_metadata")):
    from conftest import sample_export
    data = sample_export()
    folder.mkdir(exist_ok=True)
    files = []
    for i, cat in enumerate(["light_metadata", "projects", "memories", "design_chats", "frames", "conversations"]):
        fname = f"{cat}-000.zip"
        files.append({"batch_index": i, "export_url": "https://example.invalid/x", "category": cat,
                      "part": 0, "filename": fname})
        if cat not in include:
            continue
        with zipfile.ZipFile(folder / fname, "w") as zf:
            if cat == "projects":  # one JSON per project, generic names
                for j, proj in enumerate(data["projects.json"]):
                    zf.writestr(f"export/{j:04d}.json", json.dumps(proj))
            elif cat == "conversations":  # JSON Lines
                zf.writestr("conversations.jsonl", "\n".join(json.dumps(c) for c in data["conversations.json"]))
            elif cat == "memories":
                zf.writestr("memories.json", json.dumps(data["memories.json"][0]))
            else:
                zf.writestr("users.json", json.dumps(data["users.json"]))
    manifest = folder / "manifest-abc-2026-11-18.json"
    manifest.write_text(json.dumps({"instructions": "Download each file", "total_files": 6,
                                    "data_files": files, "version": "1.0"}))
    return str(manifest)


def test_split_export_via_manifest_and_folder(tmp_path):
    manifest = _write_split_export(tmp_path / "dl")
    for path in (manifest, str(tmp_path / "dl")):
        exp = ClaudeExport(path)
        assert [p.name for p in exp.projects] == ["Thesis Writing", "Cooking"]
        assert len(exp.conversations) == 4
        assert exp.project_memory(PROJECT_ID).startswith("Thesis deadline")
    out = str(tmp_path / "b")
    assert main(["pack", manifest, "--project", "Thesis", "--chat-search", "thesis", "-o", out]) == 0
    assert len(Bundle(out).conversations) == 2


def test_manifest_without_parts_explains(tmp_path, capsys):
    manifest = _write_split_export(tmp_path / "dl", include=())
    assert main(["inspect", manifest]) == 2
    err = capsys.readouterr().err
    assert "download list" in err and "projects-000.zip" in err and "conversations-000.zip" in err


# --------------------------------------------------------------------------- plan / pack-all

def test_plan_suggests_and_pack_all(export_zip, tmp_path, capsys):
    plan = str(tmp_path / "plan.txt")
    assert main(["plan", export_zip, "-o", plan]) == 0
    text = open(plan).read()
    assert "## Project: Thesis Writing" in text and "## Project: Cooking" in text
    thesis_part = text.split("## Project: Thesis Writing")[1].split("## Project:")[0]
    assert "Thesis: chapter 3 structure" in thesis_part  # keyword guess from project name
    assert main(["plan", export_zip, "-o", plan]) == 2  # never overwrite edits silently

    # user edits: move pasta chat to Cooking by pasting its bare title; skip lit review
    edited = []
    for line in text.splitlines():
        if "Pasta recipe" in line or "Literature review" in line:
            continue
        edited.append(line)
        if line.startswith("# id: 2222"):
            edited.append("Pasta recipe")
            edited.append("Last message 3 days ago")  # noise copied from the web page
    edited = "\n".join(edited).replace("\n## Skip\n", "\n## Skip\nc0000003\n")
    open(plan, "w").write(edited)

    out = str(tmp_path / "out")
    assert main(["pack-all", export_zip, plan, "-o", out, "--zip"]) == 0
    cooking = Bundle(os.path.join(out, "cooking.carryover"))
    assert [c["title"] for c in cooking.conversations] == ["Pasta recipe"]
    thesis = Bundle(os.path.join(out, "thesis-writing.carryover"))
    assert [c["title"] for c in thesis.conversations] == ["Thesis: chapter 3 structure"]
    assert os.path.isfile(os.path.join(out, "thesis-writing-kit", "START_HERE.md"))
    assert zipfile.is_zipfile(os.path.join(out, "cooking-kit.zip"))
    assert not os.path.exists(os.path.join(out, "general-chats.carryover"))  # nothing left over


def test_pack_all_reports_problems(export_zip, tmp_path, capsys):
    plan = tmp_path / "plan.txt"
    plan.write_text("## Project: Cooking\n# id: 22222222-aaaa-bbbb-cccc-000000000002\n"
                    "No such chat title\nc0000002\n\n## General chats\nc0000002\n")
    out = str(tmp_path / "out")
    assert main(["pack-all", export_zip, str(plan), "-o", out]) == 2
    err = capsys.readouterr().err
    assert "No such chat title" in err and "several sections" in err
    plan.write_text("## Project: Cooking\nNo such chat title\nc0000002\n## General chats\nc0000001\n")
    assert main(["pack-all", export_zip, str(plan), "-o", out, "--ignore-problems"]) == 0
    general = Bundle(os.path.join(out, "general-chats.carryover"))
    assert [c["title"] for c in general.conversations] == ["Thesis: chapter 3 structure"]


def test_plan_from_pages(export_zip, tmp_path, capsys):
    pages = tmp_path / "pages"
    pages.mkdir()
    # text as copied from claude.ai project pages: titles mixed with page noise
    (pages / "Thesis Writing.txt").write_text(
        "Thesis Writing\nChats\nThesis: chapter 3 structure\nLast message 2 months ago\n"
        "Literature review G…\nUpdated 5 months ago\n")
    (pages / "cooking.txt").write_text("Pasta recipe\n")
    (pages / "Unknown project.txt").write_text("whatever\n")
    plan = str(tmp_path / "plan.txt")
    assert main(["plan", export_zip, "-o", plan, "--from-pages", str(pages)]) == 0
    err = capsys.readouterr().err
    assert "Unknown project.txt matches no project" in err
    text = open(plan).read()
    thesis = text.split("## Project: Thesis Writing")[1].split("## Project:")[0]
    assert "Thesis: chapter 3 structure" in thesis and "Literature review GNN" in thesis  # cut-off title
    assert "Pasta recipe" in text.split("## Project: Cooking")[1].split("## General")[0]
    assert main(["pack-all", export_zip, plan, "-o", str(tmp_path / "out"), "--no-kits"]) == 0


def test_plan_star_takes_remaining_chats(export_zip, tmp_path):
    plan = tmp_path / "plan.txt"
    plan.write_text("## Project: Cooking\n# id: 22222222-aaaa-bbbb-cccc-000000000002\nc0000002 | Pasta recipe\n\n"
                    "## General chats\n*\n\n## Skip\nc0000003\n")
    out = str(tmp_path / "out")
    assert main(["pack-all", export_zip, str(plan), "-o", out, "--no-kits"]) == 0
    general = Bundle(os.path.join(out, "general-chats.carryover"))
    assert [c["title"] for c in general.conversations] == ["Thesis: chapter 3 structure"]
