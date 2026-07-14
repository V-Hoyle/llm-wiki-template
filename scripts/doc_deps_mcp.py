#!/usr/bin/env python3
"""Minimal stdio MCP server exposing the wiki doc-dependency tools.

Zero third-party deps (stdlib only), so it runs under system python3 without a
venv or node. Speaks JSON-RPC 2.0 over newline-delimited stdio (MCP stdio
transport). Reuses scripts/doc_deps.py as the single source of truth.

Tools:
  - which_docs_depend_on(files: [repo-qualified path], status?) -> docs that depend on them
  - docs_for_doc(doc) -> a doc's declared **Depends:** entries
  - rebuild_index(tld?) -> rebuild schema/doc-deps-index.json, return summary + warnings
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_deps import (  # noqa: E402
    INDEX_PATH,
    build_index,
    docs_for_changes,
    iter_docs,
    parse_doc,
    split_entry,
    WIKI_ROOT,
)

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "wiki-doc-deps", "version": "1.0.0"}

TOOLS = [
    {
        "name": "which_docs_depend_on",
        "description": "Given repo-qualified changed files (e.g. 'infrastructure/datasembly_iac/modules/iam/rbac.py'), return the wiki docs that declare a dependency on them.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "Repo-qualified paths, '<repo>/<relative-path>'."},
                "status": {"type": "string", "description": "Optional change status (M/A/D/R...). Default M."},
            },
            "required": ["files"],
        },
    },
    {
        "name": "docs_for_doc",
        "description": "Return the declared **Depends:** entries for a wiki doc (path relative to the wiki root, e.g. 'wiki/infra/pulumi.md', or an absolute path).",
        "inputSchema": {
            "type": "object",
            "properties": {"doc": {"type": "string"}},
            "required": ["doc"],
        },
    },
    {
        "name": "rebuild_index",
        "description": "Rebuild schema/doc-deps-index.json from all wiki docs and return a summary plus validation warnings.",
        "inputSchema": {
            "type": "object",
            "properties": {"tld": {"type": "string", "description": "Top-level dir of tracked repos (default ~/src)."}},
        },
    },
]


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
    out = []
    for h in hits:
        entries = sorted({m.entry for m in h.matches})
        out.append(f"- {h.title} ({h.doc}, Updated: {h.updated or 'n/a'}) via {', '.join(entries)}")
    return "\n".join(out)


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


def _tool_rebuild(args: dict) -> str:
    tld = args.get("tld")
    tld_path = Path(tld).expanduser() if tld else (Path.home() / "src")
    index = build_index(tld=tld_path if tld_path.exists() else None)
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n_docs = len(index["forward"])
    n_deps = sum(len(v["depends"]) for v in index["forward"].values())
    warns = index["warnings"]
    msg = f"Rebuilt {INDEX_PATH.name}: {n_docs} docs, {n_deps} dependency entries."
    if warns:
        msg += "\nWarnings:\n" + "\n".join(f"  - {w}" for w in warns)
    return msg


DISPATCH = {
    "which_docs_depend_on": _tool_which,
    "docs_for_doc": _tool_docs_for_doc,
    "rebuild_index": _tool_rebuild,
}


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
        return  # notification, no reply
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
