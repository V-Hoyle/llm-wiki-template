---
name: wiki-doc-deps
description: >-
  Maintain file-dependency metadata for the local LLM wiki. Use whenever you
  create or edit a wiki article, ingest sources into the wiki, or need to find
  which docs depend on a given source file. Every wiki article MUST carry a
  **Depends:** line listing the repo-qualified source files it was compiled from.
---

# Wiki Doc Dependencies

Every article in `~/.cursor/llm-wiki/wiki/` declares the source files it was
compiled from, so tooling can remind us to update a doc when its sources change.
This is REQUIRED for every new or edited article — treat it as part of the
karpathy-llm-wiki Ingest workflow (add the line whenever you write Sources).

## The `**Depends:**` line

Place it immediately after the `**Updated:**` line:

```
**Sources:** ...human-readable sources...; 2026-07-14
**Updated:** 2026-07-14
**Depends:** infrastructure/__main__.py; infrastructure/datasembly_iac/modules/snowflake_iam/bots/**
```

Rules:
- Semicolon-separated, **repo-qualified** entries: `<repo>/<path-or-glob>`.
- `<repo>` is the tracked repo's folder name under `~/src` (it matches an entry
  in `schema/topics.json` `sourceRepos`).
- Paths are relative to that repo's root.
- Use **exact file paths** for docs about specific files; use a **directory** or
  `/**` **glob** for docs covering a whole module/tree. `**` spans
  subdirectories; `*` stays within one path segment.
- Derive entries from the doc's `**Sources:**` line and any `path:line` cites.
- If a doc derives from no tracked-repo file, write `**Depends:** (none)`.

## After editing docs

Rebuild the queryable index (also validates that paths resolve on disk):

```
python3 ~/.cursor/llm-wiki/scripts/build-doc-deps-index.py --tld ~/src
```

## Tooling (all share `scripts/doc_deps.py`)

- `wiki-deps query --repo <name> --change <path>:<status> ...` — which docs depend on changed files.
- `wiki-deps index [--tld ~/src] [--check]` — (re)build `schema/doc-deps-index.json`.
- `wiki-deps install-hooks --tld ~/src` — install the git reminder hooks into tracked repos.
- MCP server `wiki-doc-deps` — tools `which_docs_depend_on`, `docs_for_doc`, `rebuild_index`.
- Git hooks (post-commit/merge/checkout) in tracked repos + a Cursor `postToolUse`
  hook print reminders naming the docs that depend on changed/edited files.

## When a source file is deleted or moved

The reminder marks the matching `**Depends:**` entry as STALE. Update the entry
(new path) or the article (if the concept moved), then rebuild the index.

See `~/.cursor/llm-wiki/DOC-DEPENDENCIES.md` for the full system reference and the
parallel-subagent backfill/migration recipe.
