#!/usr/bin/env python3
"""Report which wiki docs depend on a set of changed files.

Used by the git hooks (repo side) and the Cursor hook. Fails open (prints
nothing, exits 0) if the wiki is absent so it never blocks a commit.

Usage:
  # explicit changes
  doc-deps-query.py --repo infrastructure --change datasembly_iac/modules/iam/rbac.py:M ...

  # pipe `git diff --name-status` in
  git diff --name-status HEAD~1 HEAD | doc-deps-query.py --repo infrastructure --name-status

Options:
  --format {text,json}   Output style (default text).
  --title STR            Optional header line for the reminder block.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from doc_deps import docs_for_changes, parse_name_status
except Exception:
    # Wiki not present / import failed -> fail open.
    sys.exit(0)

_STALE = {"D", "R-from", "C-from"}


def _status_word(status: str) -> str:
    return {
        "M": "Modified", "A": "Added", "D": "Deleted",
        "R-from": "Renamed (from)", "R-to": "Renamed (to)",
        "C-from": "Copied (from)", "C-to": "Copied (to)",
    }.get(status, status)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--change", action="append", default=[], help="path:status")
    ap.add_argument("--name-status", action="store_true", help="read `git diff --name-status` from stdin")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--title", default="")
    args = ap.parse_args()

    changes: list[tuple[str, str, str]] = []
    for c in args.change:
        path, _, status = c.rpartition(":")
        if not path:
            path, status = c, "M"
        changes.append((args.repo, path, status or "M"))
    if args.name_status and not sys.stdin.isatty():
        changes.extend(parse_name_status(sys.stdin.read(), args.repo))

    if not changes:
        return 0

    hits = docs_for_changes(changes)
    if not hits:
        return 0

    if args.format == "json":
        print(json.dumps([
            {"doc": h.doc, "title": h.title, "updated": h.updated,
             "matches": [{"file": m.file, "status": m.status, "entry": m.entry} for m in h.matches]}
            for h in hits
        ], indent=2))
        return 0

    lines: list[str] = []
    header = args.title or "LLM wiki: docs may need updating (tracked source files changed)"
    lines.append(f"[wiki-deps] {header}")
    stale_entries = set()
    for h in hits:
        lines.append(f"  - {h.title}  ({h.doc}, Updated: {h.updated or 'n/a'})")
        for m in sorted(set((m.file, m.status, m.entry) for m in h.matches)):
            fpath, status, entry = m
            note = "  <- DEP ENTRY NOW STALE, fix the **Depends:** line" if status in _STALE else ""
            lines.append(f"      via {entry}  [{_status_word(status)}: {args.repo}/{fpath}]{note}")
    lines.append("  Update the affected docs, then run: wiki-deps index  (or build-doc-deps-index.py)")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
