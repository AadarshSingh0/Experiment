"""Small helpers shared across the package."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".html", ".htm", ".css",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".rb",
    ".php", ".c", ".h", ".cpp", ".hpp", ".cs", ".swift", ".sh", ".bash", ".sql",
    ".r", ".m", ".tex", ".bib", ".log",
}


def slugify(text: str, max_len: int = 50) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].rstrip("-")) or "untitled"


def safe_filename(name: str, max_len: int = 120) -> str:
    """Make a filename safe on every OS while keeping it recognisable."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = "untitled"
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 10:
            name = stem[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]
    return name


def text_filename(name: str) -> str:
    """Knowledge docs in an export hold extracted *text*. Give non-text
    originals (e.g. report.pdf) a .txt suffix so the file type is honest."""
    name = safe_filename(name)
    lower = name.lower()
    if any(lower.endswith(ext) for ext in TEXT_EXTENSIONS):
        return name
    return name + ".txt"


def unique_name(name: str, taken: set) -> str:
    if name not in taken:
        taken.add(name)
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    i = 2
    while True:
        candidate = f"{stem}-{i}.{ext}" if ext else f"{stem}-{i}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
        i += 1


def parse_time(value) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        # Python < 3.11 cannot parse fractional seconds of arbitrary length.
        m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", v)
        if not m:
            return None
        frac = (m.group(2) or ".0")[:7].ljust(7, "0")
        try:
            dt = datetime.fromisoformat(m.group(1) + frac + m.group(3))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_day(value: str, end_of_day: bool = False) -> datetime:
    """Parse YYYY-MM-DD (or a full ISO time) given on the command line."""
    dt = parse_time(value if "T" in value else value + ("T23:59:59" if end_of_day else "T00:00:00"))
    if dt is None:
        raise ValueError(f"not a date: {value!r} (use YYYY-MM-DD)")
    return dt


def short_time(value) -> str:
    dt = parse_time(value)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def short_day(value) -> str:
    dt = parse_time(value)
    return dt.strftime("%Y-%m-%d") if dt else ""


def fence(content: str, lang: str = "") -> str:
    """Wrap content in a code fence that cannot be closed by the content itself."""
    longest = max((len(m) for m in re.findall(r"`{3,}", content)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{content.rstrip()}\n{ticks}"


def excerpt(text: str, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
