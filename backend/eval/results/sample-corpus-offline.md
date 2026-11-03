# SpecRAG evaluation - sample-corpus-offline

- Generated: 2026-11-02T18:23:26+00:00
- Dataset: `eval/datasets/sample_qa.jsonl` (53 questions)
- Index: 63 chunks, embedder `lsa:192`

## Retrieval (section-level relevance)

| Config | Hit@1 | Hit@5 | Recall@5 | MRR@10 | nDCG@10 | p50 ms |
|---|---|---|---|---|---|---|
| bm25 | 0.667 | 0.889 | 0.844 | 0.759 | 0.781 | 0.16 |
| bm25+expansion | 0.667 | 0.889 | 0.844 | 0.759 | 0.781 | 0.18 |
| dense | 0.600 | 0.889 | 0.833 | 0.727 | 0.764 | 1.0 |
| hybrid (RRF) | 0.644 | 0.889 | 0.844 | 0.754 | 0.775 | 1.04 |
| hybrid + fixed-size chunks | 0.222 | 0.689 | 0.567 | 0.415 | 0.429 | 0.89 |

### Question type: lookup

| Config | Hit@1 | Hit@5 | MRR@10 | n |
|---|---|---|---|---|
| bm25 | 0.920 | 1.000 | 0.948 | 25 |
| bm25+expansion | 0.920 | 1.000 | 0.948 | 25 |
| dense | 0.800 | 1.000 | 0.887 | 25 |
| hybrid (RRF) | 0.880 | 1.000 | 0.933 | 25 |
| hybrid + fixed-size chunks | 0.160 | 0.800 | 0.443 | 25 |

### Question type: paraphrase

| Config | Hit@1 | Hit@5 | MRR@10 | n |
|---|---|---|---|---|
| bm25 | 0.350 | 0.750 | 0.522 | 20 |
| bm25+expansion | 0.350 | 0.750 | 0.522 | 20 |
| dense | 0.350 | 0.750 | 0.528 | 20 |
| hybrid (RRF) | 0.350 | 0.750 | 0.530 | 20 |
| hybrid + fixed-size chunks | 0.300 | 0.550 | 0.379 | 20 |

## Retrieval-side abstention gate (8 out-of-scope questions)

| Gate | Threshold | Out-of-scope refused | In-scope wrongly refused | Balanced acc. |
|---|---|---|---|---|
| lexical configured | 0.08 | 0.250 | 0.000 | 0.625 |
| lexical best | 0.27 | 1.000 | 0.422 | 0.789 |
| dense best | 0.74 | 1.000 | 0.467 | 0.767 |

## Answer quality (extractive)

| Metric | Value |
|---|---|
| keyword_recall | 0.7222 |
| citation_precision | 0.5556 |
| answers_with_correct_citation | 0.8444 |
| citation_coverage | 0.6519 |
| false_abstention_rate | 0.0 |
| correct_abstention_rate | 0.25 |
| latency p50 / p95 ms | 1.4 / 1.7 |
