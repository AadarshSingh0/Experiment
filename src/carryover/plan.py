"""Plan which chats go with which project, then pack everything at once.

The claude.ai export does not record which project a chat belongs to, but the
project pages on claude.ai do. ``carryover plan`` writes an editable text file
with one section per project, pre-filled with best guesses; the user fixes it
(moving lines, or pasting chat titles copied from each project's page), and
``carryover pack-all`` packs every project from it.

Plan file format::

    ## Project: Thesis
    # id: 01a180b5-...
    01a180b5 | 2026-11-13 | Corrected LaTeX paper        <- id prefix wins
    Colloquium slides and paper checks                  <- or just a title

    ## General chats          (moved too, as a project of their own)
    ## Skip                   (not moved)

Lines starting with ``#`` are comments. Blank lines are ignored.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from carryover.util import parse_time, short_day

GENERAL = "General chats"
SKIP = "Skip"

_STOP = set("""
a an and are as at be but by for from has have how i in into is it its me my of on or our so that the
this to was we what when where which who why will with you your project projects chat chats new
using use help about can do does get make need want work working paper notes misc untitled
http https www com org net html github gitlab tree blob main master src modules module
""".split())

_ID_LINE = re.compile(r"^([0-9a-f]{8})(?:[0-9a-f-]{0,28})\s*(?:\||$)", re.IGNORECASE)
# Lines copied from a claude.ai project page that are not chat titles.
_NOISE = re.compile(r"^(last message|updated|created|starred|recents?|chats?|"
                    r"\d+\s+(seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago)\b",
                    re.IGNORECASE)


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9+\-]{2,}", (text or "").lower()) if w not in _STOP]


def _keywords(project) -> Counter:
    kw: Counter = Counter()
    for w in _words(project.name):
        kw[w] += 3
    for doc in project.docs:
        for w in _words(re.sub(r"\.[a-z0-9]+$", "", doc.name, flags=re.I)):
            kw[w] += 1
    for w in _words(project.description):
        kw[w] += 1
    return kw


def suggest(export) -> Dict[str, List]:
    """Return {project_id: [conversations]} plus GENERAL for unmatched chats."""
    projects = [p for p in export.projects if not p.is_starter]
    keywords = {p.id: _keywords(p) for p in projects}
    out: Dict[str, List] = {p.id: [] for p in projects}
    out[GENERAL] = []
    for c in export.conversations:
        if not c.messages:
            continue
        if c.project_id and c.project_id in out:
            out[c.project_id].append(c)
            continue
        title = Counter(_words(c.title))
        opening = next((m.text for m in c.messages if m.role == "user" and m.text.strip()), "")
        body = Counter(_words(opening[:2000]))
        created = parse_time(c.created_at)
        scores: List[Tuple[float, str]] = []
        for p in projects:
            kw = keywords[p.id]
            score = sum(3 * title[w] * wt + min(body[w], 3) * wt for w, wt in kw.items())
            pc = parse_time(p.created_at)
            if pc and created and created < pc:
                score *= 0.3  # a chat older than the project is less likely to be in it
            scores.append((score, p.id))
        scores.sort(reverse=True)
        best = scores[0] if scores else (0, None)
        runner = scores[1] if len(scores) > 1 else (0, None)
        if best[0] >= 6 and best[0] >= 1.5 * runner[0]:
            out[best[1]].append(c)
        else:
            out[GENERAL].append(c)
    return out


def chat_line(c) -> str:
    return f"{c.id[:8]} | {short_day(c.created_at) or 'undated':10} | {len(c.messages):>4} msgs | {c.title}"


def _norm(text: str) -> str:
    text = re.sub(r"[\u2026]|\.\.\.$", "", text or "")
    return re.sub(r"\s+", " ", text).strip().lower()


def _match_project(name: str, projects: list):
    target = _norm(name)
    slug = re.sub(r"[^a-z0-9]+", "", target)
    for p in projects:
        if _norm(p.name) == target or re.sub(r"[^a-z0-9]+", "", _norm(p.name)) == slug:
            return p
    return None


def from_pages(export, folder: str) -> Tuple[Dict[str, List], List[str], List[Tuple[str, str]]]:
    """Assign chats using text copied from each project's page on claude.ai.

    ``folder`` holds one text file per project, named after the project
    (e.g. ``Thesis.txt``), containing whatever was copied from that page.
    Returns (groups, unknown_files, unmatched (file, line))."""
    import os
    projects = [p for p in export.projects if not p.is_starter]
    groups: Dict[str, List] = {p.id: [] for p in projects}
    by_title: Dict[str, List] = {}
    for c in export.conversations:
        by_title.setdefault(_norm(c.title), []).append(c)
    titles = list(by_title)
    assigned: Dict[str, str] = {}
    unknown, unmatched = [], []
    for fname in sorted(os.listdir(folder)):
        full = os.path.join(folder, fname)
        if fname.startswith(".") or not os.path.isfile(full):
            continue
        project = _match_project(os.path.splitext(fname)[0], projects)
        if project is None:
            unknown.append(fname)
            continue
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh.read().splitlines()]
        for line in lines:
            if len(line) < 3 or _NOISE.match(line) or line.lower() == _norm(project.name):
                continue
            key = _norm(line)
            hits = by_title.get(key, [])
            if not hits and len(key) >= 15:  # titles cut short on the page
                cands = [t for t in titles if t.startswith(key)]
                if len(cands) == 1:
                    hits = by_title[cands[0]]
            if not hits:
                unmatched.append((fname, line))
                continue
            for c in hits:  # several chats can share a title (e.g. "Untitled chat")
                if c.id not in assigned and c.messages:
                    assigned[c.id] = project.id
                    groups[project.id].append(c)
    groups[GENERAL] = [c for c in export.conversations if c.messages and c.id not in assigned]
    for g in groups.values():
        g.sort(key=lambda c: c.created_at or "")
    return groups, unknown, unmatched


def write_plan(export, path: str, groups: Optional[Dict[str, List]] = None) -> dict:
    groups = groups if groups is not None else suggest(export)
    lines = [
        "# carryover plan: which chats move with which project.",
        "#",
        "# The lines under each project are GUESSES. Check them against the project's page",
        "# on claude.ai (it lists the project's chats): move lines between sections, delete",
        "# wrong ones, or paste chat titles copied from the project page.",
        "# A line can be an 8-character chat id (as below) or an exact chat title.",
        f"# '## {GENERAL}' are moved as one extra project. '## {SKIP}' are not moved.",
        "# A line with just * means: every chat not listed in any other section.",
        "# Delete a whole '## Project:' section to leave that project out.",
        "# Then run:  carryover pack-all EXPORT THIS_FILE -o OUTPUT_FOLDER",
        "",
    ]
    counts = {}
    for p in export.projects:
        if p.is_starter:
            continue
        chats = groups.get(p.id, [])
        counts[p.name] = len(chats)
        lines += [f"## Project: {p.name}", f"# id: {p.id}"]
        lines += [chat_line(c) for c in chats]
        lines.append("")
    lines += [f"## {GENERAL}", "# Chats that matched no project. Move them up, or to Skip."]
    lines += [chat_line(c) for c in groups[GENERAL]]
    lines += ["", f"## {SKIP}", ""]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    return {"projects": counts, "general": len(groups[GENERAL])}


@dataclass
class PlanSection:
    kind: str  # "project" | "general" | "skip"
    name: str
    project_id: Optional[str] = None
    chats: List = field(default_factory=list)
    rest: bool = False  # a "*" line: every chat not listed in any other section


@dataclass
class Plan:
    sections: List[PlanSection]
    unmatched: List[Tuple[int, str]]
    ambiguous: List[Tuple[int, str]]
    duplicates: List[Tuple[str, List[str]]]


def read_plan(export, path: str) -> Plan:
    by_prefix: Dict[str, List] = {}
    by_title: Dict[str, List] = {}
    for c in export.conversations:
        by_prefix.setdefault(c.id[:8].lower(), []).append(c)
        by_title.setdefault(c.title.strip().lower(), []).append(c)
    projects = {p.id: p for p in export.projects}

    sections: List[PlanSection] = []
    unmatched, ambiguous = [], []
    current: Optional[PlanSection] = None
    with open(path, encoding="utf-8") as fh:
        raw_lines = fh.read().splitlines()
    for no, raw in enumerate(raw_lines, 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("##"):
            head = line.lstrip("#").strip()
            if head.lower().startswith("project:"):
                current = PlanSection("project", head.split(":", 1)[1].strip())
            elif head.lower() == GENERAL.lower():
                current = PlanSection("general", GENERAL)
            elif head.lower() == SKIP.lower():
                current = PlanSection("skip", SKIP)
            else:
                raise ValueError(f"{path}:{no}: unknown section {line!r}")
            sections.append(current)
            continue
        if line.startswith("#"):
            m = re.match(r"#\s*id:\s*(\S+)", line)
            if m and current is not None and current.kind == "project":
                current.project_id = m.group(1)
            continue
        if current is None:
            raise ValueError(f"{path}:{no}: chat listed before any '## ' section")
        if line == "*":
            current.rest = True
            continue
        m = _ID_LINE.match(line)
        hits = by_prefix.get(m.group(1).lower(), []) if m else []
        if not hits:
            title = line.split("|")[-1].strip() if "|" in line else line
            hits = by_title.get(title.lower(), [])
        if len(hits) == 1:
            current.chats.append(hits[0])
        elif len(hits) > 1:
            ambiguous.append((no, line))
        elif not _NOISE.match(line):
            unmatched.append((no, line))

    for s in sections:
        if s.kind != "project":
            continue
        if s.project_id not in projects:
            matches = [p for p in export.projects if p.name.strip().lower() == s.name.strip().lower()]
            if len(matches) != 1:
                raise ValueError(f"plan section 'Project: {s.name}' does not match exactly one project; "
                                 "keep its '# id:' line")
            s.project_id = matches[0].id

    rest = [s for s in sections if s.rest]
    if len(rest) > 1:
        raise ValueError("only one section may contain the '*' (all remaining chats) line")
    if rest:
        listed = {c.id for s in sections for c in s.chats}
        rest[0].chats += [c for c in export.conversations if c.messages and c.id not in listed]

    owners: Dict[str, List[str]] = {}
    for s in sections:
        for c in s.chats:
            owners.setdefault(c.id, []).append(s.name)
    duplicates = [(cid, names) for cid, names in owners.items() if len(set(names)) > 1]
    for s in sections:  # de-duplicate within a section, keep order
        seen = set()
        s.chats = [c for c in s.chats if not (c.id in seen or seen.add(c.id))]
    return Plan(sections, unmatched, ambiguous, duplicates)
