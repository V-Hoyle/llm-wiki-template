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
| `BOOTSTRAP.md` | Full setup guide (phases 1–4) |
| `install.sh` | Copy to `~/.cursor/llm-wiki`, install hooks + rules |
| `hooks/` | Cursor sessionStart/sessionEnd scripts |
| `examples/hooks.json` | User-level Cursor hooks config |
| `examples/llm-wiki.mdc` | Cursor rule for in-session wiki CRUD |
| `scripts/` | Ingest worker, git backup, lint, adhoc ingest |
| `launchd/` | macOS plist templates for background jobs |
| `schema/topics.json` | Example topic → repo mapping |
| `wiki/` | Empty index + log stubs |

## Architecture

```
Cursor session
  sessionStart  → inject wiki articles
  sessionEnd    → queue substantive chats
        ↓
raw/ (sources)  →  wiki/ (compiled articles)
        ↑
  background ingest worker (optional)
```

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
