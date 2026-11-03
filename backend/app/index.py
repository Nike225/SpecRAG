"""The searchable index: chunks + BM25 + dense vectors, with hybrid retrieval via
Reciprocal Rank Fusion, persisted to disk."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.models import Chunk, ScoredChunk
from app.retrieval.bm25 import BM25Index
from app.retrieval.embeddings import Embedder
from app.retrieval.text import expand_query

EXPANSION_WEIGHT = 0.35


@dataclass
class RetrievalResult:
    results: list[ScoredChunk]
    lexical_confidence: float  # best BM25 score normalised to [0, 1]
    dense_confidence: float | None = None  # best cosine similarity, when dense retrieval ran
    timings_ms: dict[str, float] = field(default_factory=dict)


class SpecIndex:
    def __init__(self, embedder: Embedder | None):
        self.embedder = embedder
        self.chunks: list[Chunk] = []
        self.bm25 = BM25Index()
        self.vectors = np.zeros((0, 0), dtype=np.float32)
        self._lock = threading.RLock()

    # ----------------------------------------------------------------- building
    def _rebuild(self, new_chunks: list[Chunk] | None = None) -> None:
        texts = [c.embed_text() for c in self.chunks]
        self.bm25 = BM25Index().build(texts)
        if self.embedder is None:
            return
        if self.embedder.needs_fit or new_chunks is None or self.vectors.shape[0] == 0:
            self.embedder.fit(texts)
            self.vectors = self.embedder.embed_documents(texts) if texts else np.zeros((0, 0), np.float32)
        elif new_chunks:
            new_vecs = self.embedder.embed_documents([c.embed_text() for c in new_chunks])
            self.vectors = np.vstack([self.vectors, new_vecs])

    def build(self, chunks: list[Chunk]) -> "SpecIndex":
        with self._lock:
            self.chunks = list(chunks)
            self._rebuild()
        return self

    def add(self, chunks: list[Chunk]) -> None:
        """Add (or replace) a document's chunks."""
        with self._lock:
            doc_ids = {c.doc_id for c in chunks}
            if any(c.doc_id in doc_ids for c in self.chunks):
                self.chunks = [c for c in self.chunks if c.doc_id not in doc_ids] + list(chunks)
                self._rebuild()
            else:
                self.chunks.extend(chunks)
                self._rebuild(new_chunks=chunks)

    def remove(self, doc_id: str) -> int:
        with self._lock:
            before = len(self.chunks)
            keep = [i for i, c in enumerate(self.chunks) if c.doc_id != doc_id]
            self.chunks = [self.chunks[i] for i in keep]
            if self.vectors.size and not (self.embedder and self.embedder.needs_fit):
                self.vectors = self.vectors[keep]
                self.bm25 = BM25Index().build([c.embed_text() for c in self.chunks])
            else:
                self._rebuild()
            return before - len(self.chunks)

    # ---------------------------------------------------------------- retrieval
    def _mask(self, doc_ids: list[str] | None) -> np.ndarray | None:
        if not doc_ids:
            return None
        allowed = set(doc_ids)
        return np.array([c.doc_id in allowed for c in self.chunks])

    @staticmethod
    def _top(scores: np.ndarray, n: int, mask: np.ndarray | None) -> list[int]:
        if mask is not None:
            scores = np.where(mask, scores, -np.inf)
        n = min(n, scores.shape[0])
        if n <= 0:
            return []
        idx = np.argpartition(-scores, n - 1)[:n]
        idx = idx[np.argsort(-scores[idx])]
        return [int(i) for i in idx if np.isfinite(scores[i]) and scores[i] > -1e9]

    def search(
        self,
        query: str,
        k: int = 6,
        mode: str = "hybrid",
        candidates: int = 30,
        rrf_k: int = 60,
        doc_ids: list[str] | None = None,
        expand: bool = True,
    ) -> RetrievalResult:
        with self._lock:
            timings: dict[str, float] = {}
            if not self.chunks:
                return RetrievalResult([], 0.0, None, timings)
            mask = self._mask(doc_ids)
            fused: dict[int, dict[str, float]] = {}
            dense_conf: float | None = None

            t = time.perf_counter()
            bm = self.bm25.scores(query)
            expansion = expand_query(query)[len(query):].strip() if expand else ""
            if expansion:  # acronym expansions help recall but get a lower weight than the user's words
                bm = bm + EXPANSION_WEIGHT * self.bm25.scores(expansion)
            top_bm = self._top(bm, candidates, mask)
            timings["bm25_ms"] = (time.perf_counter() - t) * 1000
            lexical_conf = float(bm[top_bm[0]] / self.bm25.max_possible(query)) if top_bm else 0.0
            lexical_conf = min(1.0, lexical_conf)

            if mode in ("bm25", "hybrid"):
                for rank, i in enumerate(top_bm):
                    if bm[i] <= 0:
                        break
                    d = fused.setdefault(i, {})
                    d["bm25"], d["bm25_rank"] = float(bm[i]), rank + 1

            if mode in ("dense", "hybrid"):
                if self.embedder is None or self.vectors.size == 0:
                    if mode == "dense":
                        raise RuntimeError("Dense retrieval requested but no embeddings are built")
                else:
                    t = time.perf_counter()
                    q = self.embedder.embed_query(query)
                    dense = self.vectors @ q
                    dense_conf = float(np.max(np.where(mask, dense, -1.0) if mask is not None else dense))
                    for rank, i in enumerate(self._top(dense, candidates, mask)):
                        d = fused.setdefault(i, {})
                        d["dense"], d["dense_rank"] = float(dense[i]), rank + 1
                    timings["dense_ms"] = (time.perf_counter() - t) * 1000

            # Reciprocal Rank Fusion: robust to the very different score scales of BM25
            # and cosine similarity, no tuning of weights required.
            for d in fused.values():
                d["rrf"] = sum(1.0 / (rrf_k + d[r]) for r in ("bm25_rank", "dense_rank") if r in d)
            key = {"bm25": "bm25", "dense": "dense"}.get(mode, "rrf")
            order = sorted(fused, key=lambda i: fused[i].get(key, 0.0), reverse=True)[:k]
            results = [
                ScoredChunk(chunk=self.chunks[i], score=fused[i].get(key, 0.0), scores=fused[i]) for i in order
            ]
            return RetrievalResult(results, lexical_conf, dense_conf, timings)

    # ------------------------------------------------------------- persistence
    FILES = ("chunks.jsonl", "bm25.json", "vectors.npy", "embedder.pkl", "manifest.json")

    @staticmethod
    def _retry(fn, attempts: int = 20, delay: float = 0.5):
        """Windows: sync clients (OneDrive, Dropbox) and antivirus briefly lock files that were
        just written. Retry instead of failing the whole ingest."""
        for i in range(attempts):
            try:
                return fn()
            except PermissionError:
                if i == attempts - 1:
                    raise
                time.sleep(delay)

    def _write(self, path: Path, writer) -> None:
        """Write to a temp file, then atomically swap it in: an interrupted save can never
        leave a half-written index behind."""
        tmp = path.with_name(path.name + ".tmp")
        writer(tmp)
        if not tmp.exists():  # nothing to persist (e.g. Ollama/OpenAI embedders keep no local state)
            if path.exists():
                self._retry(path.unlink)  # don't leave a stale file from a previous embedder
            return
        self._retry(lambda: os.replace(tmp, path))

    def save(self, directory: Path) -> None:
        with self._lock:
            directory.mkdir(parents=True, exist_ok=True)

            def write_chunks(p: Path) -> None:
                with p.open("w", encoding="utf-8") as f:
                    for c in self.chunks:
                        f.write(c.model_dump_json() + "\n")

            def write_vectors(p: Path) -> None:
                with p.open("wb") as f:  # file handle: np.save(path) would append ".npy"
                    np.save(f, self.vectors)

            manifest = json.dumps(
                {
                    "embedder": self.embedder.name if self.embedder else None,
                    "n_chunks": len(self.chunks),
                    "documents": self.documents(),
                },
                indent=2,
            )
            self._write(directory / "chunks.jsonl", write_chunks)
            self._write(directory / "bm25.json", self.bm25.save)
            if self.embedder is not None:
                self._write(directory / "vectors.npy", write_vectors)
                self._write(directory / "embedder.pkl", self.embedder.save)
            # manifest last: its presence marks a complete index
            self._write(directory / "manifest.json", lambda p: p.write_text(manifest, encoding="utf-8"))

    @classmethod
    def clear(cls, directory: Path) -> None:
        """Delete the index files but keep the folder (removing a synced folder fails on Windows)."""
        if not directory.exists():
            return
        for name in ("manifest.json", *cls.FILES):  # manifest first: a partial index is never loaded
            for p in (directory / name, directory / (name + ".tmp")):
                if p.exists():
                    cls._retry(p.unlink)

    @classmethod
    def load(cls, directory: Path, embedder: Embedder | None) -> "SpecIndex":
        idx = cls(embedder)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if embedder is not None and manifest.get("embedder") != embedder.name:
            raise ValueError(
                f"Index was built with embedder {manifest.get('embedder')!r} but {embedder.name!r} is "
                "configured. Re-run ingestion (scripts/ingest.py --rebuild) or change EMBED_PROVIDER."
            )
        with (directory / "chunks.jsonl").open(encoding="utf-8") as f:
            idx.chunks = [Chunk.model_validate_json(line) for line in f if line.strip()]
        idx.bm25 = BM25Index.load(directory / "bm25.json")
        if embedder is not None and (directory / "vectors.npy").exists():
            idx.vectors = np.load(directory / "vectors.npy")
            embedder.load(directory / "embedder.pkl")
        return idx

    def documents(self) -> list[dict]:
        docs: dict[str, dict] = {}
        for c in self.chunks:
            d = docs.setdefault(c.doc_id, {"doc_id": c.doc_id, "title": c.doc_title, "chunks": 0, "sections": set()})
            d["chunks"] += 1
            d["sections"].add(c.section_id)
        return [{**d, "sections": len(d["sections"])} for d in docs.values()]
