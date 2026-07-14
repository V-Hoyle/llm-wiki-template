#!/usr/bin/env python3
"""Hybrid retrieval library for the local LLM wiki.

This is the single source of truth for *search* over the wiki, mirroring how
``doc_deps.py`` is the single source of truth for *dependencies*. The MCP
server, the ``wiki-deps`` CLI, and the sessionStart hook all import from here so
retrieval behaviour never diverges.

Design
------
- Chunking: each wiki article is split by markdown ``##`` heading. Every chunk is
  prefixed with the article title + topic so a section is self-describing.
- Dense: chunks are embedded with a local Ollama model (``qwen3-embedding:8b``,
  4096-dim, instruction-aware) reached over HTTP with stdlib ``urllib``. Vectors
  live in a Qdrant **local-mode** collection (on-disk, HNSW, cosine) -- a real
  ANN index, never brute-force cosine.
- Lexical: a hand-rolled Okapi BM25 with a code-aware tokenizer (splits
  ``snake_case``/``camelCase`` and keeps path segments) rebuilt in memory from
  the persisted chunk text.
- Hybrid: dense and lexical rankings are combined with Reciprocal Rank Fusion.
- Incremental: chunks are keyed by a SHA-256 of their text, so a reindex only
  re-embeds changed/new chunks and drops removed ones.

Requires the venv deps (``qdrant-client``, ``numpy``); Ollama is a runtime HTTP
dependency only, so lexical search and indexing metadata survive an outage.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_deps import WIKI_ROOT, TOPICS_JSON, Doc, iter_docs  # noqa: E402

# --------------------------------------------------------------------------- #
# Configuration (all env-overridable)
# --------------------------------------------------------------------------- #
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
EMBED_MODEL = os.environ.get("WIKI_EMBED_MODEL", "qwen3-embedding:8b")
# Native dim for qwen3-embedding:8b is 4096. Set WIKI_EMBED_DIM to opt into
# Matryoshka (MRL) truncation (32-4096); only then do we pass `dimensions` to
# Ollama, since older daemons may reject an unknown parameter.
EMBED_DIM_EXPLICIT = "WIKI_EMBED_DIM" in os.environ
EMBED_DIM = int(os.environ.get("WIKI_EMBED_DIM", "4096"))
EMBED_BATCH = int(os.environ.get("WIKI_EMBED_BATCH", "16"))
SNIPPET_CHARS = int(os.environ.get("WIKI_SNIPPET_CHARS", "1000"))

# Qwen3 is instruction-aware: queries get a task instruction, documents do not.
QUERY_INSTRUCTION = os.environ.get(
    "WIKI_QUERY_INSTRUCTION",
    "Given a question or task about our infrastructure, code, and engineering "
    "decisions, retrieve the wiki passages that best answer it",
)

QDRANT_PATH = Path(os.environ.get("WIKI_QDRANT_PATH", str(WIKI_ROOT / "schema" / "wiki-vectors")))
CHUNKS_PATH = Path(os.environ.get("WIKI_CHUNKS_PATH", str(WIKI_ROOT / "schema" / "wiki-chunks.json")))
COLLECTION = os.environ.get("WIKI_COLLECTION", "wiki")

# Stable namespace so chunk ids are deterministic across rebuilds.
_ID_NAMESPACE = uuid.UUID("6f6e2d77-696b-6920-7365-617263680001")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# Metadata lines we strip from chunk bodies (kept as structured fields instead).
_META_RE = re.compile(r"^\*\*(Depends|Updated|Sources|Raw):\*\*", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Chunk:
    id: str                 # deterministic uuid5(doc#idx)
    doc: str                # doc path relative to WIKI_ROOT, e.g. wiki/infra/pulumi.md
    title: str              # article title (the top-level # heading)
    topic: str              # folder under wiki/, e.g. "infra"
    heading: str            # section heading (or "" for the article preamble)
    text: str               # embed/search text (title + topic + heading + body)
    updated: str
    hash: str

    def payload(self) -> dict:
        return {
            "doc": self.doc,
            "title": self.title,
            "topic": self.topic,
            "heading": self.heading,
            "text": self.text,
            "updated": self.updated,
            "hash": self.hash,
        }

    @classmethod
    def from_payload(cls, cid: str, p: dict) -> "Chunk":
        return cls(
            id=cid,
            doc=p.get("doc", ""),
            title=p.get("title", ""),
            topic=p.get("topic", ""),
            heading=p.get("heading", ""),
            text=p.get("text", ""),
            updated=p.get("updated", ""),
            hash=p.get("hash", ""),
        )


@dataclass
class SearchHit:
    chunk: Chunk
    score: float
    dense_rank: int | None = None
    lexical_rank: int | None = None

    def to_dict(self) -> dict:
        return {
            "doc": self.chunk.doc,
            "title": self.chunk.title,
            "topic": self.chunk.topic,
            "heading": self.chunk.heading,
            "score": round(self.score, 6),
            "text": body(self.chunk.text),
        }


class OllamaError(RuntimeError):
    """Raised when Ollama (daemon, model, or client lib) is unavailable.

    The message always includes actionable setup guidance so both the user and
    the agent know exactly how to fix it.
    """


def install_guidance() -> str:
    """Human/agent-facing instructions for getting semantic search working."""
    return (
        "Local semantic search needs Ollama + the embedding model:\n"
        "  1. Install Ollama:           https://ollama.com/download  (macOS: `brew install ollama`)\n"
        "  2. Start the daemon:         `ollama serve`  (or open the Ollama app)\n"
        f"  3. Pull the embedding model: `ollama pull {EMBED_MODEL}`  (~4.7 GB)\n"
        "  4. (Re)build the index:      `wiki-deps reindex`\n"
        f"\nPython deps live in the wiki venv ({WIKI_ROOT}/.venv). Recreate with:\n"
        f"  `python3 -m venv {WIKI_ROOT}/.venv && {WIKI_ROOT}/.venv/bin/pip install "
        f"-r {WIKI_ROOT}/requirements.txt`\n"
        "\nLexical (BM25) search and all dependency tools work without Ollama."
    )


def _diagnose_ollama(err: Exception) -> str:
    msg = str(err).lower()
    if any(s in msg for s in ("not found", "no such model", "try pulling")):
        head = f"Embedding model '{EMBED_MODEL}' is not installed."
    elif any(s in msg for s in ("connection", "refused", "connect", "timed out", "max retries")):
        head = f"Cannot reach the Ollama daemon at {OLLAMA_HOST}."
    else:
        head = f"Ollama embedding failed: {err}"
    return f"{head}\n\n{install_guidance()}"


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
def _topic_of(doc_rel: str) -> str:
    parts = Path(doc_rel).parts
    # doc_rel looks like "wiki/<topic>/<article>.md"
    return parts[1] if len(parts) >= 3 and parts[0] == "wiki" else ""


def _chunk_id(doc_rel: str, idx: int) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"{doc_rel}#{idx}"))


def _clean_body(lines: list[str]) -> str:
    kept = [ln for ln in lines if not _META_RE.match(ln.strip())]
    return "\n".join(kept).strip()


def chunk_doc(doc: Doc) -> list[Chunk]:
    """Split one article into heading-scoped chunks with title/topic context."""
    text = doc.path.read_text(encoding="utf-8", errors="replace")
    topic = _topic_of(doc.rel)

    # Split into (heading, body-lines) sections. Content before the first ## is
    # the preamble (heading = "").
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) <= 3 and m.group(2) != doc.title:
            sections.append((m.group(2), []))
        else:
            sections[-1][1].append(line)

    chunks: list[Chunk] = []
    idx = 0
    for heading, body_lines in sections:
        body = _clean_body(body_lines)
        if not body:
            continue
        header = f"{doc.title} ({topic})"
        embed_text = f"{header}\n## {heading}\n{body}" if heading else f"{header}\n{body}"
        cid = _chunk_id(doc.rel, idx)
        chunks.append(
            Chunk(
                id=cid,
                doc=doc.rel,
                title=doc.title,
                topic=topic,
                heading=heading,
                text=embed_text,
                updated=doc.updated,
                hash=hashlib.sha256(embed_text.encode("utf-8")).hexdigest(),
            )
        )
        idx += 1
    return chunks


def all_chunks(docs: list[Doc] | None = None) -> list[Chunk]:
    doc_list = docs if docs is not None else list(iter_docs())
    out: list[Chunk] = []
    for d in doc_list:
        out.extend(chunk_doc(d))
    return out


def body(text: str) -> str:
    """Full section text with only the synthetic ``Title (topic)`` header removed.

    Used for anything returned to a model/agent -- we do NOT truncate retrieved
    content, since the whole point of retrieval is to hand over the real text.
    """
    return text.split("\n", 1)[1].strip() if "\n" in text else text.strip()


def snippet(text: str, limit: int | None = None) -> str:
    """A compact, single-line preview. Only for the auto-injected session warm-up
    (which must stay lean); explicit searches return full ``body()`` text."""
    limit = SNIPPET_CHARS if limit is None else limit
    flat = re.sub(r"\s+", " ", body(text)).strip()
    return flat[:limit] + ("..." if len(flat) > limit else "")


# --------------------------------------------------------------------------- #
# Lexical: code-aware BM25
# --------------------------------------------------------------------------- #
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def tokenize(text: str) -> list[str]:
    """Code-aware tokenizer.

    Keeps whole identifiers/path segments *and* their sub-tokens so a query for
    ``rbac`` matches ``sso_rbac.py`` and ``RbacMembers`` matches ``rbac``.
    """
    tokens: list[str] = []
    for raw in _WORD_RE.findall(text.lower()):
        tokens.append(raw)
        # Add unique sub-tokens once each (camelCase + snake_case parts) so we
        # boost recall without inflating term frequencies via duplicates.
        subs: set[str] = set(m for m in _CAMEL_RE.findall(raw))
        subs.update(p for p in raw.split("_") if p)
        subs.discard(raw)
        tokens.extend(subs)
    return tokens


class Bm25Index:
    """Minimal in-memory Okapi BM25 (k1/b tunable), built from chunk text."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.chunks = chunks
        self.doc_tokens: list[list[str]] = [tokenize(c.text) for c in chunks]
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.avg_len = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.tf: list[Counter] = [Counter(t) for t in self.doc_tokens]
        df: Counter = Counter()
        for toks in self.doc_tokens:
            df.update(set(toks))
        n = len(chunks)
        self.idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }

    def rank(self, query: str, limit: int = 30) -> list[tuple[str, float]]:
        q_terms = set(tokenize(query))
        scores: list[tuple[str, float]] = []
        for i, c in enumerate(self.chunks):
            if not self.doc_len[i]:
                continue
            s = 0.0
            tf_i = self.tf[i]
            denom_norm = self.k1 * (1 - self.b + self.b * self.doc_len[i] / (self.avg_len or 1))
            for term in q_terms:
                f = tf_i.get(term)
                if not f:
                    continue
                s += self.idf.get(term, 0.0) * (f * (self.k1 + 1)) / (f + denom_norm)
            if s > 0:
                scores.append((c.id, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:limit]


# --------------------------------------------------------------------------- #
# Dense: Ollama embeddings
# --------------------------------------------------------------------------- #
class OllamaEmbedder:
    """Thin wrapper over the official ``ollama`` client with setup guidance."""

    def __init__(self, host: str = OLLAMA_HOST, model: str = EMBED_MODEL):
        self.host = host
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from ollama import Client
            except ImportError as e:
                raise OllamaError(
                    "The 'ollama' python package is not installed in the wiki venv.\n\n"
                    + install_guidance()
                ) from e
            self._client = Client(host=self.host)
        return self._client

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        kwargs = {"dimensions": EMBED_DIM} if EMBED_DIM_EXPLICIT else {}
        try:
            resp = self.client.embed(model=self.model, input=inputs, **kwargs)
        except OllamaError:
            raise
        except Exception as e:  # connection / model-missing / client errors
            raise OllamaError(_diagnose_ollama(e)) from e
        embs = getattr(resp, "embeddings", None)
        if not embs:
            raise OllamaError("Ollama returned no embeddings.\n\n" + install_guidance())
        return [list(v) for v in embs]

    def embed_documents(self, texts: list[str], progress: bool = False) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            out.extend(self._embed(texts[start : start + EMBED_BATCH]))
            if progress:
                print(
                    f"  embedded {min(start + EMBED_BATCH, len(texts))}/{len(texts)} chunks",
                    file=sys.stderr,
                    flush=True,
                )
        return out

    def embed_query(self, query: str) -> list[float]:
        # Qwen3 is instruction-aware: queries get a task instruction, docs don't.
        prompt = f"Instruct: {QUERY_INSTRUCTION}\nQuery: {query}"
        return self._embed([prompt])[0]


# --------------------------------------------------------------------------- #
# Vector store: Qdrant local-mode HNSW
# --------------------------------------------------------------------------- #
class QdrantStore:
    def __init__(self, path: Path = QDRANT_PATH, collection: str = COLLECTION):
        from qdrant_client import QdrantClient
        from qdrant_client import models as qm

        self._qm = qm
        path.mkdir(parents=True, exist_ok=True)
        self.client = QdrantClient(path=str(path))
        self.collection = collection
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        qm = self._qm
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=qm.VectorParams(size=EMBED_DIM, distance=qm.Distance.COSINE),
                hnsw_config=qm.HnswConfigDiff(m=16, ef_construct=100),
            )

    def stored_hashes(self) -> dict[str, str]:
        """Map chunk id -> stored hash for incremental diffing."""
        out: dict[str, str] = {}
        next_page = None
        while True:
            points, next_page = self.client.scroll(
                collection_name=self.collection,
                with_payload=True,
                with_vectors=False,
                limit=512,
                offset=next_page,
            )
            for p in points:
                out[str(p.id)] = (p.payload or {}).get("hash", "")
            if next_page is None:
                break
        return out

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        qm = self._qm
        points = [
            qm.PointStruct(id=c.id, vector=v, payload=c.payload())
            for c, v in zip(chunks, vectors)
        ]
        if points:
            self.client.upsert(collection_name=self.collection, points=points)

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.client.delete(
                collection_name=self.collection,
                points_selector=self._qm.PointIdsList(points=ids),
            )

    def query(self, vector: list[float], limit: int, topic: str | None = None) -> list[tuple[str, float]]:
        qm = self._qm
        flt = None
        if topic:
            flt = qm.Filter(must=[qm.FieldCondition(key="topic", match=qm.MatchValue(value=topic))])
        res = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            query_filter=flt,
            with_payload=False,
            search_params=qm.SearchParams(hnsw_ef=128),
        )
        return [(str(p.id), float(p.score)) for p in res.points]

    def count(self) -> int:
        return self.client.count(collection_name=self.collection).count


# --------------------------------------------------------------------------- #
# Fusion
# --------------------------------------------------------------------------- #
def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over several ranked id lists."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
class WikiIndex:
    """Loads persisted chunks, serves search, and (re)builds the index."""

    def __init__(self):
        self._chunks: list[Chunk] | None = None
        self._by_id: dict[str, Chunk] | None = None
        self._bm25: Bm25Index | None = None
        self._store: QdrantStore | None = None
        self._embedder: OllamaEmbedder | None = None

    # --- lazy accessors ---------------------------------------------------- #
    def load_chunks(self) -> list[Chunk]:
        if self._chunks is None:
            if CHUNKS_PATH.exists():
                data = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
                self._chunks = [Chunk.from_payload(c["id"], c) for c in data.get("chunks", [])]
            else:
                self._chunks = []
            self._by_id = {c.id: c for c in self._chunks}
        return self._chunks

    @property
    def by_id(self) -> dict[str, Chunk]:
        self.load_chunks()
        return self._by_id or {}

    @property
    def bm25(self) -> Bm25Index:
        if self._bm25 is None:
            self._bm25 = Bm25Index(self.load_chunks())
        return self._bm25

    @property
    def store(self) -> QdrantStore:
        if self._store is None:
            self._store = QdrantStore()
        return self._store

    @property
    def embedder(self) -> OllamaEmbedder:
        if self._embedder is None:
            self._embedder = OllamaEmbedder()
        return self._embedder

    # --- search ------------------------------------------------------------ #
    def search(self, query: str, k: int = 8, mode: str = "hybrid", topic: str | None = None) -> list[SearchHit]:
        mode = (mode or "hybrid").lower()
        pool = max(k * 4, 30)

        lexical: list[tuple[str, float]] = []
        dense: list[tuple[str, float]] = []

        if mode in ("lexical", "hybrid"):
            lexical = self.bm25.rank(query, limit=pool)
            if topic:
                lexical = [(cid, s) for cid, s in lexical if self.by_id.get(cid) and self.by_id[cid].topic == topic]
        if mode in ("semantic", "hybrid"):
            vec = self.embedder.embed_query(query)  # may raise OllamaError
            dense = self.store.query(vec, limit=pool, topic=topic)

        dense_rank = {cid: i for i, (cid, _) in enumerate(dense)}
        lexical_rank = {cid: i for i, (cid, _) in enumerate(lexical)}

        if mode == "semantic":
            ordered = [(cid, s) for cid, s in dense]
        elif mode == "lexical":
            ordered = [(cid, s) for cid, s in lexical]
        else:
            ordered = rrf_fuse([[c for c, _ in dense], [c for c, _ in lexical]])

        hits: list[SearchHit] = []
        for cid, score in ordered:
            c = self.by_id.get(cid)
            if not c:
                continue
            hits.append(
                SearchHit(
                    chunk=c,
                    score=score,
                    dense_rank=dense_rank.get(cid),
                    lexical_rank=lexical_rank.get(cid),
                )
            )
            if len(hits) >= k:
                break
        return hits

    # --- context pack ------------------------------------------------------ #
    def context_pack(self, query: str, budget_chars: int = 8000, k: int = 12, mode: str = "hybrid") -> str:
        hits = self.search(query, k=k, mode=mode)
        if not hits:
            return f"No wiki matches for: {query}"

        related = self._related_docs_for([h.chunk.doc for h in hits])
        out: list[str] = [f"# Wiki context for: {query}", ""]
        used = len(out[0]) + len(query)
        seen_headings: set[tuple[str, str]] = set()

        for h in hits:
            key = (h.chunk.doc, h.chunk.heading)
            if key in seen_headings:
                continue
            seen_headings.add(key)
            head = f"## {h.chunk.title} \u2014 {h.chunk.heading or 'overview'} ({h.chunk.doc})"
            block = f"{head}\n{body(h.chunk.text)}\n"
            if used + len(block) > budget_chars and len(out) > 2:
                break
            out.append(block)
            used += len(block)

        if related:
            out.append("### Related docs")
            out.append("; ".join(related))
        return "\n".join(out)

    def _related_docs_for(self, docs: list[str]) -> list[str]:
        """Docs that share a topic or a declared dependency with the given docs."""
        seed = set(docs)
        chunks = self.load_chunks()
        topics = {c.topic for c in chunks if c.doc in seed}

        all_docs = list(iter_docs())
        seed_deps: set[str] = set()
        for d in all_docs:
            if d.rel in seed:
                seed_deps.update(d.depends)

        related: list[str] = []
        # Same-topic siblings (cheap, high-signal).
        for c in chunks:
            if c.doc not in seed and c.topic in topics and c.doc not in related:
                related.append(c.doc)
        # Dependency neighbours: docs that declare an overlapping **Depends:** entry.
        if seed_deps:
            for d in all_docs:
                if d.rel in seed or d.rel in related:
                    continue
                if seed_deps.intersection(d.depends):
                    related.append(d.rel)
        return related[:8]

    # --- build / reindex --------------------------------------------------- #
    def reindex(self, incremental: bool = True, progress: bool = False) -> dict:
        """Rebuild the vector store, re-embedding only what changed.

        Change detection is keyed on a per-chunk SHA-256 of the (title + topic +
        heading + body) text -- finer-grained than a whole-file hash, so editing
        one section of an article re-embeds only that section, not the whole doc.
        Removed chunks are deleted from Qdrant. ``incremental=False`` forces a
        full re-embed of every chunk.
        """
        docs = list(iter_docs())
        current = all_chunks(docs)
        current_by_id = {c.id: c for c in current}

        store = self.store
        stored = store.stored_hashes() if incremental else {}

        to_embed = [c for c in current if stored.get(c.id) != c.hash]
        removed = [cid for cid in stored if cid not in current_by_id]

        if to_embed:
            vectors = self.embedder.embed_documents([c.text for c in to_embed], progress=progress)
            store.upsert(to_embed, vectors)
        if removed:
            store.delete(removed)

        CHUNKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CHUNKS_PATH.write_text(
            json.dumps({"chunks": [c.payload() | {"id": c.id} for c in current]}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        # Reset caches so a long-lived process reflects the new state.
        self._chunks = None
        self._by_id = None
        self._bm25 = None
        return {
            "total_chunks": len(current),
            "embedded": len(to_embed),
            "removed": len(removed),
            "docs": len(docs),
            "vector_count": store.count(),
        }

    def ensure_fresh(self) -> dict:
        """Lazily reconcile the index if wiki content changed since last build.

        Best-effort: if the embedding backend is unavailable we keep serving the
        existing (stale) index rather than failing the query.
        """
        st = self.status()
        if st.get("stale"):
            try:
                return self.reindex(incremental=True)
            except OllamaError:
                pass
        return st

    def status(self) -> dict:
        current = all_chunks()
        persisted = {c.id: c.hash for c in self.load_chunks()}
        current_ids = {c.id for c in current}
        changed = [c.id for c in current if persisted.get(c.id) != c.hash]
        removed = [cid for cid in persisted if cid not in current_ids]
        return {
            "persisted_chunks": len(persisted),
            "current_chunks": len(current),
            "changed": len(changed),
            "removed": len(removed),
            "stale": bool(changed or removed),
            "chunks_index_exists": CHUNKS_PATH.exists(),
        }


# --------------------------------------------------------------------------- #
# Session warm-up (used by the sessionStart hook)
# --------------------------------------------------------------------------- #
def build_session_seed(topics_str: str = "", paths_str: str = "", prompt: str = "") -> str:
    """Compose a warm-up seed query.

    Precedence: the user's prompt (if the session payload carries one) is the
    strongest retrieval signal, so it dominates. Otherwise fall back to the
    matched topics + workspace repo. Topic folder names are expanded to their
    human labels (from topics.json) because a descriptive label
    ("Infrastructure & IaC (Pulumi)") is a far better seed than "infra".
    """
    prompt = (prompt or "").strip()
    try:
        tmap = json.loads(TOPICS_JSON.read_text(encoding="utf-8")).get("topics", {})
    except Exception:
        tmap = {}
    labels = [tmap.get(t, {}).get("label") or t for t in topics_str.split()]
    segments = re.findall(r"/([A-Za-z0-9_.-]+)", paths_str)
    repo_hint = [segments[-1]] if segments else []
    if prompt:
        # Prompt leads; a light repo hint helps disambiguate cross-repo terms.
        parts = [prompt] + repo_hint
    else:
        parts = repo_hint + labels
    return " ".join(dict.fromkeys(p for p in parts if p)).strip()


SESSION_SNIPPET_CHARS = int(os.environ.get("WIKI_SESSION_SNIPPET_CHARS", "1000"))


def session_context(seed_query: str, budget_chars: int = 9000, k: int = 6) -> str:
    """Best-effort warm-up context. Falls back to lexical if Ollama is down."""
    idx = WikiIndex()
    if not idx.load_chunks():
        return ""
    try:
        hits = idx.search(seed_query, k=k, mode="hybrid")
    except OllamaError:
        hits = idx.search(seed_query, k=k, mode="lexical")
    if not hits:
        return ""
    out = [
        "# Work Brain (local wiki) \u2014 relevant sections",
        "",
        "These are section previews from your local wiki, chosen for this session. "
        "They may be truncated \u2014 if a section looks relevant, read the full article "
        "at the path shown (or use the `wiki` MCP tools `get_article` / `search_wiki` / "
        "`get_context_pack`) before relying on it.",
        "",
    ]
    used = len(out[2])
    for h in hits:
        block = f"## {h.chunk.title} \u2014 {h.chunk.heading or 'overview'} ({h.chunk.doc})\n{snippet(h.chunk.text, SESSION_SNIPPET_CHARS)}\n"
        if used + len(block) > budget_chars and len(out) > 4:
            break
        out.append(block)
        used += len(block)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cli(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="wiki_search", description="Wiki hybrid search / index tools.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="hybrid/semantic/lexical search")
    p_search.add_argument("query")
    p_search.add_argument("-k", type=int, default=8)
    p_search.add_argument("--mode", choices=["hybrid", "semantic", "lexical"], default="hybrid")
    p_search.add_argument("--topic", default=None)
    p_search.add_argument("--json", action="store_true")

    p_ctx = sub.add_parser("context", help="assemble a budgeted context pack")
    p_ctx.add_argument("query")
    p_ctx.add_argument("--budget", type=int, default=8000)
    p_ctx.add_argument("-k", type=int, default=12)

    p_re = sub.add_parser("reindex", help="(re)build embeddings + vector store")
    p_re.add_argument("--full", action="store_true", help="rebuild everything, ignore stored hashes")

    sub.add_parser("status", help="report index freshness")

    p_sess = sub.add_parser("for-session", help="emit sessionStart additional_context JSON")
    p_sess.add_argument("--seed", default="", help="explicit seed query (overrides the rest)")
    p_sess.add_argument("--prompt", default="", help="user prompt, if the session payload has one")
    p_sess.add_argument("--topics", default="", help="space-separated topic folders")
    p_sess.add_argument("--paths", default="", help="workspace paths (repo hint source)")
    p_sess.add_argument("--budget", type=int, default=6000)

    args = ap.parse_args(argv)
    idx = WikiIndex()

    if args.cmd == "search":
        hits = idx.search(args.query, k=args.k, mode=args.mode, topic=args.topic)
        if args.json:
            print(json.dumps([h.to_dict() for h in hits], indent=2, ensure_ascii=False))
        else:
            for i, h in enumerate(hits, 1):
                print(f"\n{i}. {h.chunk.title} \u2014 {h.chunk.heading or 'overview'} ({h.chunk.doc})  score={h.score:.4f}")
                print(body(h.chunk.text))
        return 0

    if args.cmd == "context":
        print(idx.context_pack(args.query, budget_chars=args.budget, k=args.k))
        return 0

    if args.cmd == "reindex":
        summary = idx.reindex(incremental=not args.full, progress=True)
        print(json.dumps(summary, indent=2))
        return 0

    if args.cmd == "status":
        print(json.dumps(idx.status(), indent=2))
        return 0

    if args.cmd == "for-session":
        seed = args.seed or build_session_seed(args.topics, args.paths, args.prompt)
        if not seed:
            return 0
        ctx = session_context(seed, budget_chars=args.budget)
        if ctx:
            print(json.dumps({"additional_context": ctx}))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
