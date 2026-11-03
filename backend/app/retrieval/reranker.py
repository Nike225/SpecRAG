"""Second-stage rerankers. Hybrid retrieval optimises recall over ~30-60 candidates;
the reranker reads (query, passage) jointly and fixes the precision at the top."""
from __future__ import annotations

import json
import re

from app.models import ScoredChunk


class Reranker:
    name = "none"

    def rerank(self, query: str, candidates: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        return candidates[:top_k]


class CrossEncoderReranker(Reranker):
    def __init__(self, model: str):
        from sentence_transformers import CrossEncoder  # optional dependency

        self.name = f"cross-encoder:{model}"
        self.model = CrossEncoder(model, max_length=512)

    def rerank(self, query: str, candidates: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        if not candidates:
            return []
        scores = self.model.predict([(query, c.chunk.embed_text()) for c in candidates])
        for c, s in zip(candidates, scores):
            c.scores["rerank"] = float(s)
            c.score = float(s)
        return sorted(candidates, key=lambda c: c.score, reverse=True)[:top_k]


class LLMReranker(Reranker):
    """Listwise reranking with the configured LLM in a single call (no extra model)."""

    def __init__(self, llm):
        self.name = f"llm:{llm.name}"
        self.llm = llm

    def rerank(self, query: str, candidates: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        pool = candidates[: max(top_k * 3, 12)]
        passages = "\n\n".join(f"[{i}] {c.chunk.embed_text()[:700]}" for i, c in enumerate(pool))
        prompt = (
            f"Question: {query}\n\nPassages:\n{passages}\n\n"
            f"Return a JSON list of the {top_k} passage numbers most useful for answering the "
            "question, most relevant first. Output only the JSON list, e.g. [3, 0, 7]."
        )
        try:
            raw = self.llm.complete("You rank passages by relevance.", prompt, max_tokens=100)
            order = [int(i) for i in json.loads(re.search(r"\[[\d,\s]*\]", raw).group(0))]
        except Exception:
            return candidates[:top_k]
        seen, ranked = set(), []
        for i in order:
            if 0 <= i < len(pool) and i not in seen:
                seen.add(i)
                ranked.append(pool[i])
        ranked += [c for i, c in enumerate(pool) if i not in seen]
        return ranked[:top_k]
