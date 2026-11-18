"""No-export mode.

For accounts with no "Export data" button (e.g. organization accounts where
the owner hasn't allowed member exports). The user:

1. pastes HANDOFF_PROMPT into a chat inside the old project, and saves
   Claude's answer as a Markdown file;
2. copies the project instructions and downloads the knowledge files by hand;
3. runs `carryover from-manual`, which turns those pieces into a normal bundle.
"""

from __future__ import annotations

import os
from typing import Iterable, List

from carryover.adapters.claude_ai import Conversation, KnowledgeDoc, Message, Project
from carryover.summarize import PROJECT_CONTEXT_TEMPLATE

HANDOFF_PROMPT = """\
I'm moving this project to a different Claude account and can't export my data, \
so I need a complete handoff document. A new Claude will continue this project \
from it with no access to any of these chats.

1. If you can search or reference my past chats, go through every chat in this \
project. Search with several different keywords and list recent chats until \
you've covered them all. If you can't access past chats, say so in one line at \
the top, then work from the project instructions, project knowledge, what you \
remember about me and this project, and this chat.
2. Write the handoff as one Markdown document, as a downloadable file if you can. \
Use the template below. Be concrete: decisions with their reasons, facts, names, \
numbers, file names, my preferences, outputs produced and unfinished work. Don't \
invent anything. Mark anything you're unsure about with (unsure).
3. After the template, add a section "## Chat summaries" with one entry per chat \
you found, oldest first: the chat title, its date, and 3-6 bullet points.
4. Finally, add a section "## Memory" listing everything you remember about me \
and this project that is relevant to continuing it.

Template:

""" + PROJECT_CONTEXT_TEMPLATE.replace("{name}", "<project name>").replace(
    "built from {count} earlier chats", "written in the old account"
).replace(
    "> Full transcripts and per-chat summaries are in the project knowledge.\n",
    "> Per-chat summaries are at the end of this document.\n",
)


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _expand(paths: Iterable[str]) -> List[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                out += [os.path.join(root, f) for f in sorted(files) if not f.startswith(".")]
        elif os.path.isfile(p):
            out.append(p)
        else:
            raise FileNotFoundError(f"no such file or folder: {p}")
    return out


def manual_project(name: str, description: str = "", instructions: str = "",
                   knowledge: Iterable[str] = (), memory: str = "") -> Project:
    docs = []
    for path in _expand(knowledge):
        with open(path, "rb") as fh:
            docs.append(KnowledgeDoc(name=os.path.basename(path), content="", data=fh.read()))
    return Project(id="manual", name=name, description=description, instructions=instructions,
                   docs=docs, created_at=None, updated_at=None, memory=memory.strip())


def manual_chats(paths: Iterable[str]) -> List[Conversation]:
    """Chats the user copied out by hand (one .md/.txt file per chat)."""
    convs = []
    for i, path in enumerate(_expand(paths), 1):
        text = _read_text(path)
        title = os.path.splitext(os.path.basename(path))[0].replace("_", " ").strip() or f"Chat {i}"
        convs.append(Conversation(id=f"manual-{i:04d}", title=title, created_at=None, updated_at=None,
                                  messages=[Message(role="transcript", text=text)]))
    return convs
