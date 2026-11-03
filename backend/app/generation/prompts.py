from __future__ import annotations

from app.models import ScoredChunk

ANSWER_SYSTEM = """You are SpecRAG, an assistant for firmware and validation engineers working with \
storage interface specifications (NVMe, PCIe).

Rules:
1. Answer ONLY from the numbered sources. Never use outside knowledge, even if you are confident.
2. After every sentence that states a fact, cite its source(s) like [S1] or [S2][S4].
3. Copy opcodes, field names, bit positions, hex values and status codes exactly as the sources write them.
4. If the sources do not contain the answer, reply with exactly: INSUFFICIENT_CONTEXT
   If they contain only part of it, answer that part and say what is missing.
5. Be concise and technical. Use a short list or table when comparing fields or values."""

TESTGEN_SYSTEM = """You are a senior SSD firmware validation engineer. You write precise, executable \
test cases derived strictly from the specification excerpts you are given. Every expectation must be \
traceable to a cited section. You output only JSON."""


def format_context(contexts: list[ScoredChunk], max_chars: int = 1800) -> str:
    blocks = []
    for i, sc in enumerate(contexts, 1):
        c = sc.chunk
        page = f", page {c.page_start}" if c.page_start else ""
        blocks.append(f"[S{i}] {c.doc_title} - Section {c.section_id} {c.section_title}{page}\n{c.text[:max_chars]}")
    return "\n\n---\n\n".join(blocks)


def answer_prompt(question: str, contexts: list[ScoredChunk]) -> str:
    return f"Sources:\n\n{format_context(contexts)}\n\nQuestion: {question}\n\nAnswer (with [S#] citations):"


def testgen_prompt(feature: str, count: int, contexts: list[ScoredChunk]) -> str:
    section_ids = sorted({sc.chunk.section_id for sc in contexts})
    return f"""Specification excerpts:

{format_context(contexts)}

Write {count} test cases that validate a device's compliance for: {feature}

Cover a mix of categories: positive, negative (invalid fields / opcodes), boundary (min/max values),
error-handling (expected status codes) and state-transition, where the excerpts support them.

Return JSON of exactly this shape:
{{"test_cases": [{{
  "id": "TC-001",
  "title": "short imperative title",
  "objective": "what requirement this verifies",
  "category": "positive | negative | boundary | error-handling | state-transition",
  "preconditions": ["..."],
  "steps": [{{"action": "concrete host action incl. opcode/fields", "expected": "observable result"}}],
  "pass_criteria": ["..."],
  "spec_refs": ["section id the test is derived from"]
}}]}}

spec_refs must only use these section ids: {", ".join(section_ids)}.
Do not invent fields, opcodes or status codes that are not in the excerpts."""
