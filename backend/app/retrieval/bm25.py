"""Okapi BM25 over an inverted index (implemented from scratch, no external deps).

Lexical retrieval matters a lot for specs: opcode/field lookups ("what is CNS 01h",
"log identifier 02h") are exact-match problems that embedding models handle poorly.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from app.retrieval.text import tokenize

# Spec literals (06h, 81h, 31:16, cdw10) are the most discriminative tokens in a query:
# "what is status 1Dh" should rank the row containing 1Dh above a short generic paragraph
# that merely repeats "status code" several times.
LITERAL_RE = re.compile(r"^(?:[0-9a-f]+h|\d+:\d+|[a-z]*\d[a-z0-9]*)$")
LITERAL_BOOST = 2.0


class BM25Index:
    def __init__(self, k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.doc_len = np.zeros(0, dtype=np.float32)
        self.idf: dict[str, float] = {}
        self.avgdl = 0.0

    @property
    def n_docs(self) -> int:
        return int(self.doc_len.shape[0])

    def build(self, texts: list[str]) -> "BM25Index":
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        lengths = []
        for i, text in enumerate(texts):
            toks = tokenize(text)
            lengths.append(len(toks))
            for term, tf in Counter(toks).items():
                postings[term].append((i, tf))
        self.postings = dict(postings)
        self.doc_len = np.asarray(lengths, dtype=np.float32)
        self.avgdl = float(self.doc_len.mean()) if lengths else 0.0
        n = len(texts)
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}
        return self

    def scores(self, query: str) -> np.ndarray:
        scores = np.zeros(self.n_docs, dtype=np.float32)
        if not self.n_docs:
            return scores
        norm = self.k1 * (1 - self.b + self.b * self.doc_len / max(self.avgdl, 1e-9))
        for term, qtf in Counter(tokenize(query)).items():
            plist = self.postings.get(term)
            if not plist:
                continue
            ids = np.fromiter((d for d, _ in plist), dtype=np.int64, count=len(plist))
            tf = np.fromiter((f for _, f in plist), dtype=np.float32, count=len(plist))
            boost = LITERAL_BOOST if LITERAL_RE.match(term) else 1.0
            scores[ids] += boost * self.idf[term] * (tf * (self.k1 + 1)) / (tf + norm[ids]) * (1 + 0.2 * (qtf - 1))
        return scores

    def max_possible(self, query: str) -> float:
        """Upper bound used to normalise scores into [0, 1] for the abstention check.
        Query terms the corpus has never seen ("zoned", "retimer") count at the maximum
        IDF: a question built around unknown terms is probably out of scope."""
        oov_idf = math.log(1 + (self.n_docs + 0.5) / 0.5)
        return sum(
            (LITERAL_BOOST if LITERAL_RE.match(t) else 1.0) * self.idf.get(t, oov_idf) * (self.k1 + 1)
            for t in set(tokenize(query))
        ) or 1.0

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {"k1": self.k1, "b": self.b, "postings": self.postings, "doc_len": self.doc_len.tolist()}
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        data = json.loads(path.read_text(encoding="utf-8"))
        idx = cls(data["k1"], data["b"])
        idx.postings = {t: [tuple(p) for p in plist] for t, plist in data["postings"].items()}
        idx.doc_len = np.asarray(data["doc_len"], dtype=np.float32)
        idx.avgdl = float(idx.doc_len.mean()) if idx.n_docs else 0.0
        n = idx.n_docs
        idx.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in idx.postings.items()}
        return idx
