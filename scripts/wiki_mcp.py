#!/usr/bin/env python3
"""Unified stdio MCP server for the local LLM wiki.

Supersedes the old ``doc_deps_mcp.py`` (dependency-only) server. Speaks
JSON-RPC 2.0 over newline-delimited stdio (MCP stdio transport) and exposes
three families of tools:

  Search / RAG  -- search_wiki, get_context_pack, reindex
  Navigation    -- get_article, list_articles, outline, related_docs,
                   recent_changes
  Dependencies  -- which_docs_depend_on, docs_for_doc, docs_for_files,
                   rebuild_dep_index

Dependency and lexical tooling reuse only stdlib + the persisted index, so they
keep working when the Ollama daemon is down; the semantic side degrades
gracefully with a clear message. All retrieval logic lives in ``wiki_search``
and all dependency logic in ``doc_deps`` -- this file is only transport + glue.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_deps import (  # noqa: E402
    INDEX_PATH,
    WIKI_ROOT,
    WIKI_DIR,
    build_index,
    docs_for_changes,
    iter_docs,
    parse_doc,
    split_entry,
)

# wiki_search only imports Qdrant/Ollama lazily (at call time), so importing it
# here is cheap and safe -- dependency + lexical tools keep working even when
# the Ollama daemon or vector store is unavailable.
from wiki_search import WikiIndex, OllamaError, body  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "wiki", "version": "2.0.0"}
SRC_TLD = Path(os.environ.get("SRC_TLD", str(Path.home() / "src"))).expanduser()
# Auto-reconcile the embedding index when wiki content changed (cheap unless stale).
AUTO_REINDEX = os.environ.get("WIKI_AUTO_REINDEX", "1") not in ("0", "false", "False", "")


def _fresh_index() -> WikiIndex:
    idx = WikiIndex()
    if AUTO_REINDEX:
        try:
            idx.ensure_fresh()
        except OllamaError:
            pass  # serve the existing index; search handlers report Ollama issues
    return idx


# --------------------------------------------------------------------------- #
# Search / RAG tools
# --------------------------------------------------------------------------- #
def _tool_search_wiki(args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "Provide a 'query'."
    k = int(args.get("k") or 8)
    mode = (args.get("mode") or "hybrid").lower()
    topic = args.get("topic") or None
    try:
        hits = _fresh_index().search(query, k=k, mode=mode, topic=topic)
    except OllamaError as e:
        return f"Semantic search unavailable. Retry with mode='lexical'.\n\n{e}"
    if not hits:
        return f"No wiki matches for: {query}"
    blocks = []
    for i, h in enumerate(hits, 1):
        c = h.chunk
        header = f"{i}. {c.title} \u2014 {c.heading or 'overview'} ({c.doc})  score={h.score:.4f}"
        blocks.append(f"{header}\n{body(c.text)}")
    return "\n\n".join(blocks)


def _tool_get_context_pack(args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "Provide a 'query'."
    budget = int(args.get("budget") or 8000)
    k = int(args.get("k") or 12)
    try:
        return _fresh_index().context_pack(query, budget_chars=budget, k=k)
    except OllamaError as e:
        return f"Semantic context unavailable.\n\n{e}"


def _tool_reindex(args: dict) -> str:
    incremental = args.get("full") is not True
    try:
        summary = WikiIndex().reindex(incremental=incremental)
    except OllamaError as e:
        return f"Reindex failed.\n\n{e}"
    return (f"Reindexed: {summary['embedded']} chunk(s) embedded, "
            f"{summary['removed']} removed, {summary['total_chunks']} total "
            f"({summary['vector_count']} vectors, {summary['docs']} docs).")


# --------------------------------------------------------------------------- #
# Navigation tools
# --------------------------------------------------------------------------- #
def _resolve_doc(ref: str):
    """Resolve a doc by rel path (wiki/topic/x.md), absolute path, or title."""
    p = Path(ref)
    if p.is_absolute() and p.exists():
        return p
    cand = WIKI_ROOT / ref
    if cand.exists():
        return cand
    ref_low = ref.strip().lower()
    for d in iter_docs():
        if d.title.lower() == ref_low or d.rel.lower() == ref_low:
            return d.path
    return None


def _tool_get_article(args: dict) -> str:
    ref = (args.get("doc") or args.get("title") or "").strip()
    if not ref:
        return "Provide 'doc' (path) or 'title'."
    path = _resolve_doc(ref)
    if not path:
        return f"Article not found: {ref}"
    return path.read_text(encoding="utf-8", errors="replace")


def _tool_list_articles(args: dict) -> str:
    topic = (args.get("topic") or "").strip().lower() or None
    rows = []
    for d in iter_docs():
        d_topic = Path(d.rel).parts[1] if len(Path(d.rel).parts) >= 3 else ""
        if topic and d_topic != topic:
            continue
        rows.append(f"- [{d_topic}] {d.title} ({d.rel})  Updated: {d.updated or 'n/a'}")
    if not rows:
        return f"No articles{' for topic ' + topic if topic else ''}."
    return "\n".join(sorted(rows))


def _tool_outline(args: dict) -> str:
    by_topic: dict[str, list[str]] = {}
    for d in iter_docs():
        parts = Path(d.rel).parts
        t = parts[1] if len(parts) >= 3 else "(root)"
        by_topic.setdefault(t, []).append(f"  - {d.title} ({d.rel})")
    out = []
    for t in sorted(by_topic):
        out.append(f"## {t}")
        out.extend(sorted(by_topic[t]))
    return "\n".join(out) if out else "Wiki is empty."


def _tool_related_docs(args: dict) -> str:
    ref = (args.get("doc") or "").strip()
    if not ref:
        return "Provide 'doc'."
    path = _resolve_doc(ref)
    if not path:
        return f"Article not found: {ref}"
    rel = os.path.relpath(path, WIKI_ROOT)
    related = WikiIndex()._related_docs_for([rel])
    if not related:
        return f"No related docs found for {rel}."
    return f"Related to {rel}:\n" + "\n".join(f"  - {r}" for r in related)


def _tool_recent_changes(args: dict) -> str:
    n = int(args.get("n") or 15)
    log = WIKI_DIR / "log.md"
    if not log.exists():
        return "No wiki/log.md found."
    entries = [ln for ln in log.read_text(encoding="utf-8", errors="replace").splitlines()
               if ln.startswith("## ")]
    if not entries:
        return "No log entries."
    return "\n".join(entries[-n:])


# --------------------------------------------------------------------------- #
# Dependency tools (ported from doc_deps_mcp.py)
# --------------------------------------------------------------------------- #
def _tool_which(args: dict) -> str:
    files = args.get("files") or []
    status = args.get("status") or "M"
    changes = []
    for f in files:
        repo, rel = split_entry(f)
        if repo and rel:
            changes.append((repo, rel, status))
    if not changes:
        return "No valid repo-qualified files provided (expected '<repo>/<path>')."
    hits = docs_for_changes(changes)
    if not hits:
        return "No wiki docs depend on those files."
    return "\n".join(
        f"- {h.title} ({h.doc}, Updated: {h.updated or 'n/a'}) via "
        f"{', '.join(sorted({m.entry for m in h.matches}))}"
        for h in hits
    )


def _tool_docs_for_files(args: dict) -> str:
    """Like which_docs_depend_on but accepts absolute or repo-qualified paths."""
    files = args.get("files") or args.get("paths") or []
    status = args.get("status") or "M"
    changes: list[tuple[str, str, str]] = []
    for f in files:
        p = Path(f)
        repo = rel = ""
        if p.is_absolute():
            try:
                rp = p.expanduser().resolve().relative_to(SRC_TLD)
                if len(rp.parts) >= 2:
                    repo, rel = rp.parts[0], "/".join(rp.parts[1:])
            except Exception:
                pass
        if not repo:
            repo, rel = split_entry(str(f))
        if repo and rel:
            changes.append((repo, rel, status))
    if not changes:
        return "No usable paths (give absolute paths under ~/src or '<repo>/<path>')."
    hits = docs_for_changes(changes)
    if not hits:
        return "No wiki docs document those files."
    return "\n".join(
        f"- {h.title} ({h.doc}) \u2014 documents: {', '.join(sorted({m.file for m in h.matches}))}"
        for h in hits
    )


def _tool_docs_for_doc(args: dict) -> str:
    doc = args.get("doc", "")
    p = Path(doc)
    if not p.is_absolute():
        p = WIKI_ROOT / doc
    if not p.exists():
        return f"Doc not found: {doc}"
    d = parse_doc(p)
    deps = "\n".join(f"  - {e}" for e in d.depends) or "  (none)"
    return f"{d.title} ({d.rel})  Updated: {d.updated or 'n/a'}\nDepends:\n{deps}"


def _tool_rebuild_dep_index(args: dict) -> str:
    tld = args.get("tld")
    tld_path = Path(tld).expanduser() if tld else SRC_TLD
    index = build_index(tld=tld_path if tld_path.exists() else None)
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n_docs = len(index["forward"])
    n_deps = sum(len(v["depends"]) for v in index["forward"].values())
    msg = f"Rebuilt {INDEX_PATH.name}: {n_docs} docs, {n_deps} dependency entries."
    if index["warnings"]:
        msg += "\nWarnings:\n" + "\n".join(f"  - {w}" for w in index["warnings"])
    return msg


# --------------------------------------------------------------------------- #
# Tool registry
# --------------------------------------------------------------------------- #
_STR = {"type": "string"}
_STR_ARR = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    {
        "name": "search_wiki",
        "description": "Hybrid search over the wiki (dense qwen3 embeddings via Qdrant HNSW + BM25, RRF-fused). Returns ranked sections with snippets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": _STR,
                "k": {"type": "integer", "description": "Max results (default 8)."},
                "mode": {"type": "string", "enum": ["hybrid", "semantic", "lexical"], "description": "Default hybrid."},
                "topic": {"type": "string", "description": "Optional topic folder filter (e.g. infra/backend/frontend, per schema/topics.json)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_context_pack",
        "description": "Assemble a budgeted, ready-to-use context bundle for a task: hybrid search plus same-topic and dependency-graph related docs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": _STR,
                "budget": {"type": "integer", "description": "Max characters (default 8000)."},
                "k": {"type": "integer", "description": "Sections to consider (default 12)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "reindex",
        "description": "Rebuild the wiki embedding/BM25 index (incremental by content hash unless full=true). Run after editing wiki articles.",
        "inputSchema": {"type": "object", "properties": {"full": {"type": "boolean"}}},
    },
    {
        "name": "get_article",
        "description": "Return the full markdown of a wiki article by rel path (wiki/topic/x.md), absolute path, or exact title.",
        "inputSchema": {"type": "object", "properties": {"doc": _STR, "title": _STR}},
    },
    {
        "name": "list_articles",
        "description": "List wiki articles (optionally filtered by topic folder) with title, path, and last-updated date.",
        "inputSchema": {"type": "object", "properties": {"topic": _STR}},
    },
    {
        "name": "outline",
        "description": "Show the wiki table of contents grouped by topic.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "related_docs",
        "description": "Given a wiki article, list docs that share its topic or an overlapping **Depends:** entry.",
        "inputSchema": {"type": "object", "properties": {"doc": _STR}, "required": ["doc"]},
    },
    {
        "name": "recent_changes",
        "description": "Show the most recent wiki/log.md entries (default 15).",
        "inputSchema": {"type": "object", "properties": {"n": {"type": "integer"}}},
    },
    {
        "name": "which_docs_depend_on",
        "description": "Given repo-qualified changed files (e.g. 'my-repo/src/auth/rbac.py'), return wiki docs that declare a dependency on them.",
        "inputSchema": {
            "type": "object",
            "properties": {"files": _STR_ARR, "status": _STR},
            "required": ["files"],
        },
    },
    {
        "name": "docs_for_files",
        "description": "Which wiki docs document these files (read before editing). Accepts absolute paths under ~/src or repo-qualified '<repo>/<path>'.",
        "inputSchema": {
            "type": "object",
            "properties": {"files": _STR_ARR, "status": _STR},
            "required": ["files"],
        },
    },
    {
        "name": "docs_for_doc",
        "description": "Return the declared **Depends:** entries for a wiki doc (rel path like 'wiki/infra/pulumi.md' or absolute).",
        "inputSchema": {"type": "object", "properties": {"doc": _STR}, "required": ["doc"]},
    },
    {
        "name": "rebuild_dep_index",
        "description": "Rebuild schema/doc-deps-index.json (the dependency index, not embeddings) and return a summary plus validation warnings.",
        "inputSchema": {"type": "object", "properties": {"tld": _STR}},
    },
]

DISPATCH = {
    "search_wiki": _tool_search_wiki,
    "get_context_pack": _tool_get_context_pack,
    "reindex": _tool_reindex,
    "get_article": _tool_get_article,
    "list_articles": _tool_list_articles,
    "outline": _tool_outline,
    "related_docs": _tool_related_docs,
    "recent_changes": _tool_recent_changes,
    "which_docs_depend_on": _tool_which,
    "docs_for_files": _tool_docs_for_files,
    "docs_for_doc": _tool_docs_for_doc,
    "rebuild_dep_index": _tool_rebuild_dep_index,
}


# --------------------------------------------------------------------------- #
# JSON-RPC transport
# --------------------------------------------------------------------------- #
def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _result(req_id, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def handle(msg: dict) -> None:
    method = msg.get("method")
    req_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })
    elif method in ("notifications/initialized", "initialized"):
        return
    elif method == "ping":
        _result(req_id, {})
    elif method == "tools/list":
        _result(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = DISPATCH.get(name)
        if fn is None:
            _error(req_id, -32602, f"Unknown tool: {name}")
            return
        try:
            text = fn(args)
            _result(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:  # tool errors are reported in-band per MCP
            _result(req_id, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
    elif is_notification:
        return
    else:
        _error(req_id, -32601, f"Method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        try:
            handle(msg)
        except Exception as e:
            if isinstance(msg, dict) and msg.get("id") is not None:
                _error(msg.get("id"), -32603, f"Internal error: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
