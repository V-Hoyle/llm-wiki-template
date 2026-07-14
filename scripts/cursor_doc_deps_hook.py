#!/usr/bin/env python3
"""Cursor postToolUse hook: when the agent edits a tracked source file, inject a
reminder listing the wiki docs that depend on it.

Reads the hook JSON on stdin and (only for edit-like tools that touched a file
under a tracked repo in $SRC_TLD) prints {"additional_context": "..."}.
Fails open: any error / no match -> no output, exit 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from doc_deps import docs_for_changes, source_repos
except Exception:
    sys.exit(0)

SRC_TLD = Path(os.environ.get("SRC_TLD", str(Path.home() / "src"))).expanduser()
_EDIT_TOOL_RE = re.compile(r"(write|edit|apply|create|multiedit)", re.I)
_READ_ONLY_RE = re.compile(r"^(read|tabread|grep|glob|search|list)$", re.I)
_PATH_KEYS = {"path", "file_path", "filepath", "target_file", "file", "abs_path", "absolute_path"}


def _tool_name(payload: dict) -> str:
    for k in ("tool_name", "toolName", "tool", "name"):
        v = payload.get(k)
        if isinstance(v, str):
            return v
    return ""


def _collect_paths(obj, out: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k.lower() in _PATH_KEYS:
                out.add(v)
            else:
                _collect_paths(v, out)
    elif isinstance(obj, list):
        for it in obj:
            _collect_paths(it, out)
    elif isinstance(obj, str):
        # Fallback: absolute-looking path strings.
        if obj.startswith("/") and len(obj) > 3:
            out.add(obj)


def _to_repo_relative(p: str, repos: set[str]) -> tuple[str, str] | None:
    try:
        ap = Path(p).expanduser().resolve()
    except Exception:
        return None
    try:
        rel = ap.relative_to(SRC_TLD)
    except Exception:
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    repo = parts[0]
    if repos and repo not in repos:
        return None
    return repo, "/".join(parts[1:])


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    tool = _tool_name(payload)
    if tool and (_READ_ONLY_RE.match(tool) or not _EDIT_TOOL_RE.search(tool)):
        return 0

    candidates: set[str] = set()
    _collect_paths(payload, candidates)
    if not candidates:
        return 0

    repos = source_repos()
    changes: set[tuple[str, str, str]] = set()
    for c in candidates:
        rr = _to_repo_relative(c, repos)
        if rr:
            changes.add((rr[0], rr[1], "M"))
    if not changes:
        return 0

    hits = docs_for_changes(sorted(changes))
    if not hits:
        return 0

    lines = ["The file(s) you just edited are documented in the local LLM wiki. "
             "Consider updating these docs (and their **Depends:** lines if files moved/were deleted):"]
    for h in hits:
        files = sorted({m.file for m in h.matches})
        lines.append(f"  - {h.title} ({h.doc}) — depends on: {', '.join(files)}")
    lines.append("After updating, run: python3 " + str(Path(__file__).resolve().parent / "build-doc-deps-index.py"))
    print(json.dumps({"additional_context": "\n".join(lines)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
