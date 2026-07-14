"""Shared library for the wiki doc-dependency system.

Every wiki article may declare a ``**Depends:**`` line listing the source files
it was compiled from, as repo-qualified paths/globs (repo = the ``~/src`` folder
name, which matches ``schema/topics.json`` sourceRepos). For example:

    **Depends:** infrastructure/__main__.py; infrastructure/datasembly_iac/modules/snowflake_iam/bots/**

This module is the single source of truth for parsing those lines and matching
changed files against them. The CLI (``wiki-deps``), the MCP server, the git
hooks, and the Cursor hook all import it so matching behavior never diverges.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

WIKI_ROOT = Path(os.environ.get("WIKI_ROOT", str(Path.home() / ".cursor" / "llm-wiki")))
WIKI_DIR = WIKI_ROOT / "wiki"
TOPICS_JSON = WIKI_ROOT / "schema" / "topics.json"
INDEX_PATH = WIKI_ROOT / "schema" / "doc-deps-index.json"

# Files under wiki/ that are not articles.
_NON_ARTICLES = {"index.md", "log.md"}

_DEPENDS_RE = re.compile(r"^\*\*Depends:\*\*\s*(.+?)\s*$", re.MULTILINE)
_UPDATED_RE = re.compile(r"^\*\*Updated:\*\*\s*(.+?)\s*$", re.MULTILINE)
_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_GLOB_CHARS = re.compile(r"[*?\[]")


@dataclass
class Doc:
    path: Path                      # absolute path to the .md file
    rel: str                        # path relative to WIKI_ROOT, e.g. wiki/infra/pulumi.md
    title: str
    updated: str
    depends: list[str] = field(default_factory=list)  # raw repo-qualified entries


@dataclass
class Match:
    repo: str
    file: str                       # repo-relative path of the changed file
    status: str                     # M/A/D/R... (annotation only)
    entry: str                      # the **Depends:** entry that matched


@dataclass
class DocHit:
    doc: str                        # doc path relative to WIKI_ROOT
    title: str
    updated: str
    matches: list[Match] = field(default_factory=list)


def source_repos() -> set[str]:
    """Tracked repo names, from topics.json sourceRepos (falls back to empty)."""
    try:
        data = json.loads(TOPICS_JSON.read_text(encoding="utf-8"))
        repos: set[str] = set()
        for topic in data.get("topics", {}).values():
            repos.update(topic.get("sourceRepos", []) or [])
        return repos
    except Exception:
        return set()


def _split_entries(raw: str) -> list[str]:
    # "(none)"/"none" is the explicit "no tracked-repo dependencies" sentinel.
    return [
        e.strip()
        for e in raw.split(";")
        if e.strip() and e.strip().lower() not in ("(none)", "none")
    ]


def parse_doc(path: Path) -> Doc:
    text = path.read_text(encoding="utf-8", errors="replace")
    title_m = _TITLE_RE.search(text)
    updated_m = _UPDATED_RE.search(text)
    depends: list[str] = []
    for m in _DEPENDS_RE.finditer(text):
        depends.extend(_split_entries(m.group(1)))
    return Doc(
        path=path,
        rel=os.path.relpath(path, WIKI_ROOT),
        title=title_m.group(1) if title_m else path.stem,
        updated=updated_m.group(1) if updated_m else "",
        depends=depends,
    )


def iter_docs(wiki_dir: Path | None = None) -> Iterable[Doc]:
    base = wiki_dir or WIKI_DIR
    if not base.exists():
        return
    for path in sorted(base.rglob("*.md")):
        if path.name in _NON_ARTICLES:
            continue
        yield parse_doc(path)


def split_entry(entry: str) -> tuple[str, str]:
    """Split a repo-qualified entry into (repo, repo_relative_pattern)."""
    entry = entry.strip().lstrip("/")
    repo, _, rest = entry.partition("/")
    return repo, rest


def _glob_to_regex(pattern: str) -> str:
    out = ["^"]
    i, n = 0, len(pattern)
    while i < n:
        if pattern[i : i + 3] == "**/":
            out.append("(?:.*/)?")
            i += 3
        elif pattern[i : i + 2] == "**":
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    out.append("$")
    return "".join(out)


def pattern_matches(pattern: str, rel_path: str) -> bool:
    """True if a repo-relative pattern matches a repo-relative changed path.

    - No glob chars -> exact file match OR directory-prefix match.
    - Glob chars    -> ** spans directories, * stays within one segment.
    """
    pattern = pattern.strip().rstrip("/") if pattern.strip() != "/" else pattern
    rel_path = rel_path.strip().lstrip("/")
    if not pattern:
        return False
    if not _GLOB_CHARS.search(pattern):
        return rel_path == pattern or rel_path.startswith(pattern + "/")
    return re.match(_glob_to_regex(pattern), rel_path) is not None


def docs_for_changes(changes: Iterable[tuple[str, str, str]], docs: list[Doc] | None = None) -> list[DocHit]:
    """Given (repo, repo_relative_path, status) changes, return dependent docs.

    Each returned DocHit lists which change(s) matched which **Depends:** entry.
    """
    doc_list = docs if docs is not None else list(iter_docs())
    change_list = list(changes)
    hits: list[DocHit] = []
    for doc in doc_list:
        matches: list[Match] = []
        for entry in doc.depends:
            e_repo, e_pat = split_entry(entry)
            for c_repo, c_path, c_status in change_list:
                if c_repo != e_repo:
                    continue
                if pattern_matches(e_pat, c_path):
                    matches.append(Match(repo=c_repo, file=c_path, status=c_status, entry=entry))
        if matches:
            hits.append(DocHit(doc=doc.rel, title=doc.title, updated=doc.updated, matches=matches))
    return hits


def build_index(tld: Path | None = None) -> dict:
    """Build the reverse/forward index. If tld is given, validate dep targets exist."""
    docs = list(iter_docs())
    forward: dict[str, dict] = {}
    reverse: dict[str, dict[str, list[str]]] = {}
    warnings: list[str] = []
    known = source_repos()
    for doc in docs:
        forward[doc.rel] = {"title": doc.title, "updated": doc.updated, "depends": doc.depends}
        for entry in doc.depends:
            repo, pat = split_entry(entry)
            reverse.setdefault(repo, {}).setdefault(pat, []).append(doc.rel)
            if known and repo not in known:
                warnings.append(f"{doc.rel}: unknown repo '{repo}' in entry '{entry}'")
            if tld is not None and not _GLOB_CHARS.search(pat):
                target = Path(tld) / repo / pat
                if not target.exists():
                    warnings.append(f"{doc.rel}: dep target missing on disk: {repo}/{pat}")
    from datetime import datetime, timezone

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "forward": forward,
        "reverse": reverse,
        "warnings": warnings,
    }


def parse_name_status(text: str, repo: str) -> list[tuple[str, str, str]]:
    """Parse `git diff --name-status` output into (repo, path, status) tuples.

    Renames/copies (``R100 old new`` / ``C75 old new``) yield two entries so a
    doc depending on either the old or new path is flagged.
    """
    changes: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0].strip()
        letter = code[0] if code else "M"
        if letter in ("R", "C") and len(parts) >= 3:
            changes.append((repo, parts[1].strip(), f"{letter}-from"))
            changes.append((repo, parts[2].strip(), f"{letter}-to"))
        elif len(parts) >= 2:
            changes.append((repo, parts[1].strip(), letter))
    return changes
