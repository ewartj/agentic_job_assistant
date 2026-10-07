"""cv-server: exposes CV evidence and past cover letters over MCP (stdio).

Note: mcp 2.x renamed FastMCP -> MCPServer. Most tutorials (and the
Phase 2 plan) still say `from mcp.server.fastmcp import FastMCP`.
"""
import json
import re
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

mcp = MCPServer("cv-server")


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


CV = _load("cv.json")
LETTERS = _load("letters.json")

STOPWORDS = {"a", "an", "and", "the", "of", "in", "on", "for", "to", "with", "or", "experience", "using"}


def _terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9+#.\-]*", query.lower())
    return [w for w in words if w not in STOPWORDS]


def _score(entry: dict[str, Any], terms: list[str]) -> int:
    """Tag hits count double; text hits once. Short terms (e.g. "r", "ci") must match a
    whole word, longer ones match a word prefix so "test" finds "testing"."""
    text = entry["text"].lower()
    tags = " | ".join(entry.get("tags", [])).lower()
    score = 0
    for term in terms:
        pattern = rf"\b{re.escape(term)}\b" if len(term) <= 2 else rf"\b{re.escape(term)}"
        if re.search(pattern, tags):
            score += 2
        elif re.search(pattern, text):
            score += 1
    return score


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    """Tags are a search aid only - don't send them to the model."""
    return {k: entry[k] for k in ("id", "category", "text", "source")}


# --- Tools (model-controlled) -----------------------------------------------
# The docstring is what the model sees as the tool's description, and the type
# hints become the tool's JSON input schema.

@mcp.tool()
def list_past_applications() -> list[dict[str, Any]]:
    """List every job application on file (company, role, date, type) without the letter text.

    Call this first to discover exact company names before calling
    get_past_letters, or to see which kinds of roles the user has applied for.
    """
    return [
        {k: letter[k] for k in ("id", "company", "role", "date", "category")}
        for letter in LETTERS
    ]


@mcp.tool()
def get_cv_evidence(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search the user's CV for evidence supporting one skill, technology or job requirement.

    Each result is a single, self-contained claim about the user's experience,
    projects, skills, education or publications, with an `id` and the `source`
    document it came from.

    Call this once per requirement (e.g. "Terraform", then "mentoring",
    then "NLP") rather than with one long query - keyword matching works best
    with 1-3 focused terms. Cite the returned `id`s for every claim you make
    about the user. An empty list means no keyword match: try a synonym or
    broader term (e.g. "IaC" -> "infrastructure") before concluding the user
    lacks that experience.

    Args:
        query: A skill, technology or requirement, e.g. "Terraform", "AWS Lambda",
            "mentoring juniors", "OMOP".
        limit: Maximum number of entries to return (default 5).
    """
    terms = _terms(query)
    scored = [(_score(entry, terms), entry) for entry in CV]
    ranked = sorted((pair for pair in scored if pair[0] > 0), key=lambda pair: -pair[0])
    return [_public(entry) for _, entry in ranked[:limit]]


@mcp.tool()
def get_past_letters(company: str) -> list[dict[str, Any]]:
    """Get the full text of cover letters and application answers the user previously wrote to a company.

    Matching is a case-insensitive substring on the company name ("future"
    matches "Our Future Health"). If you don't know the exact name, call
    list_past_applications first. Returns an empty list if there is no match.

    Use these for the user's tone, structure and phrasing, not as a source of
    facts - cite get_cv_evidence for claims. Letters with status "draft" may
    contain errors; check `notes` for known issues.

    Args:
        company: Full or partial company name, e.g. "BBC", "Genomics England", "future health".
    """
    needle = company.strip().lower()
    return [letter for letter in LETTERS if needle in letter["company"].lower()]


# --- Resources (application/user-controlled) ---------------------------------
# The model can't call these. The client lists them (resources/list) and chooses
# what to read (resources/read) and put into context.

@mcp.resource(
    "cv://full",
    name="Full CV",
    description="Every CV evidence entry, for putting the whole CV in context instead of searching it.",
    mime_type="application/json",
)
def full_cv() -> str:
    return json.dumps([_public(entry) for entry in CV], indent=2, ensure_ascii=False)


@mcp.resource(
    "cv://entry/{entry_id}",
    name="CV entry",
    description="A single CV evidence entry by id, e.g. cv://entry/cv-005.",
    mime_type="application/json",
)
def cv_entry(entry_id: str) -> str:
    for entry in CV:
        if entry["id"] == entry_id:
            return json.dumps(_public(entry), indent=2, ensure_ascii=False)
    raise ValueError(f"No CV entry with id {entry_id!r}")


def main() -> None:
    mcp.run()  # stdio by default
