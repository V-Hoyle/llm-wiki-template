# LLM Wiki Template

Sanitized starter kit for a **personal, user-local** Cursor knowledge base using the [Karpathy LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern.

**No employer repo content** — just scaffolding, hooks, background workers, and docs.

## Quick start

```bash
# 1. Clone into your wiki root
git clone git@github.com:rylanhess/llm-wiki-template.git ~/.cursor/llm-wiki
cd ~/.cursor/llm-wiki

# 2. Run installer (hooks, rules, npm deps)
chmod +x install.sh
./install.sh

# 3. Optional: background workers (macOS launchd)
INSTALL_LAUNCHD=1 ./install.sh

# 4. Customize topics + seed content — see BOOTSTRAP.md
```

Install the Karpathy skill if the installer didn't:

```bash
npx add-skill Astro-Han/karpathy-llm-wiki
```

## What's included

| Path | Purpose |
|------|---------|
| `BOOTSTRAP.md` | Full setup guide (phases 1–6) |
| `install.sh` | Copy to `~/.cursor/llm-wiki`, install hooks + rules + search venv/index + MCP server |
| `hooks/` | Cursor sessionStart/sessionEnd scripts (+ doc-deps and wiki-repo reindex hooks) |
| `examples/hooks.json` | User-level Cursor hooks config |
| `examples/mcp.json` | Unified `wiki` MCP server (search + RAG + deps) config |
| `examples/llm-wiki.mdc` | Cursor rule for in-session wiki CRUD |
| `scripts/` | Ingest worker, git backup, lint, adhoc ingest, search + dependency tooling |
| `launchd/` | macOS plist templates for background jobs |
| `schema/topics.json` | Example topic → repo mapping |
| `wiki/` | Empty index + log stubs |
| `SEARCH.md` | Hybrid search + RAG: Ollama/Qdrant setup, MCP/CLI tools, upgrade guide |
| `requirements.txt` | Python deps for the optional search venv (`qdrant-client`, `ollama`, `numpy`) |
| `DOC-DEPENDENCIES.md` | Doc file-dependency system: `**Depends:**` convention, `wiki-deps` CLI, MCP server, git + Cursor reminder hooks |
| `hooks/repo-hooks/` | Git hooks (post-commit/merge/checkout) installed into tracked repos to remind you which docs depend on changed files |
| `skills/wiki-doc-deps/` | Skill making the `**Depends:**` line required on every article |

## Architecture

```
Cursor session
  sessionStart  → semantic warm-up: inject relevant wiki sections
  sessionEnd    → queue substantive chats
        ↓
raw/ (sources)  →  wiki/ (compiled articles)
        ↑                    │
  background ingest worker   └─ hybrid search index (Qdrant HNSW + BM25)
     (optional)                   ↑ Ollama qwen3-embedding:8b (local, optional)
```

**Hybrid search & RAG** (optional, local): articles are embedded with a local
Ollama model into a Qdrant HNSW index and fused with BM25. Query it via the
`wiki` MCP server (`search_wiki`, `get_context_pack`, …) or `wiki-deps search`.
Lexical search and dependency tools work without Ollama. See
[SEARCH.md](SEARCH.md).

## Customize

1. **`schema/topics.json`** — your repos and wiki topic folders
2. **`hooks/lib/wiki-common.sh`** — path keyword → topic routing
3. **`~/.cursor/llm-wiki/.env`** — `CURSOR_API_KEY`, optional `WIKI_GIT_REMOTE`
4. **Bootstrap scripts** — `scripts/bootstrap-tier*.sh` are optional examples; write tiers for your own repos or ingest manually via Cursor chat

## Background automation

| Job | Schedule | Script |
|-----|----------|--------|
| Ingest | Every 15 min | `run-ingest-worker.sh` |
| Git backup | Daily 04:00 | `run-git-backup.sh` |
| Lint | Sunday 03:00 | `run-lint-worker.sh` |
| Backlog sync | Saturday 02:00 | `run-backlog-sync.sh` |

Requires Mac awake + logged in. Rate limit: 4 ingests/hour (use `npm run ingest -- --count N --no-rate-limit` for manual batches).

## Related

- [Karpathy gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [karpathy-llm-wiki skill](https://github.com/Astro-Han/karpathy-llm-wiki)

## License

MIT — use freely; customize for your own work brain.
