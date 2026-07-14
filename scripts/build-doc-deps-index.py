#!/usr/bin/env python3
"""Build schema/doc-deps-index.json from every wiki article's **Depends:** line.

Usage:
  build-doc-deps-index.py [--tld ~/src] [--check]

  --tld DIR   Validate that non-glob dep targets exist under DIR/<repo>/... and
              warn on missing/renamed files.
  --check     Exit non-zero if any warnings are produced (for CI/lint).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_deps import INDEX_PATH, build_index  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tld", default=str(Path.home() / "src"), help="Top-level dir holding tracked repos")
    ap.add_argument("--check", action="store_true", help="Exit non-zero if warnings exist")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    tld = Path(args.tld).expanduser() if args.tld else None
    index = build_index(tld=tld if tld and tld.exists() else None)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    n_docs = len(index["forward"])
    n_deps = sum(len(v["depends"]) for v in index["forward"].values())
    if not args.quiet:
        print(f"Wrote {INDEX_PATH} — {n_docs} docs, {n_deps} dependency entries.")
        for w in index["warnings"]:
            print(f"  WARN: {w}", file=sys.stderr)
    if args.check and index["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
