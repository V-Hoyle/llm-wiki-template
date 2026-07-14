#!/usr/bin/env python3
"""(Re)build the wiki embedding + BM25 index.

Thin wrapper around ``wiki_search.WikiIndex.reindex`` so the build step is
discoverable alongside ``build-doc-deps-index.py``. All real logic lives in
``wiki_search.py`` (the single source of truth for retrieval).

Usage:
    build-wiki-index.py            # incremental: only re-embed changed chunks
    build-wiki-index.py --full     # rebuild everything, ignoring stored hashes
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_search import WikiIndex, OllamaError  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Build the wiki embedding/BM25 index.")
    ap.add_argument("--full", action="store_true", help="rebuild everything, ignore stored hashes")
    args = ap.parse_args(argv)

    try:
        summary = WikiIndex().reindex(incremental=not args.full, progress=True)
    except OllamaError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
