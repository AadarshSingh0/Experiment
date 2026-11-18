"""Read the claude.ai data export (Settings -> Privacy -> Export data).

The export is a zip with (at least) these JSON files:

* ``projects.json``      - projects: uuid, name, description, prompt_template
                           (the custom instructions) and docs (knowledge files
                           with their extracted text).
* ``conversations.json`` - every chat: uuid, name, created_at, updated_at,
                           chat_messages[...].
* ``memories.json``      - conversations_memory (account-wide) and
                           project_memories {project_uuid: text}. Not present
                           in every export.
* ``users.json``         - account details. Never copied into a bundle.

Known gap: conversations do not record which project they belonged to, so the
caller chooses which chats travel with a project. If a future export adds a
link (``project_uuid`` / ``project.uuid``), it is used automatically.

The parser is deliberately tolerant: unknown fields are ignored and missing
ones fall back to sensible defaults.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from carryover.util import parse_time

PLATFORM = "claude.ai"


class ExportError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def _load_json_files(path: str) -> dict:
    """Return {basename: parsed_json} for the JSON files we care about."""
    wanted = {"projects.json", "conversations.json", "memories.json", "users.json"}
    found: dict = {}
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in files:
                if name in wanted and name not in found:
                    with open(os.path.join(root, name), "r", encoding="utf-8") as fh:
                        found[name] = json.load(fh)
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                base = info.filename.rsplit("/", 1)[-1]
                if base in wanted and base not in found:
                    with zf.open(info) as fh:
                        found[base] = json.loads(fh.read().decode("utf-8"))
    else:
        raise ExportError(f"{path} is neither a directory nor a zip file")
    if "projects.json" not in found and "conversations.json" not in found:
        raise ExportError(
            f"{path} does not look like a claude.ai export "
            "(no projects.json or conversations.json found)"
        )
    return found


# --------------------------------------------------------------------------- #
# Normalised data
# --------------------------------------------------------------------------- #

@dataclass
class KnowledgeDoc:
    name: str
    content: str
    created_at: Optional[str] = None
    source_id: Optional[str] = None


@dataclass
class Project:
    id: str
    name: str
    description: str
    instructions: str
    docs: List[KnowledgeDoc]
    created_at: Optional[str]
    updated_at: Optional[str]
    is_starter: bool = False
    memory: str = ""


@dataclass
class Message:
    role: str  # "user" | "assistant"
    text: str
    created_at: Optional[str] = None
    attachments: List[dict] = field(default_factory=list)  # {name, type, content}
    files: List[str] = field(default_factory=list)  # names of binary uploads

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "created_at": self.created_at,
            "text": self.text,
            "attachments": self.attachments,
            "files": self.files,
        }


@dataclass
class Conversation:
    id: str
    title: str
    created_at: Optional[str]
    updated_at: Optional[str]
    messages: List[Message]
    source_summary: str = ""
    project_id: Optional[str] = None

    def search_text(self) -> str:
        parts = [self.title]
        for m in self.messages:
            parts.append(m.text)
            parts.extend(a.get("name", "") for a in m.attachments)
            parts.extend(m.files)
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_summary": self.source_summary,
            "messages": [m.to_dict() for m in self.messages],
        }


def _block_text(block: dict, include_thinking: bool) -> str:
    kind = block.get("type")
    if kind == "text":
        return block.get("text") or ""
    if kind == "thinking":
        return f"[thinking]\n{block.get('thinking', '')}" if include_thinking else ""
    if kind == "tool_use":
        name = block.get("name") or "tool"
        inp = block.get("input")
        # Artifacts / files Claude produced are worth keeping verbatim.
        if isinstance(inp, dict) and isinstance(inp.get("content"), str) and inp["content"].strip():
            title = inp.get("title") or inp.get("id") or inp.get("path") or name
            return f"[{name}: {title}]\n{inp['content']}"
        return f"[used tool: {name}]"
    if kind == "tool_result":
        return ""
    return ""


def _parse_message(raw: dict, include_thinking: bool) -> Optional[Message]:
    sender = (raw.get("sender") or raw.get("role") or "").lower()
    role = "user" if sender in ("human", "user") else "assistant"
    content = raw.get("content")
    text = ""
    if isinstance(content, list) and content:
        pieces = [_block_text(b, include_thinking) for b in content if isinstance(b, dict)]
        text = "\n\n".join(p for p in pieces if p.strip())
    if not text.strip():
        text = raw.get("text") or ""
    attachments = []
    for att in raw.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        attachments.append({
            "name": att.get("file_name") or att.get("name") or "attachment",
            "type": att.get("file_type") or "",
            "content": att.get("extracted_content") or "",
        })
    files = []
    for f in (raw.get("files") or []) + (raw.get("files_v2") or []):
        if isinstance(f, dict):
            fname = f.get("file_name") or f.get("name")
            if fname and fname not in files:
                files.append(fname)
    if not text.strip() and not attachments and not files:
        return None
    return Message(role=role, text=text, created_at=raw.get("created_at"),
                   attachments=attachments, files=files)


def _project_link(raw: dict) -> Optional[str]:
    if isinstance(raw.get("project_uuid"), str):
        return raw["project_uuid"]
    proj = raw.get("project")
    if isinstance(proj, dict) and isinstance(proj.get("uuid"), str):
        return proj["uuid"]
    return None


class ClaudeExport:
    """A parsed claude.ai export."""

    def __init__(self, path: str, include_thinking: bool = False):
        self.path = path
        self.include_thinking = include_thinking
        data = _load_json_files(path)
        self._raw_projects = data.get("projects.json") or []
        self._raw_conversations = data.get("conversations.json") or []
        self._raw_memories = data.get("memories.json")
        if not isinstance(self._raw_projects, list):
            raise ExportError("projects.json is not a list")
        if not isinstance(self._raw_conversations, list):
            raise ExportError("conversations.json is not a list")
        self._conversations: Optional[List[Conversation]] = None

    # -- memory --------------------------------------------------------------
    def _memory_record(self) -> dict:
        mem = self._raw_memories
        if isinstance(mem, list):
            merged: dict = {"conversations_memory": "", "project_memories": {}}
            for item in mem:
                if not isinstance(item, dict):
                    continue
                if item.get("conversations_memory"):
                    merged["conversations_memory"] = item["conversations_memory"]
                if isinstance(item.get("project_memories"), dict):
                    merged["project_memories"].update(item["project_memories"])
            return merged
        return mem if isinstance(mem, dict) else {}

    @property
    def has_memory_file(self) -> bool:
        return self._raw_memories is not None

    @property
    def account_memory(self) -> str:
        return (self._memory_record().get("conversations_memory") or "").strip()

    def project_memory(self, project_id: str) -> str:
        pm = self._memory_record().get("project_memories") or {}
        val = pm.get(project_id) if isinstance(pm, dict) else None
        if isinstance(val, dict):  # tolerate {"memory": "..."} shapes
            val = val.get("memory") or val.get("text") or ""
        return (val or "").strip()

    # -- projects ------------------------------------------------------------
    @property
    def projects(self) -> List[Project]:
        out = []
        for raw in self._raw_projects:
            if not isinstance(raw, dict):
                continue
            pid = raw.get("uuid") or raw.get("id") or ""
            docs = []
            for d in raw.get("docs") or []:
                if not isinstance(d, dict):
                    continue
                docs.append(KnowledgeDoc(
                    name=d.get("filename") or d.get("file_name") or d.get("name") or "document.txt",
                    content=d.get("content") or "",
                    created_at=d.get("created_at"),
                    source_id=d.get("uuid"),
                ))
            out.append(Project(
                id=pid,
                name=raw.get("name") or "Untitled project",
                description=raw.get("description") or "",
                instructions=raw.get("prompt_template") or "",
                docs=docs,
                created_at=raw.get("created_at"),
                updated_at=raw.get("updated_at"),
                is_starter=bool(raw.get("is_starter_project")),
                memory=self.project_memory(pid),
            ))
        return out

    def find_project(self, query: str) -> Project:
        projects = self.projects
        q = query.strip()
        ql = q.lower()
        for p in projects:
            if p.id == q:
                return p
        exact = [p for p in projects if p.name.lower() == ql]
        if len(exact) == 1:
            return exact[0]
        prefix = [p for p in projects if len(q) >= 6 and p.id.startswith(q)]
        if len(prefix) == 1:
            return prefix[0]
        partial = exact or [p for p in projects if ql in p.name.lower()]
        if len(partial) == 1:
            return partial[0]
        if not partial:
            raise ExportError(f"no project matches {query!r}. Run `carryover inspect` to list projects.")
        listing = "\n".join(f"  {p.id}  {p.name}" for p in partial)
        raise ExportError(f"{query!r} matches several projects; pass the id instead:\n{listing}")

    # -- conversations -------------------------------------------------------
    @property
    def conversations(self) -> List[Conversation]:
        if self._conversations is None:
            convs = []
            for raw in self._raw_conversations:
                if not isinstance(raw, dict):
                    continue
                msgs = [m for m in (
                    _parse_message(r, self.include_thinking)
                    for r in raw.get("chat_messages") or [] if isinstance(r, dict)
                ) if m is not None]
                convs.append(Conversation(
                    id=raw.get("uuid") or raw.get("id") or "",
                    title=(raw.get("name") or "").strip() or "Untitled chat",
                    created_at=raw.get("created_at"),
                    updated_at=raw.get("updated_at"),
                    messages=msgs,
                    source_summary=(raw.get("summary") or "").strip(),
                    project_id=_project_link(raw),
                ))
            convs.sort(key=lambda c: c.created_at or "")
            self._conversations = convs
        return self._conversations

    @property
    def has_project_links(self) -> bool:
        return any(c.project_id for c in self.conversations)

    def select_conversations(
        self,
        project: Optional[Project] = None,
        ids: Iterable[str] = (),
        title_patterns: Iterable[str] = (),
        search_terms: Iterable[str] = (),
        all_chats: bool = False,
        since=None,
        until=None,
        skip_empty: bool = True,
    ) -> List[Conversation]:
        """Union of every selector given, then filtered by date range.

        With no selector at all, chats linked to ``project`` are used (only
        possible when the export records project links)."""
        ids = [i.strip() for i in ids if i.strip()]
        title_res = [re.compile(p, re.IGNORECASE) for p in title_patterns]
        terms = [t.lower() for t in search_terms if t.strip()]

        chosen = []
        for c in self.conversations:
            hit = False
            if all_chats:
                hit = True
            elif project is not None and c.project_id and c.project_id == project.id:
                hit = True
            elif any(c.id == i or (len(i) >= 8 and c.id.startswith(i)) for i in ids):
                hit = True
            elif any(r.search(c.title) for r in title_res):
                hit = True
            elif terms:
                blob = c.search_text().lower()
                hit = any(t in blob for t in terms)
            if not hit:
                continue
            when = parse_time(c.updated_at) or parse_time(c.created_at)
            if since and when and when < since:
                continue
            created = parse_time(c.created_at) or when
            if until and created and created > until:
                continue
            if skip_empty and not c.messages:
                continue
            chosen.append(c)
        return chosen
