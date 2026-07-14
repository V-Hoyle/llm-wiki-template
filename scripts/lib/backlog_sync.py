#!/usr/bin/env python3
"""Shared helpers for branch bootstrap and transcript export."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

WIKI_ROOT = Path.home() / ".cursor" / "llm-wiki"
DATASSEMBLY = Path.home() / "Documents" / "Github" / "Datasembly"
TRANSCRIPTS_ROOT = Path.home() / ".cursor" / "projects"
DEFAULT_AUTHOR = "rylan"
DEFAULT_SINCE_DAYS = 60

DOC_GLOBS = (
    "*.md",
    "docs/**",
    "AGENTS*.md",
    "README*",
    ".cursor/agents/**",
    ".cursor/rules/**",
)

SECRET_PATTERNS = re.compile(
    r"(api[_-]?key|secret|password|token|authorization)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
SK_PATTERN = re.compile(r"sk-[A-Za-z0-9]{10,}")
BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE)

REPO_CONFIG: dict[str, dict[str, str]] = {
    "compass": {"topic": "compass", "base": "develop"},
    "compass-mcp-server": {"topic": "compass-mcp", "base": "main"},
    "datahub": {"topic": "data-platform", "base": "main"},
    "treasury": {"topic": "data-platform", "base": "main"},
    "airflow": {"topic": "orchestration", "base": "main"},
    "infrastructure": {"topic": "infrastructure", "base": "main"},
    "ops": {"topic": "infrastructure", "base": "master"},
    "exports": {"topic": "data-platform", "base": "main"},
    "halfpipe": {"topic": "data-platform", "base": "main"},
}

SKIP_BRANCHES = frozenset({"develop", "main", "master", "HEAD"})

KEYWORDS = re.compile(
    r"decision|runbook|architecture|agents|compass|treasury|airflow|"
    r"infrastructure|bootstrap|wiki|mcp|datahub|alert|eval|golden",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RepoRef:
    name: str
    path: Path
    topic: str
    base: str


def slugify(value: str, max_len: int = 80) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:max_len] or "unknown"


def redact_secrets(text: str) -> str:
    text = SECRET_PATTERNS.sub("REDACTED", text)
    text = SK_PATTERN.sub("REDACTED", text)
    text = BEARER_PATTERN.sub("Bearer REDACTED", text)
    return text


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def parse_since(since_days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=since_days)


def append_manifest(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_manifest_ids(path: Path, key: str) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)[key])
            except (json.JSONDecodeError, KeyError):
                continue
    return ids


def file_matches_doc_patterns(path: str) -> bool:
    lower = path.lower()
    if lower.endswith(".md"):
        return True
    if "/docs/" in lower or lower.startswith("docs/"):
        return True
    if "agents" in lower and lower.endswith(".md"):
        return True
    if "readme" in Path(path).name.lower():
        return True
    if "/.cursor/" in lower and lower.endswith(".md"):
        return True
    return False


def is_secret_path(path: str) -> bool:
    return bool(re.search(r"\.env|credentials|secret|\.pem|id_rsa", path, re.I))


def iter_repo_refs(repos: Iterable[str] | None = None) -> list[RepoRef]:
    refs: list[RepoRef] = []
    names = list(repos) if repos else list(REPO_CONFIG.keys())
    for name in names:
        cfg = REPO_CONFIG.get(name)
        if not cfg:
            continue
        path = DATASSEMBLY / name
        if not (path / ".git").exists():
            continue
        refs.append(RepoRef(name=name, path=path, topic=cfg["topic"], base=cfg["base"]))
    return refs


def branch_last_author_date(repo: Path, branch: str, author: str, since: datetime) -> str | None:
    out = run_git(
        repo,
        "log",
        branch,
        f"--author={author}",
        f"--since={since.date().isoformat()}",
        "-1",
        "--format=%cI",
    )
    return out or None


def branches_touched_by_author(
    repo: Path, author: str, since: datetime, base: str
) -> list[str]:
    raw = run_git(
        repo,
        "log",
        "--all",
        f"--author={author}",
        f"--since={since.date().isoformat()}",
        "--format=%D",
    )
    found: set[str] = set()
    for line in raw.splitlines():
        for part in line.split(","):
            part = part.strip()
            if not part or part.startswith("tag:"):
                continue
            part = part.removeprefix("origin/").removeprefix("HEAD -> ").strip()
            if part in SKIP_BRANCHES or part.startswith("("):
                continue
            if "->" in part:
                part = part.split("->")[-1].strip()
            if part:
                found.add(part)

    local = run_git(repo, "branch", "--format=%(refname:short)")
    for branch in local.splitlines():
        branch = branch.strip()
        if not branch or branch in SKIP_BRANCHES:
            continue
        if branch_last_author_date(repo, branch, author, since):
            found.add(branch)

    stable = [b for b in found if b != base and not b.startswith("deploy-")]
    return sorted(stable)


def branch_merge_status(repo: Path, branch: str, base: str) -> str:
    merged = run_git(repo, "branch", "--merged", base, "--format=%(refname:short)")
    names = {line.strip() for line in merged.splitlines() if line.strip()}
    if branch in names:
        return "merged"
    upstream = run_git(repo, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}")
    if upstream:
        return "pushed"
    return "local-only"


def diff_doc_files(repo: Path, branch: str, base: str) -> list[str]:
    if not run_git(repo, "rev-parse", "--verify", branch):
        return []
    if not run_git(repo, "rev-parse", "--verify", base):
        base = "HEAD"
    merge_base = run_git(repo, "merge-base", base, branch) or run_git(repo, "rev-parse", base)
    if not merge_base:
        return []
    names = run_git(repo, "diff", "--name-only", f"{merge_base}..{branch}")
    return sorted({n for n in names.splitlines() if n and file_matches_doc_patterns(n)})


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
