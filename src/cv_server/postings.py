"""Job postings saved as markdown files, pasted in by hand, with an optional YAML header:

    ---
    company: Acme Health
    role: Senior Data Scientist
    url: https://example.com/jobs/123
    saved: '2026-10-19'
    ---
    (the posting text, pasted as-is)

The header is for you, not the model: the agent sends only the text below it.
"""
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

FIELDS = ("company", "role", "url", "saved")

# Names become file paths, so this also blocks tricks like "../../.env".
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
FRONT_MATTER = re.compile(r"---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """(header, body). Text without a header comes back unchanged with an empty header."""
    match = FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta = yaml.safe_load(match[1]) or {}
    if not isinstance(meta, dict):
        raise ValueError("posting header must be 'key: value' lines")
    return meta, text[match.end():].lstrip("\r\n")


def posting_path(directory: Path, name: str) -> Path:
    if not NAME.fullmatch(name):
        raise ValueError(f"Invalid posting name {name!r}: use letters, digits, '_' and '-', e.g. acme_ds")
    return directory / f"{name}.md"


def list_postings(directory: Path) -> list[dict[str, Any]]:
    """Name and header fields of every saved posting. Read on each call, so new files show up."""
    postings = []
    for path in sorted(directory.glob("*.md")):
        meta, _ = split_front_matter(path.read_text(encoding="utf-8"))
        # str() because YAML turns an unquoted 2026-10-19 into a date.
        postings.append({"name": path.stem} | {k: None if meta.get(k) is None else str(meta[k]) for k in FIELDS})
    return postings


def save_posting(directory: Path, name: str, text: str, replace: bool = False, **meta: str | None) -> Path:
    """Write a posting with a header. An existing posting is only overwritten with
    replace=True; its header fields are kept unless new values are given."""
    path = posting_path(directory, name)
    if not text.strip():
        raise ValueError("posting text is empty")
    old: dict[str, Any] = {}
    if path.exists():
        if not replace:
            raise FileExistsError(f"{path} already exists - pick another name, or use --replace")
        old, _ = split_front_matter(path.read_text(encoding="utf-8"))
    header = old | {k: v for k, v in meta.items() if v} | {"saved": date.today().isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    head = yaml.safe_dump(header, sort_keys=False, allow_unicode=True)
    path.write_text(f"---\n{head}---\n{text.strip()}\n", encoding="utf-8")
    return path
