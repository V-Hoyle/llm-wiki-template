#!/usr/bin/env python3
"""Bootstrap markdown/docs from active git branches into raw/<topic>/branches/."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from backlog_sync import (  # noqa: E402
    DEFAULT_AUTHOR,
    DEFAULT_SINCE_DAYS,
    WIKI_ROOT,
    append_manifest,
    branch_last_author_date,
    branch_merge_status,
    branches_touched_by_author,
    diff_doc_files,
    is_secret_path,
    iter_repo_refs,
    parse_since,
    redact_secrets,
    run_git,
    sha256_text,
    slugify,
)


MANIFEST = WIKI_ROOT / "schema" / "branch-bootstrap-manifest.jsonl"
INDEX_PATH = WIKI_ROOT / "schema" / "branch-index.json"


def copy_branch_file(
    *,
    topic: str,
    repo_name: str,
    branch: str,
    base: str,
    repo_path: Path,
    rel_path: str,
    collected: str,
    dry_run: bool,
) -> dict | None:
    if is_secret_path(rel_path):
        return None

    content = run_git(repo_path, "show", f"{branch}:{rel_path}")
    if not content:
        return None

    branch_slug = slugify(f"{repo_name}__{branch}")
    file_slug = slugify(rel_path.replace("/", "-"))
    dest = WIKI_ROOT / "raw" / topic / "branches" / f"{branch_slug}__{file_slug}.md"

    last_commit = run_git(
        repo_path,
        "log",
        branch,
        "-1",
        "--format=%cI",
        "--",
        rel_path,
    )
    status = branch_merge_status(repo_path, branch, base)
    sha = sha256_text(content)

    record = {
        "repo": repo_name,
        "branch": branch,
        "base": base,
        "status": status,
        "topic": topic,
        "source_path": rel_path,
        "dest": str(dest),
        "sha256": sha,
        "last_commit": last_commit,
        "collected": collected,
    }

    if dry_run:
        print(f"DRY  {repo_name}:{branch} → {dest.name}")
        return record

    dest.parent.mkdir(parents=True, exist_ok=True)
    header = "\n".join(
        [
            "---",
            f"Source: {repo_path / rel_path}",
            f"Git branch: {branch}",
            f"Base branch: {base}",
            f"Branch status: {status}",
            f"Last commit (file): {last_commit or 'unknown'}",
            f"Collected: {collected}",
            "Bootstrap: branch-backlog-sync",
            "---",
            "",
        ]
    )
    dest.write_text(header + redact_secrets(content), encoding="utf-8")
    append_manifest(MANIFEST, record)
    print(f"COPIED {repo_name}:{branch} {rel_path}")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap docs from active git branches")
    parser.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    parser.add_argument("--author", default=DEFAULT_AUTHOR)
    parser.add_argument("--repos", default="", help="Comma-separated repo names")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-docs", type=int, default=1, help="Min doc files before summary-only fallback")
    args = parser.parse_args()

    since = parse_since(args.since_days)
    collected = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    repos = [r.strip() for r in args.repos.split(",") if r.strip()] or None

    index_branches: list[dict] = []
    copied = 0
    skipped = 0

    for ref in iter_repo_refs(repos):
        if not run_git(ref.path, "rev-parse", "--verify", ref.base):
            print(f"SKIP {ref.name}: base {ref.base} missing", file=sys.stderr)
            continue

        branches = branches_touched_by_author(ref.path, args.author, since, ref.base)
        print(f"=== {ref.name} ({len(branches)} branches) ===")

        for branch in branches:
            if not branch_last_author_date(ref.path, branch, args.author, since):
                continue

            doc_files = diff_doc_files(ref.path, branch, ref.base)
            status = branch_merge_status(ref.path, branch, ref.base)
            last_commit = run_git(ref.path, "log", branch, "-1", f"--author={args.author}", "--format=%cI")

            if len(doc_files) < args.min_docs:
                # No markdown diff — still capture a commit summary for discoverability
                oneline = run_git(
                    ref.path,
                    "log",
                    f"{ref.base}..{branch}",
                    f"--author={args.author}",
                    "--oneline",
                    "-25",
                )
                if not oneline:
                    skipped += 1
                    continue

                branch_slug = slugify(f"{ref.name}__{branch}")
                dest = WIKI_ROOT / "raw" / ref.topic / "branches" / f"{branch_slug}__branch-summary.md"
                summary_body = "\n".join(
                    [
                        f"# Branch summary: {ref.name}/{branch}",
                        "",
                        f"**Base:** `{ref.base}`  ",
                        f"**Status:** {status}  ",
                        f"**Last author commit:** {last_commit or 'unknown'}",
                        "",
                        "## Recent commits (author)",
                        "",
                        "```",
                        oneline,
                        "```",
                        "",
                    ]
                )
                sha = sha256_text(summary_body)

                if args.dry_run:
                    print(f"DRY  summary {ref.name}:{branch}")
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    header = "\n".join(
                        [
                            "---",
                            f"Source: git log {ref.name}:{branch}",
                            f"Git branch: {branch}",
                            f"Base branch: {ref.base}",
                            f"Branch status: {status}",
                            f"Collected: {collected}",
                            "Bootstrap: branch-backlog-sync (summary-only)",
                            "---",
                            "",
                        ]
                    )
                    dest.write_text(header + summary_body, encoding="utf-8")
                    append_manifest(
                        MANIFEST,
                        {
                            "repo": ref.name,
                            "branch": branch,
                            "base": ref.base,
                            "status": status,
                            "topic": ref.topic,
                            "source_path": "(branch-summary)",
                            "dest": str(dest),
                            "sha256": sha,
                            "last_commit": last_commit,
                            "collected": collected,
                        },
                    )
                    print(f"SUMMARY {ref.name}:{branch}")
                    copied += 1

                index_branches.append(
                    {
                        "repo": ref.name,
                        "branch": branch,
                        "topic": ref.topic,
                        "base": ref.base,
                        "status": status,
                        "doc_count": 0,
                        "summary_only": True,
                        "last_commit": last_commit or "",
                        "files": ["(branch-summary)"],
                    }
                )
                continue

            branch_records = []
            for rel in doc_files:
                rec = copy_branch_file(
                    topic=ref.topic,
                    repo_name=ref.name,
                    branch=branch,
                    base=ref.base,
                    repo_path=ref.path,
                    rel_path=rel,
                    collected=collected,
                    dry_run=args.dry_run,
                )
                if rec:
                    branch_records.append(rec)
                    copied += 1

            if branch_records:
                index_branches.append(
                    {
                        "repo": ref.name,
                        "branch": branch,
                        "topic": ref.topic,
                        "base": ref.base,
                        "status": branch_records[0]["status"],
                        "doc_count": len(branch_records),
                        "last_commit": max(
                            (r.get("last_commit") or "" for r in branch_records),
                            default="",
                        ),
                        "files": [r["source_path"] for r in branch_records],
                    }
                )

    if not args.dry_run:
        INDEX_PATH.write_text(
            json.dumps(
                {
                    "updated": collected,
                    "since_days": args.since_days,
                    "author": args.author,
                    "branches": index_branches,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    print(f"=== branch bootstrap: {copied} files, {skipped} branches skipped (no docs) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
