#!/usr/bin/env python3
"""Export Cursor plans and canvas deliverables into raw/ and optionally queue ingest."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from backlog_sync import (  # noqa: E402
    WIKI_ROOT,
    append_manifest,
    load_manifest_ids,
    redact_secrets,
    sha256_text,
    slugify,
)

PLANS_ROOT = Path.home() / ".cursor" / "plans"
CANVAS_ROOT = Path.home() / ".cursor" / "projects"
PLAN_MANIFEST = WIKI_ROOT / "schema" / "plan-export-manifest.jsonl"
CANVAS_MANIFEST = WIKI_ROOT / "schema" / "canvas-export-manifest.jsonl"
PLAN_INDEX = WIKI_ROOT / "schema" / "plan-index.json"
CANVAS_INDEX = WIKI_ROOT / "schema" / "canvas-index.json"
QUEUE_FILE = WIKI_ROOT / "queue" / "pending.jsonl"

PLANS_OUT = WIKI_ROOT / "raw" / "decisions" / "plans"
CANVASES_OUT = WIKI_ROOT / "raw" / "decisions" / "canvases"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
JSX_TEXT_RE = re.compile(
    r"<(?:H1|H2|H3|Text|Code|Callout|Stat|CardHeader)[^>]*>(.*?)</(?:H1|H2|H3|Text|Code|Callout|Stat|CardHeader)>",
    re.DOTALL,
)
ATTR_RE = re.compile(r'\b(?:title|label|value)="([^"]+)"')


def parse_plan_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    block = match.group(1)
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"')
    return meta


def infer_topic(plan_id: str, text: str) -> str:
    blob = f"{plan_id} {text[:800]}".lower()
    if "compass-mcp" in blob or "compass_mcp" in blob or "mcp" in plan_id:
        return "compass-mcp"
    if any(k in blob for k in ("treasury", "datahub", "halfpipe", "snowflake", "exports", "semdex")):
        return "data-platform"
    if "airflow" in blob or "dag" in plan_id:
        return "orchestration"
    if any(k in blob for k in ("infrastructure", "pulumi", "helm", "ops")):
        return "infrastructure"
    if "compass" in blob:
        return "compass"
    return "decisions"


def strip_jsx_fragment(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = re.sub(r"\{`([^`]*)`\}", r"\1", text)
    text = re.sub(r"\{[^}]*\}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canvas_to_markdown(source: Path, tsx: str) -> str:
    title = source.stem.replace(".canvas", "")
    chunks: list[str] = []
    seen: set[str] = set()

    for match in JSX_TEXT_RE.finditer(tsx):
        cleaned = strip_jsx_fragment(match.group(1))
        if len(cleaned) < 3 or cleaned in seen:
            continue
        seen.add(cleaned)
        chunks.append(cleaned)

    for match in ATTR_RE.finditer(tsx):
        value = match.group(1).strip()
        if len(value) < 2 or value in seen:
            continue
        seen.add(value)
        chunks.append(value)

    body = "\n\n".join(f"- {c}" if not c.endswith(".") else c for c in chunks[:120])
    return "\n".join(
        [
            "---",
            f"Source: {source}",
            f"Canvas file: {source.name}",
            f"Exported: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "Bootstrap: cursor-deliverables-sync",
            "---",
            "",
            f"# Canvas: {title}",
            "",
            f"**Companion markdown** for `{source.name}`. Full interactive source copied to raw as `.tsx`.",
            "",
            "## Extracted content",
            "",
            body or "(no text extracted — open the .tsx source in raw/)",
            "",
        ]
    )


def render_plan_markdown(source: Path, body: str) -> str:
    return "\n".join(
        [
            "---",
            f"Source: {source}",
            f"Plan ID: {source.name}",
            f"Exported: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "Bootstrap: cursor-deliverables-sync",
            "---",
            "",
            body.lstrip(),
            "",
        ]
    )


def queue_ingest(
    *,
    conversation_id: str,
    source: str,
    topic: str,
    raw_path: Path,
    excerpt: str,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "conversation_id": conversation_id,
        "workspace": source,
        "topics": [topic],
        "transcript_path": "",
        "transcript_excerpt": redact_secrets(excerpt[:10000]),
        "ingest_kind": "adhoc",
        "raw_path": str(raw_path),
        "deliverable_kind": "plan"
        if "plans" in raw_path.parts
        else "canvas",
        "status": "pending",
    }
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_manifest_sha(path: Path, key: str) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                out[row[key]] = row.get("sha256", "")
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def export_plans(*, queue_new: bool, dry_run: bool) -> tuple[int, int, list[dict]]:
    known_sha = load_manifest_sha(PLAN_MANIFEST, "plan_id")
    exported = 0
    queued = 0
    index_entries: list[dict] = []
    collected = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for source in sorted(PLANS_ROOT.rglob("*.plan.md")):
        if source.name.startswith("."):
            continue
        plan_id = source.name
        raw_body = source.read_text(encoding="utf-8", errors="replace")
        sha = sha256_text(raw_body)
        if known_sha.get(plan_id) == sha:
            continue

        body = render_plan_markdown(source, raw_body)

        meta = parse_plan_frontmatter(raw_body)
        topic = infer_topic(plan_id, raw_body)
        dest = PLANS_OUT / plan_id.replace(".plan.md", ".md")

        record = {
            "plan_id": plan_id,
            "name": meta.get("name", plan_id),
            "overview": meta.get("overview", "")[:300],
            "topic": topic,
            "dest": str(dest),
            "sha256": sha,
            "exported": collected,
            "mtime": datetime.fromtimestamp(
                source.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }

        if dry_run:
            print(f"DRY PLAN {plan_id} → {dest.relative_to(WIKI_ROOT)}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
            append_manifest(PLAN_MANIFEST, record)
            print(f"EXPORTED plan {plan_id} → {dest.relative_to(WIKI_ROOT)}")
            if queue_new:
                queue_ingest(
                    conversation_id=f"plan-{slugify(plan_id, 40)}-{int(datetime.now().timestamp())}",
                    source=str(source),
                    topic=topic,
                    raw_path=dest,
                    excerpt=body,
                )
                queued += 1

        index_entries.append(record)
        exported += 1

    return exported, queued, index_entries


def export_canvases(*, queue_new: bool, dry_run: bool) -> tuple[int, int, list[dict]]:
    known_sha = load_manifest_sha(CANVAS_MANIFEST, "canvas_id")
    exported = 0
    queued = 0
    index_entries: list[dict] = []
    collected = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for source in sorted(CANVAS_ROOT.rglob("*.canvas.tsx")):
        canvas_id = str(source.relative_to(CANVAS_ROOT))
        tsx = source.read_text(encoding="utf-8", errors="replace")
        sha = sha256_text(tsx)
        if known_sha.get(canvas_id) == sha:
            continue

        md_body = canvas_to_markdown(source, tsx)

        slug = slugify(source.stem.replace(".canvas", ""), 60)
        tsx_dest = CANVASES_OUT / f"{slug}.canvas.tsx"
        md_dest = CANVASES_OUT / f"{slug}.md"
        topic = infer_topic(slug, tsx)

        record = {
            "canvas_id": canvas_id,
            "name": source.stem.replace(".canvas", ""),
            "workspace": source.parts[source.parts.index("projects") + 1]
            if "projects" in source.parts
            else "unknown",
            "topic": topic,
            "tsx_dest": str(tsx_dest),
            "md_dest": str(md_dest),
            "sha256": sha,
            "exported": collected,
            "mtime": datetime.fromtimestamp(
                source.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }

        if dry_run:
            print(f"DRY CANVAS {canvas_id} → {md_dest.relative_to(WIKI_ROOT)}")
        else:
            CANVASES_OUT.mkdir(parents=True, exist_ok=True)
            tsx_dest.write_text(tsx, encoding="utf-8")
            md_dest.write_text(md_body, encoding="utf-8")
            append_manifest(CANVAS_MANIFEST, record)
            print(f"EXPORTED canvas {canvas_id} → {md_dest.relative_to(WIKI_ROOT)}")
            if queue_new:
                queue_ingest(
                    conversation_id=f"canvas-{slug}-{int(datetime.now().timestamp())}",
                    source=str(source),
                    topic=topic,
                    raw_path=md_dest,
                    excerpt=md_body,
                )
                queued += 1

        index_entries.append(record)
        exported += 1

    return exported, queued, index_entries


def write_json_index(
    path: Path, id_key: str, array_key: str, entries: list[dict], existing: list[dict]
) -> None:
    by_id = {e[id_key]: e for e in existing}
    for entry in entries:
        by_id[entry[id_key]] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                array_key: sorted(
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


def regenerate_wiki_catalogs() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    all_plans: list[dict] = []
    if PLAN_INDEX.exists():
        try:
            all_plans = json.loads(PLAN_INDEX.read_text(encoding="utf-8")).get("plans", [])
        except json.JSONDecodeError:
            pass

    all_canvases: list[dict] = []
    if CANVAS_INDEX.exists():
        try:
            all_canvases = json.loads(CANVAS_INDEX.read_text(encoding="utf-8")).get(
                "canvases", []
            )
        except json.JSONDecodeError:
            pass

    plans_path = WIKI_ROOT / "wiki" / "decisions" / "cursor-plans-catalog.md"
    plans_lines = [
        "# Cursor Plans Catalog",
        "",
        f"**Updated:** {today}  ",
        "**Auto-generated** by `scripts/export-cursor-deliverables.py`.",
        "",
        "Searchable index of Cursor Plan mode artifacts exported from `~/.cursor/plans/`.",
        "Raw copies: `raw/decisions/plans/`. Machine index: `schema/plan-index.json`.",
        "",
        f"**Total plans:** {len(all_plans)}",
        "",
        "| Plan | Topic | Overview | Raw |",
        "|------|-------|----------|-----|",
    ]
    for p in sorted(all_plans, key=lambda x: x.get("mtime", ""), reverse=True):
        name = p.get("name", p.get("plan_id", ""))
        overview = (p.get("overview") or "").replace("|", "\\|")[:120]
        rel = Path(p.get("dest", "")).name
        plans_lines.append(
            f"| {name} | {p.get('topic', 'decisions')} | {overview} | [{rel}](../../raw/decisions/plans/{rel}) |"
        )
    plans_path.write_text("\n".join(plans_lines) + "\n", encoding="utf-8")

    canvas_path = WIKI_ROOT / "wiki" / "decisions" / "cursor-canvases-catalog.md"
    canvas_lines = [
        "# Cursor Canvases Catalog",
        "",
        f"**Updated:** {today}  ",
        "**Auto-generated** by `scripts/export-cursor-deliverables.py`.",
        "",
        "Cursor Canvas deliverables (`.canvas.tsx`) with markdown companions.",
        "Raw: `raw/decisions/canvases/` (`.tsx` + `.md`). Index: `schema/canvas-index.json`.",
        "",
        f"**Total canvases:** {len(all_canvases)}",
        "",
        "| Canvas | Workspace | Topic | Markdown | TSX source |",
        "|--------|-----------|-------|----------|------------|",
    ]
    for c in sorted(all_canvases, key=lambda x: x.get("mtime", ""), reverse=True):
        md_name = Path(c.get("md_dest", "")).name
        tsx_name = Path(c.get("tsx_dest", "")).name
        canvas_lines.append(
            f"| {c.get('name', '')} | `{c.get('workspace', '')}` | {c.get('topic', 'decisions')} | "
            f"[{md_name}](../../raw/decisions/canvases/{md_name}) | "
            f"[{tsx_name}](../../raw/decisions/canvases/{tsx_name}) |"
        )
    canvas_path.write_text("\n".join(canvas_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Cursor plans and canvases to raw/")
    parser.add_argument("--queue-new", action="store_true", help="Queue new/changed items for ingest worker")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--plans-only", action="store_true")
    parser.add_argument("--canvases-only", action="store_true")
    args = parser.parse_args()

    plan_exported = plan_queued = 0
    canvas_exported = canvas_queued = 0
    plan_entries: list[dict] = []
    canvas_entries: list[dict] = []

    if not args.canvases_only:
        plan_exported, plan_queued, plan_entries = export_plans(
            queue_new=args.queue_new, dry_run=args.dry_run
        )
    if not args.plans_only:
        canvas_exported, canvas_queued, canvas_entries = export_canvases(
            queue_new=args.queue_new, dry_run=args.dry_run
        )

    if not args.dry_run:
        existing_plans: list[dict] = []
        existing_canvases: list[dict] = []
        if PLAN_INDEX.exists():
            try:
                existing_plans = json.loads(PLAN_INDEX.read_text(encoding="utf-8")).get("plans", [])
            except json.JSONDecodeError:
                pass
        if CANVAS_INDEX.exists():
            try:
                existing_canvases = json.loads(
                    CANVAS_INDEX.read_text(encoding="utf-8")
                ).get("canvases", [])
            except json.JSONDecodeError:
                pass

        if plan_entries:
            write_json_index(PLAN_INDEX, "plan_id", "plans", plan_entries, existing_plans)
        if canvas_entries:
            write_json_index(
                CANVAS_INDEX, "canvas_id", "canvases", canvas_entries, existing_canvases
            )

        regenerate_wiki_catalogs()

    print(
        f"=== deliverables: plans {plan_exported} exported ({plan_queued} queued), "
        f"canvases {canvas_exported} exported ({canvas_queued} queued) ==="
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
