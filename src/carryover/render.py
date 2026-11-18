"""Render normalised conversations as readable Markdown."""

from __future__ import annotations

from carryover.util import fence, short_day, short_time

ROLE_LABEL = {"user": "User", "assistant": "Claude", "transcript": "Saved chat"}


def conversation_markdown(conv: dict, heading_level: int = 1) -> str:
    h = "#" * heading_level
    msgs = conv.get("messages") or []
    lines = [
        f"{h} {conv.get('title') or 'Untitled chat'}",
        "",
        f"- Conversation ID: `{conv.get('id', '')}`",
        f"- Started: {short_time(conv.get('created_at')) or 'unknown'}"
        f" · Last activity: {short_time(conv.get('updated_at')) or 'unknown'}",
        f"- Messages: {len(msgs)}",
        "",
    ]
    for m in msgs:
        label = ROLE_LABEL.get(m.get("role"), m.get("role", "?"))
        stamp = short_time(m.get("created_at"))
        lines.append(f"{h}# {label}" + (f" · {stamp}" if stamp else ""))
        lines.append("")
        text = (m.get("text") or "").strip()
        if text:
            lines.append(text)
            lines.append("")
        for att in m.get("attachments") or []:
            name = att.get("name") or "attachment"
            content = (att.get("content") or "").strip()
            if content:
                lines.append(f"**Attached file: {name}**")
                lines.append("")
                lines.append(fence(content))
            else:
                lines.append(f"**Attached file: {name}** (no text content in export)")
            lines.append("")
        for fname in m.get("files") or []:
            lines.append(f"**Uploaded file: {fname}** (binary; not included in export)")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def conversation_plaintext(conv: dict) -> str:
    """Compact transcript used as model input for summarisation."""
    out = [f"Title: {conv.get('title')}",
           f"Dates: {short_day(conv.get('created_at'))} to {short_day(conv.get('updated_at'))}", ""]
    for m in conv.get("messages") or []:
        label = ROLE_LABEL.get(m.get("role"), m.get("role", "?"))
        out.append(f"--- {label} ({short_time(m.get('created_at'))}) ---")
        if m.get("text"):
            out.append(m["text"].strip())
        for att in m.get("attachments") or []:
            out.append(f"[attached file: {att.get('name')}]")
            if att.get("content"):
                out.append(att["content"].strip())
        for fname in m.get("files") or []:
            out.append(f"[uploaded file: {fname}]")
        out.append("")
    return "\n".join(out)
