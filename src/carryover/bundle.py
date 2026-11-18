"""The neutral, platform-independent bundle.

A bundle is a plain directory (optionally zipped) that any AI platform adapter
can read or write. Layout (see docs/BUNDLE_FORMAT.md):

    manifest.json
    project/instructions.md       custom instructions / system prompt
    project/description.md
    memory/project.md             project-scoped memory (if any)
    memory/account.md             account-wide memory (opt-in only)
    knowledge/<files>             project knowledge files (text)
    conversations/<stem>.json     normalised chat
    conversations/<stem>.md       readable transcript
    summaries/<stem>.md           per-chat summary (written by `summarize`)
    summaries/PROJECT_CONTEXT.md  whole-project handoff (written by `summarize`)
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from typing import List, Optional

from carryover import BUNDLE_FORMAT, BUNDLE_FORMAT_VERSION, __version__
from carryover.render import conversation_markdown
from carryover.util import now_iso, short_day, slugify, text_filename, unique_name

PROJECT_CONTEXT = "summaries/PROJECT_CONTEXT.md"


class BundleError(Exception):
    pass


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_bundle(
    out_dir: str,
    project,
    conversations: list,
    source: dict,
    account_memory: str = "",
    options: Optional[dict] = None,
    overwrite: bool = False,
) -> str:
    """Write a bundle directory. ``project`` / ``conversations`` are adapter
    objects (see adapters.claude_ai.Project / Conversation)."""
    if os.path.exists(out_dir):
        if not overwrite:
            raise BundleError(f"{out_dir} already exists (use --force to replace it)")
        if not os.path.isfile(os.path.join(out_dir, "manifest.json")):
            raise BundleError(f"refusing to replace {out_dir}: it is not a carryover bundle")
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    _write(os.path.join(out_dir, "project", "instructions.md"), project.instructions)
    _write(os.path.join(out_dir, "project", "description.md"), project.description)

    memory = {}
    if project.memory:
        _write(os.path.join(out_dir, "memory", "project.md"), project.memory + "\n")
        memory["project"] = "memory/project.md"
    if account_memory:
        _write(os.path.join(out_dir, "memory", "account.md"), account_memory + "\n")
        memory["account"] = "memory/account.md"

    knowledge = []
    taken: set = set()
    for doc in project.docs:
        name = unique_name(text_filename(doc.name), taken)
        _write(os.path.join(out_dir, "knowledge", name), doc.content)
        knowledge.append({
            "path": f"knowledge/{name}",
            "original_name": doc.name,
            "chars": len(doc.content),
            "created_at": doc.created_at,
        })

    convs = []
    stems: set = set()
    for c in conversations:
        stem = unique_name(f"{short_day(c.created_at) or 'undated'}_{slugify(c.title, 40)}_{c.id[:8]}", stems)
        data = c.to_dict()
        _write(os.path.join(out_dir, "conversations", stem + ".json"),
               json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        _write(os.path.join(out_dir, "conversations", stem + ".md"), conversation_markdown(data))
        convs.append({
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at,
            "updated_at": c.updated_at,
            "messages": len(c.messages),
            "json": f"conversations/{stem}.json",
            "transcript": f"conversations/{stem}.md",
            "summary": f"summaries/{stem}.md",
        })
    os.makedirs(os.path.join(out_dir, "summaries"), exist_ok=True)

    manifest = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_FORMAT_VERSION,
        "generator": f"carryover {__version__}",
        "created_at": now_iso(),
        "source": source,
        "project": {
            "name": project.name,
            "source_id": project.id,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "instructions": "project/instructions.md",
            "description": "project/description.md",
        },
        "memory": memory,
        "knowledge": knowledge,
        "conversations": convs,
        "project_context": PROJECT_CONTEXT,
        "options": options or {},
    }
    _write(os.path.join(out_dir, "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return out_dir


def zip_dir(src_dir: str, zip_path: str) -> str:
    base = os.path.basename(os.path.normpath(src_dir))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, src_dir)
                zf.write(full, os.path.join(base, rel))
    return zip_path


class Bundle:
    """Read access to a bundle directory or zip."""

    def __init__(self, path: str):
        self.source_path = path
        self._tmp: Optional[str] = None
        if os.path.isdir(path):
            self.root = path
        elif zipfile.is_zipfile(path):
            self._tmp = tempfile.mkdtemp(prefix="carryover-")
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    target = os.path.realpath(os.path.join(self._tmp, info.filename))
                    if not target.startswith(os.path.realpath(self._tmp) + os.sep):
                        raise BundleError(f"unsafe path in zip: {info.filename}")
                zf.extractall(self._tmp)
            self.root = self._find_root(self._tmp)
        else:
            raise BundleError(f"{path} is not a bundle directory or zip")
        mpath = os.path.join(self.root, "manifest.json")
        if not os.path.isfile(mpath):
            raise BundleError(f"no manifest.json in {path}")
        with open(mpath, encoding="utf-8") as fh:
            self.manifest = json.load(fh)
        if self.manifest.get("format") != BUNDLE_FORMAT:
            raise BundleError(f"{path} is not a carryover bundle")
        if int(self.manifest.get("format_version", 0)) > BUNDLE_FORMAT_VERSION:
            raise BundleError("bundle was made by a newer carryover; please upgrade")

    @staticmethod
    def _find_root(base: str) -> str:
        if os.path.isfile(os.path.join(base, "manifest.json")):
            return base
        for entry in os.listdir(base):
            cand = os.path.join(base, entry)
            if os.path.isfile(os.path.join(cand, "manifest.json")):
                return cand
        raise BundleError("zip does not contain a bundle (manifest.json not found)")

    @property
    def is_zip(self) -> bool:
        return self._tmp is not None

    def close(self) -> None:
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- paths & reading -----------------------------------------------------
    def path(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def exists(self, rel: str) -> bool:
        return bool(rel) and os.path.isfile(self.path(rel))

    def read(self, rel: str, default: str = "") -> str:
        if not self.exists(rel):
            return default
        with open(self.path(rel), encoding="utf-8") as fh:
            return fh.read()

    def write(self, rel: str, text: str) -> None:
        if self.is_zip:
            raise BundleError("this bundle is a zip; unzip it first to add summaries")
        _write(self.path(rel), text)

    @property
    def name(self) -> str:
        return self.manifest["project"]["name"]

    @property
    def instructions(self) -> str:
        return self.read(self.manifest["project"].get("instructions", "project/instructions.md"))

    @property
    def description(self) -> str:
        return self.read(self.manifest["project"].get("description", "project/description.md"))

    @property
    def project_memory(self) -> str:
        return self.read(self.manifest.get("memory", {}).get("project", "")).strip()

    @property
    def account_memory(self) -> str:
        return self.read(self.manifest.get("memory", {}).get("account", "")).strip()

    @property
    def knowledge(self) -> List[dict]:
        return list(self.manifest.get("knowledge", []))

    @property
    def conversations(self) -> List[dict]:
        return list(self.manifest.get("conversations", []))

    def conversation(self, entry: dict) -> dict:
        return json.loads(self.read(entry["json"], "{}"))

    def summary(self, entry: dict) -> str:
        return self.read(entry.get("summary", "")).strip()

    @property
    def project_context(self) -> str:
        return self.read(self.manifest.get("project_context", PROJECT_CONTEXT)).strip()

    def missing_summaries(self) -> List[dict]:
        return [c for c in self.conversations if not self.summary(c)]
