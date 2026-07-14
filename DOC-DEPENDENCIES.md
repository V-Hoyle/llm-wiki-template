# Doc Dependency Indexing

Every wiki article declares the source files it was compiled from, so tooling can
remind you (and coding agents) to update a doc when its sources change.

## The `**Depends:**` convention

Each article carries one `**Depends:**` line, immediately after `**Updated:**`:

```
**Sources:** ...human-readable sources...; 2026-07-14
**Updated:** 2026-07-14
**Depends:** infrastructure/__main__.py; infrastructure/datasembly_iac/modules/snowflake_iam/bots/**
```

- Semicolon-separated, **repo-qualified** entries: `<repo>/<path-or-glob>`.
- `<repo>` = the tracked repo's folder name under your source TLD (e.g. `~/src`); it matches an entry in `schema/topics.json` `sourceRepos`.
- Paths are relative to the repo root.
- Exact files for file-specific docs; a directory or `/**` glob for whole-module docs. `**` spans subdirectories, `*` stays within one segment.
- **No dependencies:** when a doc derives from no tracked-repo files (e.g. pages compiled from Cursor plans/sessions or synthesized answers), write exactly `**Depends:** (none)`. The tooling treats `(none)` / `none` (case-insensitive) as zero dependencies — the line is still required so every doc is accounted for, but it matches no file changes.

## Components

| Piece | Path | Role |
|-------|------|------|
| Shared lib | `scripts/doc_deps.py` | Parse `**Depends:**`, match changed files (single source of truth) |
| Index builder | `scripts/build-doc-deps-index.py` | Writes `schema/doc-deps-index.json`; `--tld` validates paths |
| Query | `scripts/doc-deps-query.py` | Reminder for a set of changes (used by hooks) |
| CLI | `scripts/wiki-deps` | `query` / `index` / `install-hooks` / `for-doc` / `docs` |
| MCP server | `scripts/doc_deps_mcp.py` | Tools: `which_docs_depend_on`, `docs_for_doc`, `rebuild_index` |
| Git hooks | `hooks/repo-hooks/` | post-commit/merge/checkout reminders in tracked repos |
| Cursor hook | `hooks/wiki-doc-deps.sh` + `scripts/cursor_doc_deps_hook.py` | `postToolUse` reminder when an agent edits a tracked file |
| Skill | `skills/wiki-doc-deps/SKILL.md` | Makes `**Depends:**` required for future docs |

## Install

```bash
# Cursor hook + rule + skill are installed by the wiki installer:
./install.sh
# Git reminder hooks into every tracked repo under a TLD:
wiki-deps install-hooks --tld ~/src        # or: scripts/install-repo-hooks.sh --tld ~/src
# (Re)build the queryable index:
wiki-deps index --tld ~/src
```

Git hooks are installed into each repo's local `.git/hooks/` only (never committed),
chain any pre-existing hook, and fail open (never block a git operation). Uninstall
with `wiki-deps install-hooks --tld ~/src --uninstall`.

## Usage

```bash
wiki-deps query --repo infrastructure --change datasembly_iac/modules/iam/rbac.py:M
git diff --name-status HEAD~1 HEAD | wiki-deps query --repo infrastructure --name-status
wiki-deps for-doc wiki/infra/pulumi.md
wiki-deps docs
```

## Migration / backfilling an existing wiki (parallelize with agents)

If you already have docs without `**Depends:**` lines, backfill them — ideally by
dispatching several agents in parallel, each owning a slice of the docs. Give each
agent this task:

> Backfill the `**Depends:**` line into these wiki docs: <list>. Add ONE line right
> after each doc's `**Updated:**` line: `**Depends:** <repo>/<path-or-glob>; ...`.
> Repo-qualify every path (`<repo>` = the folder name under `~/src`). Derive entries
> from the doc's existing `**Sources:**` line and any `path:line` cites. Use exact
> paths for file-specific docs and a `/**` glob for whole-module docs. Verify each
> concrete path exists under `~/src/<repo>/`; prefer a correct glob over a wrong path.
> If a doc derives from no tracked-repo file, write `**Depends:** (none)`. Change
> ONLY the `**Depends:**` line.

Split the docs by area (e.g. one agent per topic dir or per module group) so they run
concurrently. When they finish:

```bash
wiki-deps index --tld ~/src   # build + validate; fix any WARN (missing/renamed paths)
```

Tip: keep landscape/reference docs' dependencies high-signal (the specific files/dirs
they actually summarize) rather than an all-encompassing glob, to avoid noisy reminders.

## When a source moves or is deleted

Reminders mark the matching `**Depends:**` entry as STALE. Update the entry (new path)
or the article, then rebuild the index.
