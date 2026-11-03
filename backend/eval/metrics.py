"""Retrieval and answer-quality metrics. Relevance is judged at the section level:
a retrieved chunk is relevant if its (doc_id, section_id) is a gold section."""
from __future__ import annotations

import math
import re

import numpy as np


def section_key(doc_id: str, section_id: str) -> str:
    return f"{doc_id}:{section_id}"


def dedupe(keys: list[str]) -> list[str]:
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def hit_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    return float(any(r in gold for r in ranked[:k]))


def recall_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    return len(gold & set(ranked[:k])) / len(gold) if gold else 0.0


def mrr_at_k(ranked: list[str], gold: set[str], k: int = 10) -> float:
    for i, r in enumerate(ranked[:k], 1):
        if r in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list[str], gold: set[str], k: int = 10) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, r in enumerate(ranked[:k], 1) if r in gold)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / ideal if ideal else 0.0


def keyword_recall(answer: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    a = answer.lower()
    return sum(1 for kw in keywords if kw.lower() in a) / len(keywords)


def section_match(cited: str, gold: set[str]) -> bool:
    """A citation is correct if it is a gold section, or its parent/child (citing 5.6 for a
    question whose gold is 5.6.1 is still a correct pointer into the spec)."""
    doc, _, sec = cited.partition(":")
    for g in gold:
        gdoc, _, gsec = g.partition(":")
        if doc == gdoc and (sec == gsec or sec.startswith(gsec + ".") or gsec.startswith(sec + ".")):
            return True
    return False


def percentile(values: list[float], p: float) -> float:
    return float(np.percentile(values, p)) if values else 0.0


def sentence_split(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
