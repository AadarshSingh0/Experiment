import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PROJECT_ID = "11111111-aaaa-bbbb-cccc-000000000001"
OTHER_PROJECT_ID = "22222222-aaaa-bbbb-cccc-000000000002"


def _msg(sender, text, when, **extra):
    m = {"uuid": f"m-{when}", "sender": sender, "text": text, "created_at": when,
         "content": [{"type": "text", "text": text}], "attachments": [], "files": []}
    m.update(extra)
    return m


def sample_export() -> dict:
    projects = [
        {
            "uuid": PROJECT_ID,
            "name": "Thesis Writing",
            "description": "PhD thesis on graph neural networks",
            "prompt_template": "Always write in British English. Be concise.",
            "is_private": True,
            "is_starter_project": False,
            "created_at": "2025-01-10T09:00:00.000000Z",
            "updated_at": "2025-06-01T09:00:00.000000Z",
            "creator": {"uuid": "u1", "full_name": "Test User"},
            "docs": [
                {"uuid": "d1", "filename": "outline.md", "content": "# Outline\n1. Intro\n2. Methods",
                 "created_at": "2025-01-11T09:00:00Z"},
                {"uuid": "d2", "filename": "advisor_notes.pdf", "content": "Extracted PDF text here",
                 "created_at": "2025-01-12T09:00:00Z"},
                {"uuid": "d3", "filename": "outline.md", "content": "duplicate name",
                 "created_at": "2025-01-13T09:00:00Z"},
            ],
        },
        {
            "uuid": OTHER_PROJECT_ID,
            "name": "Cooking",
            "description": "",
            "prompt_template": "",
            "is_starter_project": False,
            "created_at": "2025-02-01T09:00:00Z",
            "updated_at": "2025-02-01T09:00:00Z",
            "docs": [],
        },
    ]
    conversations = [
        {
            "uuid": "c0000001-0000-0000-0000-000000000001",
            "name": "Thesis: chapter 3 structure",
            "summary": "",
            "created_at": "2025-03-01T10:00:00.123456Z",
            "updated_at": "2025-03-02T11:00:00Z",
            "account": {"uuid": "u1"},
            "chat_messages": [
                _msg("human", "Help me structure chapter 3 on message passing.", "2025-03-01T10:00:00Z",
                     attachments=[{"file_name": "ch3_draft.txt", "file_type": "txt", "file_size": 10,
                                   "extracted_content": "Draft text with ``` backticks"}]),
                {"uuid": "m2", "sender": "assistant", "text": "", "created_at": "2025-03-01T10:01:00Z",
                 "content": [{"type": "thinking", "thinking": "secret reasoning"},
                             {"type": "text", "text": "Use three sections: background, model, results."},
                             {"type": "tool_use", "name": "artifacts",
                              "input": {"id": "plan", "title": "Chapter plan", "content": "3.1 Background"}}],
                 "attachments": [], "files": [{"file_name": "figure.png"}]},
            ],
        },
        {
            "uuid": "c0000002-0000-0000-0000-000000000002",
            "name": "Pasta recipe",
            "created_at": "2025-04-01T10:00:00Z",
            "updated_at": "2025-04-01T10:05:00Z",
            "chat_messages": [
                _msg("human", "Quick pasta recipe?", "2025-04-01T10:00:00Z"),
                _msg("assistant", "Boil pasta, add garlic and oil.", "2025-04-01T10:01:00Z"),
            ],
        },
        {
            "uuid": "c0000003-0000-0000-0000-000000000003",
            "name": "Literature review GNN",
            "created_at": "2025-05-01T10:00:00Z",
            "updated_at": "2025-05-03T10:00:00Z",
            "chat_messages": [
                _msg("human", "Find key papers for my thesis literature review.", "2025-05-01T10:00:00Z"),
                _msg("assistant", "Start with GCN (Kipf & Welling 2017).", "2025-05-01T10:01:00Z"),
            ],
        },
        {"uuid": "c0000004-0000-0000-0000-000000000004", "name": "", "created_at": "2025-05-05T10:00:00Z",
         "updated_at": "2025-05-05T10:00:00Z", "chat_messages": []},
    ]
    memories = [{
        "conversations_memory": "User is a PhD student in Bangalore.",
        "project_memories": {PROJECT_ID: "Thesis deadline is December 2025. Advisor prefers short chapters."},
        "account_uuid": "u1",
    }]
    users = [{"uuid": "u1", "full_name": "Test User", "email_address": "secret@example.com",
              "verified_phone_number": "+910000000000"}]
    return {"projects.json": projects, "conversations.json": conversations,
            "memories.json": memories, "users.json": users}


@pytest.fixture
def export_zip(tmp_path):
    path = tmp_path / "data-2025-06-01.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in sample_export().items():
            zf.writestr(name, json.dumps(data))
    return str(path)


@pytest.fixture
def export_dir(tmp_path):
    d = tmp_path / "export"
    d.mkdir()
    for name, data in sample_export().items():
        (d / name).write_text(json.dumps(data), encoding="utf-8")
    return str(d)
