#!/usr/bin/env python3
"""Export Cursor agent transcripts to raw/decisions/transcripts/."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from backlog_sync import (  # noqa: E402
    DEFAULT_SINCE_DAYS,
    KEYWORDS,
    TRANSCRIPTS_ROOT,
    WIKI_ROOT,
    append_manifest,
    load_manifest_ids,
    parse_since,
    redact_secrets,
    sha256_text,
    slugify,
)

MANIFEST = WIKI_ROOT / "schema" / "transcript-export-manifest.jsonl"
INDEX_PATH = WIKI_ROOT / "schema" / "transcript-index.json"
OUT_ROOT = WIKI_ROOT / "raw" / "decisions" / "transcripts"

USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)


def workspace_from_path(path: Path) -> str:
    parts = path.parts
    try:
        idx = parts.index("projects")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    except ValueError:
        pass
    return "unknown"


def workspace_slug(name: str) -> str:
    return slugify(name.replace("Users-rylanhess-", ""))


def extract_text(content: list | dict | str) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text" and "text" in content:
            return str(content["text"])
        return ""
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text", "")))
        return "\n".join(chunks)
    return ""


def parse_transcript(jsonl_path: Path) -> dict:
    user_messages: list[str] = []
    assistant_messages: list[str] = []
    tool_count = 0

    with jsonl_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            role = row.get("role", "")
            message = row.get("message", {})
            content = message.get("content", [])

            if role == "user":
                text = extract_text(content)
                match = USER_QUERY_RE.search(text)
                user_messages.append(match.group(1).strip() if match else text.strip())
            elif role == "assistant":
                text = extract_text(content)
                if text:
                    assistant_messages.append(text[:4000])
                for item in content if isinstance(content, list) else []:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        tool_count += 1

    return {
        "user_messages": user_messages,
        "assistant_messages": assistant_messages,
        "tool_count": tool_count,
        "first_user": user_messages[0] if user_messages else "",
    }


def qualifies(
    *,
    parsed: dict,
    raw_text: str,
    size: int,
    min_bytes: int,
    workspace_filter: str,
    workspace: str,
) -> bool:
    if size < min_bytes:
        return False
    if workspace_filter and workspace_filter.lower() not in workspace.lower():
        return False
    if KEYWORDS.search(raw_text):
        return True
    if size >= 4000:
        return True
    if len(parsed["user_messages"]) >= 3:
        return True
    if parsed["tool_count"] >= 5:
        return True
    return False


def render_markdown(
    *,
    conv_id: str,
    workspace: str,
    source_path: Path,
    mtime: datetime,
    parsed: dict,
) -> str:
    lines = [
        "---",
        f"Source: {source_path}",
        f"Conversation ID: {conv_id}",
        f"Workspace: {workspace}",
        f"Exported: {mtime.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "Bootstrap: transcript-backlog-sync",
        "---",
        "",
        f"# Cursor session {conv_id[:8]}",
        "",
        f"**Workspace:** `{workspace}`  ",
        f"**First prompt:** {parsed['first_user'][:200] or '(empty)'}",
        "",
    ]

    for i, msg in enumerate(parsed["user_messages"][:20], 1):
        lines.extend([f"## User ({i})", "", redact_secrets(msg), ""])

    for i, msg in enumerate(parsed["assistant_messages"][:15], 1):
        trimmed = msg[:6000] + ("…" if len(msg) > 6000 else "")
        lines.extend([f"## Assistant ({i})", "", redact_secrets(trimmed), ""])

    if parsed["tool_count"]:
        lines.extend([f"*({parsed['tool_count']} tool calls omitted from export)*", ""])

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Cursor transcripts to raw/")
    parser.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    parser.add_argument("--workspace", default="Datasembly", help="Filter workspace path substring")
    parser.add_argument("--min-bytes", type=int, default=2000)
    parser.add_argument("--all-workspaces", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    since = parse_since(args.since_days)
    collected = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    known_ids = load_manifest_ids(MANIFEST, "conversation_id")

    exported = 0
    skipped = 0
    index_entries: list[dict] = []

    files = sorted(TRANSCRIPTS_ROOT.glob("**/agent-transcripts/**/*.jsonl"))
    for jsonl_path in files:
        if jsonl_path.name != jsonl_path.parent.name + ".jsonl":
            continue

        conv_id = jsonl_path.stem
        if conv_id in known_ids:
            skipped += 1
            continue

        mtime = datetime.fromtimestamp(jsonl_path.stat().st_mtime, tz=timezone.utc)
        if mtime < since:
            continue

        raw_text = jsonl_path.read_text(encoding="utf-8", errors="replace")
        size = len(raw_text.encode("utf-8"))
        workspace = workspace_from_path(jsonl_path)
        ws_filter = "" if args.all_workspaces else args.workspace

        parsed = parse_transcript(jsonl_path)
        if not qualifies(
            parsed=parsed,
            raw_text=raw_text,
            size=size,
            min_bytes=args.min_bytes,
            workspace_filter=ws_filter,
            workspace=workspace,
        ):
            skipped += 1
            continue

        ws_slug = workspace_slug(workspace)
        date_prefix = mtime.strftime("%Y-%m-%d")
        dest = OUT_ROOT / ws_slug / f"{date_prefix}-{conv_id[:8]}.md"
        body = render_markdown(
            conv_id=conv_id,
            workspace=workspace,
            source_path=jsonl_path,
            mtime=mtime,
            parsed=parsed,
        )
        sha = sha256_text(body)

        record = {
            "conversation_id": conv_id,
            "workspace": workspace,
            "topic_hint": ws_slug,
            "dest": str(dest),
            "sha256": sha,
            "size_bytes": size,
            "first_user": parsed["first_user"][:200],
            "exported": collected,
            "mtime": mtime.isoformat(),
        }

        if args.dry_run:
            print(f"DRY  {conv_id[:8]} {workspace} ({size} bytes)")
            exported += 1
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
            append_manifest(MANIFEST, record)
            print(f"EXPORTED {conv_id[:8]} → {dest.relative_to(WIKI_ROOT)}")
            exported += 1

        index_entries.append(record)
        if args.limit and exported >= args.limit:
            break

    if not args.dry_run:
        existing: list[dict] = []
        if INDEX_PATH.exists():
            try:
                existing = json.loads(INDEX_PATH.read_text(encoding="utf-8")).get("transcripts", [])
            except json.JSONDecodeError:
                existing = []
        by_id = {e["conversation_id"]: e for e in existing}
        for entry in index_entries:
            by_id[entry["conversation_id"]] = entry
        INDEX_PATH.write_text(
            json.dumps(
                {
                    "updated": collected,
                    "since_days": args.since_days,
                    "transcripts": sorted(
                        by_id.values(),
                        key=lambda x: x.get("mtime", ""),
                        reverse=True,
                    ),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    print(f"=== transcript export: {exported} exported, {skipped} skipped ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
