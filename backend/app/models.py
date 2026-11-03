"""Core data types shared across ingestion, retrieval, generation and the API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    id: str
    doc_id: str
    doc_title: str
    section_id: str  # e.g. "5.15.2"  (numbered heading in the spec)
    section_title: str  # e.g. "Identify Controller data structure"
    section_path: list[str] = Field(default_factory=list)  # breadcrumb of parent headings
    page_start: int | None = None
    page_end: int | None = None
    text: str
    is_table: bool = False

    @property
    def citation_label(self) -> str:
        page = f", p.{self.page_start}" if self.page_start else ""
        return f"{self.doc_title} §{self.section_id} {self.section_title}{page}"

    def embed_text(self) -> str:
        """Text used for indexing: the heading breadcrumb is prepended so a chunk deep
        inside a section still matches queries about that section's topic."""
        crumbs = " > ".join(self.section_path + [f"{self.section_id} {self.section_title}"])
        return f"{crumbs}\n{self.text}"


class ScoredChunk(BaseModel):
    chunk: Chunk
    score: float
    scores: dict[str, float] = Field(default_factory=dict)  # per-retriever ranks/scores for debugging


class Citation(BaseModel):
    marker: str  # "S1"
    chunk_id: str
    section_id: str
    section_title: str
    doc_title: str
    page: int | None = None
    snippet: str


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    mode: Literal["bm25", "dense", "hybrid"] | None = None
    doc_ids: list[str] | None = None


class Answer(BaseModel):
    question: str
    answer: str
    citations: list[Citation]
    abstained: bool = False
    contexts: list[ScoredChunk] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    model: str = ""


class TestStep(BaseModel):
    action: str
    expected: str


class TestCase(BaseModel):
    id: str
    title: str
    objective: str
    category: Literal["positive", "negative", "boundary", "error-handling", "state-transition"]
    preconditions: list[str]
    steps: list[TestStep]
    pass_criteria: list[str]
    spec_refs: list[str] = Field(description="Section ids this test is derived from, e.g. ['5.15']")


class TestCaseRequest(BaseModel):
    feature: str = Field(min_length=3, max_length=500)
    count: int = Field(default=5, ge=1, le=15)


class TestCaseSuite(BaseModel):
    feature: str
    test_cases: list[TestCase]
    citations: list[Citation]
    dropped_invalid: int = 0  # test cases rejected because they cited sections not retrieved
    model: str = ""
