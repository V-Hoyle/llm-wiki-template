#!/usr/bin/env python3
"""Regenerate wiki/decisions/work-backlog-index.md from schema JSON indexes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

WIKI_ROOT = Path.home() / ".cursor" / "llm-wiki"
BRANCH_INDEX = WIKI_ROOT / "schema" / "branch-index.json"
TRANSCRIPT_INDEX = WIKI_ROOT / "schema" / "transcript-index.json"
PLAN_INDEX = WIKI_ROOT / "schema" / "plan-index.json"
CANVAS_INDEX = WIKI_ROOT / "schema" / "canvas-index.json"
OUT = WIKI_ROOT / "wiki" / "decisions" / "work-backlog-index.md"


def main() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "# Work Backlog Index — Branches & Cursor Chats",
        "",
        f"**Updated:** {today}  ",
        "**Auto-generated** by `scripts/generate-backlog-index.py` (do not hand-edit tables).",
        "",
        "Searchable inventory of **active git branches** (doc changes), **exported Cursor transcripts**, **plans**, and **canvases**.",
        "Use this when you forget what branch was for what, or to find a past chat topic.",
        "",
        "Raw sources:",
        "- Branches → `raw/<topic>/branches/`",
        "- Transcripts → `raw/decisions/transcripts/`",
        "- Plans → `raw/decisions/plans/`",
        "- Canvases → `raw/decisions/canvases/`",
        "- Machine indexes → `schema/branch-index.json`, `schema/transcript-index.json`, `schema/plan-index.json`, `schema/canvas-index.json`",
        "",
        "## Active branches (doc changes)",
        "",
        "| Repo | Branch | Status | Topic | Docs | Last commit |",
        "|------|--------|--------|-------|------|-------------|",
    ]

    if BRANCH_INDEX.exists():
        data = json.loads(BRANCH_INDEX.read_text(encoding="utf-8"))
        for b in sorted(data.get("branches", []), key=lambda x: x.get("last_commit", ""), reverse=True):
            lines.append(
                f"| {b['repo']} | `{b['branch']}` | {b['status']} | {b['topic']} | "
                f"{b['doc_count']} | {b.get('last_commit', '')[:10]} |"
            )
    else:
        lines.append("| — | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## Exported Cursor transcripts (recent)",
            "",
            "| Date | Workspace | First prompt | Conv ID |",
            "|------|-----------|--------------|---------|",
        ]
    )

    if TRANSCRIPT_INDEX.exists():
        data = json.loads(TRANSCRIPT_INDEX.read_text(encoding="utf-8"))
        for t in data.get("transcripts", [])[:80]:
            date = (t.get("mtime") or "")[:10]
            prompt = (t.get("first_user") or "").replace("|", "/")[:80]
            conv = t.get("conversation_id", "")[:8]
            ws = t.get("workspace", "").replace("Users-rylanhess-", "")[:40]
            lines.append(f"| {date} | `{ws}` | {prompt} | `{conv}` |")
    else:
        lines.append("| — | — | — | — |")

    lines.extend(
        [
            "",
            "## Cursor plans",
            "",
            "Full catalog: [cursor-plans-catalog.md](./cursor-plans-catalog.md)",
            "",
            "| Plan | Topic | Overview |",
            "|------|-------|----------|",
        ]
    )

    if PLAN_INDEX.exists():
        data = json.loads(PLAN_INDEX.read_text(encoding="utf-8"))
        for p in data.get("plans", [])[:40]:
            name = (p.get("name") or p.get("plan_id", "")).replace("|", "/")[:50]
            overview = (p.get("overview") or "").replace("|", "/")[:70]
            lines.append(f"| {name} | {p.get('topic', '')} | {overview} |")
    else:
        lines.append("| — | — | — |")

    lines.extend(
        [
            "",
            "## Cursor canvases",
            "",
            "Full catalog: [cursor-canvases-catalog.md](./cursor-canvases-catalog.md)",
            "",
            "| Canvas | Workspace | Topic |",
            "|--------|-----------|-------|",
        ]
    )

    if CANVAS_INDEX.exists():
        data = json.loads(CANVAS_INDEX.read_text(encoding="utf-8"))
        for c in data.get("canvases", []):
            lines.append(
                f"| {c.get('name', '')} | `{c.get('workspace', '')}` | {c.get('topic', '')} |"
            )
    else:
        lines.append("| — | — | — |")

    lines.extend(
        [
            "",
            "## How to refresh",
            "",
            "```bash",
            "~/.cursor/llm-wiki/scripts/run-backlog-sync.sh",
            "```",
            "",
            "Runs weekly via launchd (`com.rylanhess.llm-wiki-backlog-sync`).",
            "",
            "## Next step — compile themes",
            "",
            "Ask Cursor to compile theme articles from raw backlog (not per-branch/per-chat):",
            "",
            '> Read `raw/compass/branches/` and compile `wiki/compass/compass-chat-branches.md` with branch status and purpose.',
            "",
            "## See Also",
            "",
            "- [OPERATIONS.md](../../OPERATIONS.md)",
            "- [wiki-automation.md](./wiki-automation.md)",
            "- [workspace-repos.md](./workspace-repos.md)",
            "",
        ]
    )

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"WROTE {OUT}")


if __name__ == "__main__":
    main()
