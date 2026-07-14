# Wiki Hybrid Search & RAG

The wiki is searchable with a local, private **hybrid** retrieval stack:

- **Dense** semantic search — articles are chunked by heading, embedded with a
  local Ollama model (`qwen3-embedding:8b`, 4096-dim, instruction-aware), and
  stored in a **Qdrant local-mode HNSW** index (on-disk, cosine; no server
  process).
- **Lexical** BM25 — a code-aware tokenizer (splits `snake_case`/`camelCase`,
  keeps path segments) so identifier queries land.
- **Fusion** — dense and lexical rankings are merged with Reciprocal Rank Fusion.

Everything runs on your machine. Lexical search and all dependency tools work
even when Ollama is down; only the semantic half needs the model.

## Prerequisites (semantic search)

1. **Install Ollama** — https://ollama.com/download  (macOS: `brew install ollama`)
2. **Start the daemon** — `ollama serve` (or open the Ollama app)
3. **Pull the model** — `ollama pull qwen3-embedding:8b`  (~4.7 GB, one-time)
4. **Python venv** — created by `./install.sh` at `~/.cursor/llm-wiki/.venv`
   (`qdrant-client`, `ollama`, `numpy`). Recreate manually with:
   ```bash
   python3 -m venv ~/.cursor/llm-wiki/.venv
   ~/.cursor/llm-wiki/.venv/bin/pip install -r ~/.cursor/llm-wiki/requirements.txt
   ```
5. **Build the index** — `wiki-deps reindex`

If a step is missing, the tools and scripts print exactly these instructions.

## Components

| Piece | Path | Role |
|-------|------|------|
| Search lib | `scripts/wiki_search.py` | Chunking, Ollama embedder, Qdrant HNSW store, BM25, RRF, context packs (single source of truth) |
| Index builder | `scripts/build-wiki-index.py` | Incremental embed + vector store build (`--full` to rebuild all) |
| MCP server | `scripts/wiki_mcp.py` | Unified `wiki` server (search + RAG + navigation + deps) |
| CLI | `scripts/wiki-deps` | `search` / `context` / `reindex` / `status` subcommands |
| Session hook | `hooks/wiki-session-start.sh` | Semantic warm-up: injects relevant sections at session start |
| Reindex hook | `hooks/wiki-reindex-post-commit` | Wiki-repo `post-commit`: keeps the index fresh after commits |
| Vectors | `schema/wiki-vectors/` | Qdrant local collection (HNSW) |
| Chunks | `schema/wiki-chunks.json` | Chunk metadata + per-chunk hashes (for incremental + BM25) |

## MCP tools

Search / RAG:

- `search_wiki(query, k=8, mode=hybrid|semantic|lexical, topic?)` — ranked sections with **full** section text.
- `get_context_pack(query, budget=8000, k=12)` — a ready-to-use bundle: hybrid hits plus same-topic and dependency-graph related docs.
- `reindex(full?)` — rebuild embeddings (incremental by content hash unless `full`).

Navigation:

- `get_article(doc|title)`, `list_articles(topic?)`, `outline()`, `related_docs(doc)`, `recent_changes(n?)`.

Dependencies (see [DOC-DEPENDENCIES.md](DOC-DEPENDENCIES.md)):

- `which_docs_depend_on(files[])`, `docs_for_files(files[])`, `docs_for_doc(doc)`, `rebuild_dep_index(tld?)`.

## CLI

```bash
wiki-deps search "auth token refresh flow" -k 5                 # hybrid (default)
wiki-deps search "retry backoff" --mode lexical                 # BM25 only (no Ollama)
wiki-deps search "rate limiting" --topic backend                # restrict to a topic
wiki-deps context "how does the deploy pipeline roll back"      # assemble a context pack
wiki-deps reindex                                               # incremental (only changed chunks)
wiki-deps reindex --full                                        # rebuild everything
wiki-deps status                                                # freshness report
```

## Freshness / reindexing

Change detection is keyed on a **per-chunk SHA-256** of each section's text —
finer-grained than a whole-file hash, so editing one section re-embeds only that
section. The index stays fresh three ways:

1. **`wiki-deps reindex`** — manual/explicit.
2. **Wiki-repo post-commit hook** — after any commit (e.g. the background ingest
   worker adding articles), an incremental reindex runs detached.
3. **Lazy check in the MCP server** — before a search, the server cheaply checks
   for stale chunks and reconciles them (set `WIKI_AUTO_REINDEX=0` to disable).

## Configuration (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint |
| `WIKI_EMBED_MODEL` | `qwen3-embedding:8b` | Embedding model tag |
| `WIKI_EMBED_DIM` | `4096` (native) | Set to opt into Matryoshka (MRL) truncation, 32–4096 |
| `WIKI_EMBED_BATCH` | `16` | Chunks per embed request |
| `WIKI_SNIPPET_CHARS` | `1000` | Section preview length in results |
| `WIKI_SESSION_SNIPPET_CHARS` | `1000` | Section preview length in the session warm-up |
| `WIKI_AUTO_REINDEX` | `1` | Server reconciles a stale index before searching |
| `WIKI_QDRANT_PATH` | `schema/wiki-vectors` | Qdrant local storage dir |

## Upgrading an existing wiki to hybrid search

If you already run the doc-dependency system and want to add search:

```bash
cd ~/.cursor/llm-wiki
git pull                              # or re-run ./install.sh from an updated clone

# 1. Search deps (venv)
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 2. Local embedding model
#    Install Ollama (https://ollama.com/download), then:
ollama pull qwen3-embedding:8b

# 3. Build the index (first build embeds every chunk; a few minutes)
./.venv/bin/python scripts/build-wiki-index.py     # or: wiki-deps reindex --full

# 4. Register the MCP server: add to ~/.cursor/mcp.json (install.sh does this)
#      "wiki": { "command": "<wiki>/.venv/bin/python", "args": ["<wiki>/scripts/wiki_mcp.py"] }
#    If you had the old "wiki-doc-deps" server, replace it — wiki_mcp.py supersedes it.

# 5. Semantic session warm-up: bump the sessionStart hook timeout to ~20s
#    (examples/hooks.json shows the value) and restart Cursor.
```

Notes:

- The old `doc_deps_mcp.py` is retired; its three dependency tools now live in
  the unified `wiki_mcp.py` (`rebuild_index` was renamed `rebuild_dep_index` to
  disambiguate from the embedding `reindex`).
- The embedding index (`schema/wiki-vectors/`, `schema/wiki-chunks.json`) is
  machine-local and regenerable — it does not need to be committed.
- No Ollama? Everything degrades gracefully: lexical search and all dependency
  tooling keep working; semantic tools return setup instructions.

## Troubleshooting

- **"Cannot reach the Ollama daemon"** — `ollama serve` (or open the app); check `OLLAMA_HOST`.
- **"Embedding model … is not installed"** — `ollama pull qwen3-embedding:8b`.
- **Search venv missing** — re-run `./install.sh` or recreate the venv (step 4 above).
- **Stale results** — `wiki-deps reindex` (or check `wiki-deps status`).
