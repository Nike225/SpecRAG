"""Evaluation harness: retrieval ablation + abstention + (optional) answer quality.

    # retrieval ablation on the bundled sample corpus (fully offline)
    python -m eval.run_eval --corpus data/sample --embed-provider lsa

    # on your real indexed specs, with Ollama embeddings, plus answer quality with the LLM
    python -m eval.run_eval --index-dir data/index --dataset eval/datasets/my_qa.jsonl --generate

Writes eval/results/<name>.json and eval/results/<name>.md
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.generation.answer import citation_coverage  # noqa: E402
from app.index import SpecIndex  # noqa: E402
from app.ingest.chunker import chunk_document  # noqa: E402
from app.ingest.parser import parse_document  # noqa: E402
from app.llm.providers import make_llm  # noqa: E402
from app.models import Chunk  # noqa: E402
from app.models import QueryRequest  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.retrieval.embeddings import make_embedder  # noqa: E402
from eval import metrics as M  # noqa: E402

CONFIGS = {
    "bm25": dict(mode="bm25", expand=False),
    "bm25+expansion": dict(mode="bm25", expand=True),
    "dense": dict(mode="dense", expand=False),
    "hybrid (RRF)": dict(mode="hybrid", expand=True),
}


def load_dataset(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def fixed_size_chunks(doc, max_tokens: int, overlap: int) -> list[Chunk]:
    """Baseline for the ablation: the common 'split every N tokens' approach. It ignores
    section boundaries, so the best section it can cite is the one the window starts in."""
    words: list[tuple[str, str, str]] = []  # (word, section_id, section_title)
    for sec in doc.sections:
        for w in sec.text.split():
            words.append((w, sec.section_id, sec.title))
    size, step = int(max_tokens / 1.33), int((max_tokens - overlap) / 1.33)
    chunks = []
    for n, start in enumerate(range(0, max(1, len(words)), step)):
        window = words[start : start + size]
        if not window:
            break
        chunks.append(Chunk(id=f"{doc.doc_id}:fixed:{n}", doc_id=doc.doc_id, doc_title=doc.title,
                            section_id=window[0][1], section_title=window[0][2],
                            text=" ".join(w for w, _, _ in window)))
    return chunks


def build_index(corpus: Path, embedder, chunker: str = "section") -> SpecIndex:
    chunks = []
    for f in sorted(corpus.rglob("*")):
        if f.suffix.lower() in {".pdf", ".md", ".markdown"}:
            doc = parse_document(f)
            if chunker == "fixed":
                chunks += fixed_size_chunks(doc, settings.chunk_tokens, settings.chunk_overlap)
            else:
                chunks += chunk_document(doc, settings.chunk_tokens, settings.chunk_overlap)
    return SpecIndex(embedder).build(chunks)


def eval_retrieval(index: SpecIndex, data: list[dict], cfg: dict) -> dict:
    per_type: dict[str, list[dict]] = {}
    latencies = []
    for item in data:
        if not item["gold"]:
            continue
        gold = set(item["gold"])
        t = time.perf_counter()
        res = index.search(item["question"], k=20, candidates=settings.top_k_candidates, rrf_k=settings.rrf_k, **cfg)
        latencies.append((time.perf_counter() - t) * 1000)
        ranked = M.dedupe([M.section_key(r.chunk.doc_id, r.chunk.section_id) for r in res.results])
        row = {
            "hit@1": M.hit_at_k(ranked, gold, 1),
            "hit@5": M.hit_at_k(ranked, gold, 5),
            "recall@5": M.recall_at_k(ranked, gold, 5),
            "mrr@10": M.mrr_at_k(ranked, gold, 10),
            "ndcg@10": M.ndcg_at_k(ranked, gold, 10),
        }
        per_type.setdefault(item.get("type", "all"), []).append(row)
    out = {}
    all_rows = [r for rows in per_type.values() for r in rows]
    for name, rows in [("all", all_rows), *sorted(per_type.items())]:
        out[name] = {m: round(statistics.mean(r[m] for r in rows), 4) for m in rows[0]} | {"n": len(rows)}
    out["latency_ms"] = {"p50": round(M.percentile(latencies, 50), 2), "p95": round(M.percentile(latencies, 95), 2)}
    return out


def eval_abstention(index: SpecIndex, data: list[dict], threshold: float) -> dict:
    """How well does the retrieval-side gate separate in-scope from out-of-scope questions?
    Sweeps the lexical and dense thresholds; 'best' maximises balanced accuracy."""
    rows = []
    for item in data:
        res = index.search(item["question"], k=1, mode="hybrid" if index.vectors.size else "bm25")
        rows.append((res.lexical_confidence, res.dense_confidence, bool(item["gold"])))

    def score(signal: int, th: float) -> dict:
        ans = [r[signal] for r in rows if r[2] and r[signal] is not None]
        una = [r[signal] for r in rows if not r[2] and r[signal] is not None]
        tr = sum(c < th for c in una) / len(una) if una else 1.0  # correctly refused
        fa = sum(c < th for c in ans) / len(ans) if ans else 0.0  # wrongly refused
        return {"threshold": round(th, 3), "unanswerable_refused": round(tr, 4),
                "answerable_wrongly_refused": round(fa, 4), "balanced_accuracy": round((tr + 1 - fa) / 2, 4)}

    def best(signal: int, grid) -> dict:
        return max((score(signal, t) for t in grid), key=lambda s: (s["balanced_accuracy"], -s["answerable_wrongly_refused"]))

    out = {
        "n_unanswerable": sum(not r[2] for r in rows),
        "lexical_configured": score(0, threshold),
        "lexical_best": best(0, [t / 200 for t in range(2, 120, 2)]),
    }
    if any(r[1] is not None for r in rows):
        out["dense_best"] = best(1, [t / 100 for t in range(0, 100, 2)])
    return out


def eval_answers(pipe: Pipeline, data: list[dict]) -> dict:
    rows, lat, first_tok = [], [], []
    for item in data:
        t = time.perf_counter()
        ans = pipe.ask(QueryRequest(question=item["question"]))
        lat.append((time.perf_counter() - t) * 1000)
        gold = set(item["gold"])
        if not gold:
            rows.append({"type": "unanswerable", "abstained": ans.abstained})
            continue
        cited = [M.section_key(next(c.chunk.doc_id for c in ans.contexts if c.chunk.id == ci.chunk_id), ci.section_id)
                 for ci in ans.citations]
        rows.append({
            "type": item.get("type", "all"),
            "abstained": ans.abstained,
            "keyword_recall": M.keyword_recall(ans.answer, item.get("keywords", [])),
            "citation_precision": (sum(M.section_match(c, gold) for c in cited) / len(cited)) if cited else 0.0,
            "has_correct_citation": float(any(M.section_match(c, gold) for c in cited)),
            "citation_coverage": citation_coverage(ans.answer),
        })
    answerable = [r for r in rows if r["type"] != "unanswerable"]
    unanswerable = [r for r in rows if r["type"] == "unanswerable"]
    mean = lambda key: round(statistics.mean(r[key] for r in answerable), 4) if answerable else None  # noqa: E731
    return {
        "model": pipe.llm.name,
        "n": len(rows),
        "keyword_recall": mean("keyword_recall"),
        "citation_precision": mean("citation_precision"),
        "answers_with_correct_citation": mean("has_correct_citation"),
        "citation_coverage": mean("citation_coverage"),
        "false_abstention_rate": round(sum(r["abstained"] for r in answerable) / len(answerable), 4) if answerable else None,
        "correct_abstention_rate": round(sum(r["abstained"] for r in unanswerable) / len(unanswerable), 4) if unanswerable else None,
        "latency_ms": {"p50": round(M.percentile(lat, 50), 1), "p95": round(M.percentile(lat, 95), 1)},
    }


def to_markdown(report: dict) -> str:
    lines = [f"# SpecRAG evaluation - {report['name']}", "",
             f"- Generated: {report['timestamp']}", f"- Dataset: `{report['dataset']}` ({report['n_questions']} questions)",
             f"- Index: {report['n_chunks']} chunks, embedder `{report['embedder']}`", "",
             "## Retrieval (section-level relevance)", "",
             "| Config | Hit@1 | Hit@5 | Recall@5 | MRR@10 | nDCG@10 | p50 ms |", "|---|---|---|---|---|---|---|"]
    for name, r in report["retrieval"].items():
        a = r["all"]
        lines.append(f"| {name} | {a['hit@1']:.3f} | {a['hit@5']:.3f} | {a['recall@5']:.3f} | {a['mrr@10']:.3f} | "
                     f"{a['ndcg@10']:.3f} | {r['latency_ms']['p50']} |")
    types = [t for t in next(iter(report["retrieval"].values())) if t not in ("all", "latency_ms")]
    for t in types:
        lines += ["", f"### Question type: {t}", "", "| Config | Hit@1 | Hit@5 | MRR@10 | n |", "|---|---|---|---|---|"]
        for name, r in report["retrieval"].items():
            a = r[t]
            lines.append(f"| {name} | {a['hit@1']:.3f} | {a['hit@5']:.3f} | {a['mrr@10']:.3f} | {a['n']} |")
    ab = report["abstention"]
    lines += ["", f"## Retrieval-side abstention gate ({ab['n_unanswerable']} out-of-scope questions)", "",
              "| Gate | Threshold | Out-of-scope refused | In-scope wrongly refused | Balanced acc. |",
              "|---|---|---|---|---|"]
    for label in ("lexical_configured", "lexical_best", "dense_best"):
        if label in ab:
            s = ab[label]
            lines.append(f"| {label.replace('_', ' ')} | {s['threshold']} | {s['unanswerable_refused']:.3f} | "
                         f"{s['answerable_wrongly_refused']:.3f} | {s['balanced_accuracy']:.3f} |")
    if report.get("answers"):
        a = report["answers"]
        lines += ["", f"## Answer quality ({a['model']})", "", "| Metric | Value |", "|---|---|"]
        for k in ("keyword_recall", "citation_precision", "answers_with_correct_citation", "citation_coverage",
                  "false_abstention_rate", "correct_abstention_rate"):
            lines.append(f"| {k} | {a[k]} |")
        lines.append(f"| latency p50 / p95 ms | {a['latency_ms']['p50']} / {a['latency_ms']['p95']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--corpus", type=Path, help="folder of specs to index fresh for this run")
    src.add_argument("--index-dir", type=Path, help="use an existing index (e.g. data/index)")
    ap.add_argument("--dataset", type=Path, default=ROOT / "eval" / "datasets" / "sample_qa.jsonl")
    ap.add_argument("--embed-provider", default=None)
    ap.add_argument("--embed-model", default=None)
    ap.add_argument("--generate", action="store_true", help="also evaluate answers with the configured LLM")
    ap.add_argument("--llm-provider", default=None)
    ap.add_argument("--name", default=None)
    args = ap.parse_args()

    embedder = make_embedder(settings, args.embed_provider, args.embed_model)
    if args.index_dir:
        index = SpecIndex.load(args.index_dir, embedder)
    else:
        index = build_index(args.corpus or ROOT / "data" / "sample", embedder)
    data = load_dataset(args.dataset)

    report = {
        "name": args.name or f"{embedder.name.replace(':', '-')}",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": str(args.dataset.relative_to(ROOT) if args.dataset.is_relative_to(ROOT) else args.dataset),
        "n_questions": len(data),
        "n_chunks": len(index.chunks),
        "embedder": embedder.name,
        "retrieval": {name: eval_retrieval(index, data, cfg) for name, cfg in CONFIGS.items()},
        "abstention": eval_abstention(index, data, settings.abstain_threshold),
    }
    if not args.index_dir:  # chunking ablation needs the raw corpus
        fixed = build_index(args.corpus or ROOT / "data" / "sample", make_embedder(settings, args.embed_provider, args.embed_model), "fixed")
        report["retrieval"]["hybrid + fixed-size chunks"] = eval_retrieval(fixed, data, CONFIGS["hybrid (RRF)"])
    if args.generate:
        pipe = Pipeline(settings, llm=make_llm(settings, args.llm_provider), index=index)
        report["answers"] = eval_answers(pipe, data)

    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{report['name']}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = to_markdown(report)
    (out_dir / f"{report['name']}.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
