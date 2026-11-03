"""Grounded answer generation with citation validation and abstention."""
from __future__ import annotations

import re
from collections.abc import Iterator

from app.generation.prompts import ANSWER_SYSTEM, answer_prompt
from app.llm.providers import LLM, ExtractiveLLM
from app.models import Citation, ScoredChunk
from app.retrieval.text import tokenize

ABSTAIN_TOKEN = "INSUFFICIENT_CONTEXT"
ABSTAIN_MESSAGE = (
    "I couldn't find this in the indexed specifications. Try rephrasing with the command, "
    "log page or feature name, or ingest the spec revision that covers it."
)
MARKER_RE = re.compile(r"\[S(\d+)\]")
MULTI_MARKER_RE = re.compile(r"\[S\d+(?:\s*,\s*S?\d+)+\]")
SENT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_multi_markers(text: str) -> str:
    """Normalise "[S1, S3]" / "[S1,3]" to "[S1][S3]"."""
    def repl(m: re.Match) -> str:
        nums = re.findall(r"\d+", m.group(0))
        return "".join(f"[S{n}]" for n in nums)

    return MULTI_MARKER_RE.sub(repl, text)


def finalize(raw: str, contexts: list[ScoredChunk]) -> tuple[str, list[Citation], bool]:
    """Validate citations: drop markers that point at sources that don't exist,
    renumber nothing (markers match the context panel), return cited sources only."""
    text = _split_multi_markers(raw.strip())
    if ABSTAIN_TOKEN in text and len(text) < len(ABSTAIN_TOKEN) + 40:
        return ABSTAIN_MESSAGE, [], True
    n = len(contexts)
    text = MARKER_RE.sub(lambda m: m.group(0) if 1 <= int(m.group(1)) <= n else "", text)
    used = sorted({int(m) for m in MARKER_RE.findall(text)})
    citations = [to_citation(i, contexts[i - 1]) for i in used]
    return text, citations, False


def to_citation(i: int, sc: ScoredChunk) -> Citation:
    c = sc.chunk
    return Citation(
        marker=f"S{i}",
        chunk_id=c.id,
        section_id=c.section_id,
        section_title=c.section_title,
        doc_title=c.doc_title,
        page=c.page_start,
        snippet=c.text[:400],
    )


def citation_coverage(text: str) -> float:
    """Fraction of factual sentences that carry at least one citation."""
    sents = [s for s in SENT_RE.split(text) if len(s.split()) >= 4]
    if not sents:
        return 0.0
    return sum(1 for s in sents if MARKER_RE.search(s)) / len(sents)


def _units(text: str) -> list[str]:
    """Answerable units of a chunk: sentences for prose, one row per line for tables
    (rendered as 'Caption: cell - cell - cell' so a row reads as a statement)."""
    lines = text.splitlines()
    rows = [l for l in lines if l.strip().startswith("|") and not re.match(r"^\|[\s\-:|]+\|$", l.strip())]
    if not rows:
        return [s.strip() for s in SENT_RE.split(text.replace("\n", " ")) if s.strip()]
    caption = lines[0].strip() if not lines[0].strip().startswith("|") else ""
    body = rows[1:] if len(rows) > 1 else rows  # first piped row is the header
    prose = " ".join(l for l in lines[1:] if l.strip() and not l.strip().startswith("|"))
    units = [f"{caption}: " * bool(caption) + " - ".join(c.strip() for c in r.strip().strip("|").split("|")) for r in body]
    return units + [s.strip() for s in SENT_RE.split(prose) if s.strip()]


def extractive_answer(question: str, contexts: list[ScoredChunk], max_sentences: int = 3) -> str:
    """LLM-free fallback: pick the sentences with the highest query-term overlap."""
    q = set(tokenize(question))
    scored = []
    for i, sc in enumerate(contexts[:4], 1):
        for unit in _units(sc.chunk.text):
            toks = set(tokenize(unit))
            if len(toks) < 3:
                continue
            overlap = len(q & toks) / (len(q) ** 0.5 + 1e-9)
            scored.append((overlap - 0.05 * (i - 1), i, unit))
    scored.sort(reverse=True)
    picked = [(i, s) for score, i, s in scored[:max_sentences] if score > 0]
    if not picked:
        return ABSTAIN_TOKEN
    return " ".join(f"{s.rstrip('.')}. [S{i}]" for i, s in picked)


def generate(llm: LLM, question: str, contexts: list[ScoredChunk]) -> str:
    if isinstance(llm, ExtractiveLLM):
        return extractive_answer(question, contexts)
    return llm.complete(ANSWER_SYSTEM, answer_prompt(question, contexts), max_tokens=900)


def generate_stream(llm: LLM, question: str, contexts: list[ScoredChunk]) -> Iterator[str]:
    if isinstance(llm, ExtractiveLLM):
        yield extractive_answer(question, contexts)
        return
    yield from llm.stream(ANSWER_SYSTEM, answer_prompt(question, contexts), max_tokens=900)
