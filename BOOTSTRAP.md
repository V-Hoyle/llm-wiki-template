# LLM Wiki — Bootstrap Guide

Shareable setup guide for a **personal, user-local** knowledge base in Cursor.  
Pattern: [Karpathy LLM wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) + [karpathy-llm-wiki skill](https://github.com/Astro-Han/karpathy-llm-wiki).

**Who this is for:** Anyone who wants Cursor to compound institutional memory across sessions — without putting personal notes in team repos.

**Time to value:** ~30 minutes for skeleton + hooks; **2–4 days** of seeding before it feels magical (repo docs + old chats + compiled articles).

---

## What you are building

```
Cursor session
  sessionStart  → inject relevant wiki articles (~8k chars)
  sessionEnd    → queue substantive sessions for background compile
        │
        ▼
raw/  (immutable sources)  →  wiki/  (compiled articles)
        ▲                           ▲
   bootstrap / adhoc ingest    agent + background worker
```

| Layer | Path | Role |
|-------|------|------|
| Index | `wiki/index.md` | Start here — catalog of all articles |
| Log | `wiki/log.md` | Append-only history of ingests and maintenance |
| Raw | `raw/<topic>/` | Immutable sources (repo snapshots, sessions, exports) |
| Wiki | `wiki/<topic>/` | Compiled knowledge (one level of subdirs only) |
| Queue | `queue/pending.jsonl` | Sessions waiting for background ingest |
| Secrets | `.env` | `CURSOR_API_KEY`, optional `WIKI_GIT_REMOTE` — **never commit** |

**Key principle:** Project repo rules beat personal wiki. Keep the wiki under `~/.cursor/`, not inside your employer's git repos.

---

## Prerequisites

- **Cursor** with [Hooks](https://cursor.com/docs) enabled (user-level `~/.cursor/hooks.json`)
- **macOS** (launchd examples below; Linux can use cron instead)
- **Node.js 18+** (background ingest worker)
- **Python 3** (hook helpers, bootstrap scripts)
- **Optional:** `jq` (hooks work without it via python3 fallback)
- **Cursor API key** — only if you want automated background ingest (Phase 4)

---

## Phase 1 — Skeleton (~30 min)

### 1. Install the Karpathy skill

```bash
npx add-skill Astro-Han/karpathy-llm-wiki
```

Skill path (typical): `~/.agents/skills/karpathy-llm-wiki/SKILL.md`

### 2. Create wiki directories

```bash
WIKI_ROOT="${HOME}/.cursor/llm-wiki"

mkdir -p "${WIKI_ROOT}"/{raw,wiki,queue/processed,schema,scripts,.state/logs}
```

### 3. Initialize index and log

Create `wiki/index.md`:

```markdown
# Knowledge Base Index

Personal work-brain wiki. Compiled from repo docs, sessions, and decisions.

## decisions

| Article | Summary | Updated |
|---------|---------|---------|
```

Create `wiki/log.md`:

```markdown
# Wiki Log

Append-only operation log for ingest, query archive, and lint runs.
```

### 4. Define your topics

Edit `schema/topics.json` for **your** repos and workspaces. Example:

```json
{
  "wikiRoot": "~/.cursor/llm-wiki",
  "skillPath": "~/.agents/skills/karpathy-llm-wiki/SKILL.md",
  "topics": {
    "my-app": {
      "label": "My main app",
      "sourceRepos": ["my-app"]
    },
    "infra": {
      "label": "Infrastructure",
      "sourceRepos": ["terraform", "k8s-config"]
    },
    "decisions": {
      "label": "Decisions & sessions",
      "sourceRepos": []
    }
  },
  "pathKeywords": {
    "my-app": ["my-app", "frontend"],
    "infra": ["terraform", "k8s-config"],
    "decisions": ["decisions"]
  }
}
```

`pathKeywords` drive hook routing: which wiki topic folder to inject based on open workspace paths.

### 5. Optional git backup

```bash
cd ~/.cursor/llm-wiki
git init -b main
cp .env.example .env   # if present in template repo
# Edit .env: WIKI_GIT_REMOTE=git@github.com:you/llm-wiki-private.git
```

Add a `.gitignore` that excludes `.env`, `.state/`, `node_modules/`, and any local caches.

---

## Phase 2 — Cursor hooks (~30 min)

Hooks are **short shell scripts** — they queue work, not full LLM compiles.

### 1. `~/.cursor/hooks.json`

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [
      { "command": "./hooks/wiki-session-start.sh", "timeout": 5 }
    ],
    "stop": [
      { "command": "./hooks/wiki-session-end.sh", "timeout": 3 }
    ],
    "sessionEnd": [
      { "command": "./hooks/wiki-session-end.sh", "timeout": 3 }
    ]
  }
}
```

### 2. Hook scripts

Copy from a template repo (or this project's maintainer):

| File | Purpose |
|------|---------|
| `~/.cursor/hooks/wiki-session-start.sh` | Match workspace → inject up to 5 articles (~8k chars) |
| `~/.cursor/hooks/wiki-session-end.sh` | Queue substantive sessions to `queue/pending.jsonl` |
| `~/.cursor/hooks/lib/wiki-common.sh` | Shared paths, topic routing, secret redaction |

**Customize** `wiki_topics_from_paths()` in `wiki-common.sh` for your `pathKeywords`.

### 3. sessionEnd — what gets queued?

A session is queued if **any** of:

- User message > 200 characters
- Session duration > 2 minutes
- Transcript file > 4 KB
- Transcript matches keywords: `decision`, `runbook`, `architecture`, `agents`, `wiki`, etc.

**Important:** Queuing happens when you **end/close** a chat thread — open tabs do not queue.

### 4. Optional user rule

Add `~/.cursor/rules/llm-wiki.mdc` telling the agent to JIT-read `wiki/index.md` and patch articles when durable facts change in-session. Hooks handle mechanical read/queue; the agent handles nuanced CRUD.

### 5. Verify hooks

1. Open Cursor Settings → Hooks — confirm user hooks load
2. Start a new chat in a repo matching a topic — you should see wiki context injected
3. End a substantive chat — check `queue/pending.jsonl` for a new line

---

## Phase 3 — Seed content (where the payoff comes from)

An empty wiki injects nothing useful. Plan to build **20–50 articles** before expecting one-shot answers.

### Source ideas (in priority order)

1. **Repo docs** — README, AGENTS.md, architecture docs, runbooks → copy to `raw/<topic>/`
2. **Old Cursor chats** — re-open substantive threads, let sessionEnd queue them, or export transcripts
3. **Plans and decisions** — Cursor Plan artifacts, design docs, postmortems
4. **External references** — blog posts, API docs, newsletters (adhoc ingest)

### Manual ingest (no background worker)

In Cursor chat:

> Ingest `~/path/to/doc.md` into my LLM wiki under **my-app**. Follow karpathy-llm-wiki: save to `raw/`, compile `wiki/`, update `index.md` and `log.md`.

Or use the adhoc script (if you have the template repo):

```bash
~/.cursor/llm-wiki/scripts/ingest-adhoc.sh --topic my-app ~/path/to/doc.md
```

### Bootstrap scripts (optional)

If you cloned a template with `scripts/bootstrap-tier*.sh`, run tiers to bulk-copy repo docs into `raw/` with provenance headers, then compile via ingest worker or manual Cursor sessions.

**Expect:** Several hours to a few days of seeding; cost ~$2–5 in API usage if using background ingest for large batches.

---

## Phase 4 — Background automation (optional)

For hands-off compile + git backup after each ingest.

### 1. Environment

```bash
cp ~/.cursor/llm-wiki/.env.example ~/.cursor/llm-wiki/.env
# Add CURSOR_API_KEY from Cursor dashboard
# Optional: WIKI_GIT_REMOTE=git@github.com:you/llm-wiki-private.git
```

### 2. Install worker dependencies

```bash
cd ~/.cursor/llm-wiki/scripts
npm install
```

### 3. Manual test

```bash
cd ~/.cursor/llm-wiki/scripts
source ../.env
npm run ingest:dry    # preview without API call
npm run ingest        # process one queued item
```

Batch processing (bypasses 4/hour rate limit):

```bash
npm run ingest -- --count 10 --no-rate-limit
```

After successful ingests, the worker auto-commits and pushes (if `WIKI_GIT_REMOTE` is set).

### 4. macOS launchd (recommended)

Install plists from template repo into `~/Library/LaunchAgents/`:

| Job | Schedule | Purpose |
|-----|----------|---------|
| `llm-wiki-ingest` | Every 15 min | Process queue (max 4/hour) |
| `llm-wiki-backup` | Daily 04:00 | Safety-net git commit + push |
| `llm-wiki-lint` | Sunday 03:00 | Index repair, dead links |
| `llm-wiki-backlog-sync` | Weekly | Export plans, transcripts, branches |

Load:

```bash
launchctl load ~/Library/LaunchAgents/com.you.llm-wiki-ingest.plist
```

**Note:** Jobs only run while your Mac is **awake and logged in**.

### 5. Rate limits

- Automated ingest: **4 items/hour** (prevents runaway API cost)
- Manual batch: `--no-rate-limit` when you explicitly want to drain a backlog

---

## Day-to-day usage

| You want… | Do this |
|-----------|---------|
| Search wiki | Ask Cursor: *"What does my wiki say about X?"* |
| Ingest a file | *"Ingest `~/file.md` into topic Y"* |
| Fix quality | *"Lint my LLM wiki"* or wait for weekly lint job |
| Check queue | `cat ~/.cursor/llm-wiki/queue/pending.jsonl` |
| Check worker logs | `tail ~/.cursor/llm-wiki/.state/logs/ingest-worker.log` |
| Force backup | `bash ~/.cursor/llm-wiki/scripts/run-git-backup.sh` |

Full ops reference: `OPERATIONS.md` (in template repos).

---

## Verification checklist

| Check | How |
|-------|-----|
| User-local only | `git status` clean in all employer repos |
| Hooks load | Cursor Settings → Hooks |
| Auto-read | New chat in a mapped repo → wiki context in prompt |
| Queue | End a substantive chat → line in `pending.jsonl` |
| Ingest | Worker run → new/updated `wiki/` article + `log.md` entry |
| No secrets | Grep `raw/` and `wiki/` for `API_KEY`, `password`, tokens |
| Git backup | `git log` on wiki repo; remote push succeeds |

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Wiki feels useless | Not enough seeded articles yet — keep bootstrapping |
| Chats never ingest | Sessions not ended, or filtered as non-substantive |
| Queue empty but chats happened | Open threads don't fire sessionEnd |
| Worker runs but does nothing | `queue empty` — normal when nothing is queued |
| Push fails | SSH/network to GitHub; retry `run-git-backup.sh` |
| Rate limit | Wait for next hour or use `--no-rate-limit` manually |

---

## What to customize for your environment

1. **`schema/topics.json`** — your repos and topic folders
2. **`wiki-common.sh`** — path keyword → topic mapping
3. **Substantive session keywords** — add domain terms in `wiki-session-end.sh`
4. **Bootstrap tiers** — which repo docs to copy first
5. **`.gitignore`** — never commit `.env` or API keys

---

## Further reading

| Doc | Content |
|-----|---------|
| [Karpathy gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | Original pattern |
| [karpathy-llm-wiki skill](https://github.com/Astro-Han/karpathy-llm-wiki) | Ingest / Query / Lint workflows |
| `OPERATIONS.md` | Maintainer ops guide (if cloned from template) |
| `wiki/decisions/wiki-automation.md` | Deep dive on hooks + workers |
| `wiki/decisions/local-llm-wiki-setup.md` | Full blueprint with design decisions |

---

## Getting a full template

Clone the sanitized template repo (no personal wiki content):

```bash
git clone git@github.com:rylanhess/llm-wiki-template.git ~/.cursor/llm-wiki
cd ~/.cursor/llm-wiki && ./install.sh
```

Includes hook scripts, worker scripts, launchd plists, example `topics.json`, and `install.sh`.

**Do not share:** `.env`, personal `raw/` content, or compiled `wiki/` articles unless intentional.

---

## Phase 5 — Doc dependency indexing (optional but recommended)

Every article can declare the source files it was compiled from via a `**Depends:**`
line, so git + Cursor hooks remind you (and agents) which docs need updating when a
tracked file changes. `install.sh` sets up the Cursor hook, rule, and skill; then:

```bash
wiki-deps install-hooks --tld ~/src   # git reminder hooks into tracked repos
wiki-deps index --tld ~/src           # build/validate the queryable index
```

To backfill an existing wiki (parallelize across agents) and for the full reference,
see [DOC-DEPENDENCIES.md](DOC-DEPENDENCIES.md).

---

## Phase 6 — Hybrid search & RAG (optional, local)

Make the wiki *searchable* — dense semantic search (local embeddings in a Qdrant
HNSW index) fused with BM25 lexical search, exposed as MCP tools and a CLI, plus a
semantic session warm-up. Everything runs on your machine; lexical search and the
dependency tools keep working even without the model.

```bash
# 1. Python venv for the search stack (install.sh does this automatically)
python3 -m venv ~/.cursor/llm-wiki/.venv
~/.cursor/llm-wiki/.venv/bin/pip install -r ~/.cursor/llm-wiki/requirements.txt

# 2. Local embedding model
#    Install Ollama (https://ollama.com/download), then:
ollama pull qwen3-embedding:8b

# 3. Build the index, then register the MCP server (see examples/mcp.json)
wiki-deps reindex
```

Query it via `wiki-deps search "..."` / `wiki-deps context "..."`, or the `wiki`
MCP server tools (`search_wiki`, `get_context_pack`, `get_article`, …). Full setup,
tool list, config, and upgrade notes: [SEARCH.md](SEARCH.md).

---

*Template repo: [github.com/rylanhess/llm-wiki-template](https://github.com/rylanhess/llm-wiki-template)*
