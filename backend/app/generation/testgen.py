"""Spec-grounded test-case generation.

The LLM drafts structured test cases; the output is then validated:
  1. JSON is parsed (with one repair retry on failure),
  2. each case is validated against the TestCase schema,
  3. each case's spec_refs must point at sections that were actually retrieved -
     test cases citing sections the model never saw are rejected as ungrounded.
"""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.generation.answer import SENT_RE, to_citation
from app.generation.prompts import TESTGEN_SYSTEM, testgen_prompt
from app.llm.providers import LLM, ExtractiveLLM
from app.models import ScoredChunk, TestCase, TestStep

NORMATIVE_RE = re.compile(r"\b(shall|should|is required to|must)\b", re.IGNORECASE)


def _extract_json(raw: str) -> dict:
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(raw[start : end + 1])


def _ref_ok(ref: str, allowed: set[str]) -> bool:
    ref = ref.strip().lstrip("§").split()[0] if ref.strip() else ""
    # a reference to a parent of a retrieved section (5.15 for 5.15.2) is also grounded
    return any(a == ref or a.startswith(ref + ".") or ref.startswith(a + ".") for a in allowed)


def validate_cases(data: dict, allowed_sections: set[str]) -> tuple[list[TestCase], int]:
    cases, dropped = [], 0
    for i, item in enumerate(data.get("test_cases", []), 1):
        try:
            item.setdefault("id", f"TC-{i:03d}")
            tc = TestCase.model_validate(item)
        except ValidationError:
            dropped += 1
            continue
        refs = [r for r in tc.spec_refs if _ref_ok(r, allowed_sections)]
        if not refs:
            dropped += 1
            continue
        tc.spec_refs = refs
        cases.append(tc)
    return cases, dropped


def _extractive_cases(feature: str, contexts: list[ScoredChunk], count: int) -> list[TestCase]:
    """LLM-free fallback: turn normative sentences ("... shall ...") into test cases."""
    cases: list[TestCase] = []
    for sc in contexts:
        for sent in SENT_RE.split(sc.chunk.text.replace("\n", " ")):
            if len(cases) >= count:
                return cases
            if not NORMATIVE_RE.search(sent) or len(sent.split()) < 6:
                continue
            c = sc.chunk
            cases.append(
                TestCase(
                    id=f"TC-{len(cases) + 1:03d}",
                    title=f"Verify: {sent.strip()[:80]}",
                    objective=f"Requirement from §{c.section_id} {c.section_title}",
                    category="positive",
                    preconditions=["Controller is enabled (CC.EN=1, CSTS.RDY=1)", "Admin queues are configured"],
                    steps=[TestStep(action=f"Exercise the behavior described in §{c.section_id}", expected=sent.strip())],
                    pass_criteria=[sent.strip()],
                    spec_refs=[c.section_id],
                )
            )
    return cases


def generate_test_cases(llm: LLM, feature: str, count: int, contexts: list[ScoredChunk]) -> tuple[list[TestCase], int]:
    allowed = {sc.chunk.section_id for sc in contexts}
    if isinstance(llm, ExtractiveLLM):
        return _extractive_cases(feature, contexts, count), 0

    prompt = testgen_prompt(feature, count, contexts)
    raw = llm.complete(TESTGEN_SYSTEM, prompt, max_tokens=3000, json_mode=True)
    try:
        data = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as e:
        repair = f"{prompt}\n\nYour previous output was not valid JSON ({e}). Return only the JSON object."
        data = _extract_json(llm.complete(TESTGEN_SYSTEM, repair, max_tokens=3000, json_mode=True))
    return validate_cases(data, allowed)


def suite_citations(cases: list[TestCase], contexts: list[ScoredChunk]):
    used = {r for tc in cases for r in tc.spec_refs}
    out, seen = [], set()
    for i, sc in enumerate(contexts, 1):
        sid = sc.chunk.section_id
        if sid in seen:
            continue
        if any(sid == r or sid.startswith(r + ".") or r.startswith(sid + ".") for r in used):
            seen.add(sid)
            out.append(to_citation(i, sc))
    return out
