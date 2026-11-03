"""End-to-end RAG pipeline: ingest -> retrieve (hybrid) -> rerank -> generate -> validate."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from pathlib import Path

from app.config import Settings
from app.generation import answer as gen
from app.generation.testgen import generate_test_cases, suite_citations
from app.index import RetrievalResult, SpecIndex
from app.ingest.chunker import chunk_document
from app.ingest.parser import parse_document
from app.llm.providers import LLM, make_llm
from app.models import Answer, QueryRequest, TestCaseRequest, TestCaseSuite
from app.retrieval.embeddings import make_embedder
from app.retrieval.reranker import CrossEncoderReranker, LLMReranker, Reranker

log = logging.getLogger("specrag")


class Pipeline:
    def __init__(self, settings: Settings, llm: LLM | None = None, index: SpecIndex | None = None):
        self.s = settings
        self.llm = llm or make_llm(settings)
        if index is not None:
            self.index = index
        else:
            embedder = make_embedder(settings)
            if (settings.index_dir / "manifest.json").exists():
                self.index = SpecIndex.load(settings.index_dir, embedder)
            else:
                self.index = SpecIndex(embedder)
        self.reranker = self._make_reranker()

    def _make_reranker(self) -> Reranker:
        if self.s.reranker == "cross-encoder":
            return CrossEncoderReranker(self.s.rerank_model)
        if self.s.reranker == "llm":
            return LLMReranker(self.llm)
        return Reranker()

    # ------------------------------------------------------------------ ingest
    def ingest(self, path: Path, doc_id: str | None = None, title: str | None = None) -> dict:
        t = time.perf_counter()
        doc = parse_document(path, doc_id=doc_id, title=title)
        chunks = chunk_document(doc, self.s.chunk_tokens, self.s.chunk_overlap)
        self.index.add(chunks)
        self.index.save(self.s.index_dir)
        return {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "pages": doc.n_pages,
            "sections": len(doc.sections),
            "chunks": len(chunks),
            "tables": sum(c.is_table for c in chunks),
            "seconds": round(time.perf_counter() - t, 2),
        }

    # --------------------------------------------------------------- retrieval
    def retrieve(self, question: str, k: int | None = None, mode: str | None = None, doc_ids=None) -> RetrievalResult:
        k = k or self.s.top_k_context
        use_rerank = self.reranker.name != "none"
        res = self.index.search(
            question,
            k=self.s.top_k_candidates if use_rerank else k,
            mode=mode or self.s.retrieval_mode,
            candidates=self.s.top_k_candidates,
            rrf_k=self.s.rrf_k,
            doc_ids=doc_ids,
        )
        if use_rerank:
            t = time.perf_counter()
            res.results = self.reranker.rerank(question, res.results, k)
            res.timings_ms["rerank_ms"] = (time.perf_counter() - t) * 1000
        return res

    def _should_abstain(self, res: RetrievalResult) -> bool:
        if not res.results or res.lexical_confidence < self.s.abstain_threshold:
            return True
        dense_th = self.s.abstain_dense_threshold
        return bool(dense_th and res.dense_confidence is not None and res.dense_confidence < dense_th)

    # ------------------------------------------------------------------ answer
    def ask(self, req: QueryRequest) -> Answer:
        t0 = time.perf_counter()
        res = self.retrieve(req.question, req.top_k, req.mode, req.doc_ids)
        timings = dict(res.timings_ms)
        timings["retrieval_ms"] = (time.perf_counter() - t0) * 1000
        if self._should_abstain(res):
            return Answer(question=req.question, answer=gen.ABSTAIN_MESSAGE, citations=[], abstained=True,
                          contexts=res.results, timings_ms=timings, model=self.llm.name)
        t1 = time.perf_counter()
        raw = gen.generate(self.llm, req.question, res.results)
        timings["generation_ms"] = (time.perf_counter() - t1) * 1000
        text, citations, abstained = gen.finalize(raw, res.results)
        timings["total_ms"] = (time.perf_counter() - t0) * 1000
        return Answer(question=req.question, answer=text, citations=citations, abstained=abstained,
                      contexts=res.results, timings_ms=timings, model=self.llm.name)

    def ask_stream(self, req: QueryRequest) -> Iterator[dict]:
        """Yields events: contexts -> token* -> done (with validated citations)."""
        t0 = time.perf_counter()
        res = self.retrieve(req.question, req.top_k, req.mode, req.doc_ids)
        timings = {**res.timings_ms, "retrieval_ms": (time.perf_counter() - t0) * 1000}
        yield {"type": "contexts", "contexts": [c.model_dump() for c in res.results]}
        if self._should_abstain(res):
            yield {"type": "done", "answer": gen.ABSTAIN_MESSAGE, "citations": [], "abstained": True,
                   "timings_ms": timings, "model": self.llm.name}
            return
        parts: list[str] = []
        t1 = time.perf_counter()
        first_token_ms = None
        for tok in gen.generate_stream(self.llm, req.question, res.results):
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - t1) * 1000
            parts.append(tok)
            yield {"type": "token", "text": tok}
        timings["first_token_ms"] = first_token_ms or 0.0
        timings["generation_ms"] = (time.perf_counter() - t1) * 1000
        timings["total_ms"] = (time.perf_counter() - t0) * 1000
        text, citations, abstained = gen.finalize("".join(parts), res.results)
        yield {"type": "done", "answer": text, "citations": [c.model_dump() for c in citations],
               "abstained": abstained, "timings_ms": timings, "model": self.llm.name}

    # -------------------------------------------------------------- test cases
    def test_cases(self, req: TestCaseRequest) -> TestCaseSuite:
        query = f"{req.feature} command fields parameters status values error conditions requirements"
        res = self.retrieve(query, k=max(8, self.s.top_k_context))
        cases, dropped = generate_test_cases(self.llm, req.feature, req.count, res.results)
        return TestCaseSuite(feature=req.feature, test_cases=cases, citations=suite_citations(cases, res.results),
                             dropped_invalid=dropped, model=self.llm.name)
