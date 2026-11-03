# SpecRAG evaluation - ollama-nomic-embed-text

- Generated: 2026-11-02T20:00:35+00:00
- Dataset: `eval\datasets\nvme_qa.jsonl` (128 questions)
- Index: 2755 chunks, embedder `ollama:nomic-embed-text`

## Retrieval (section-level relevance)

| Config | Hit@1 | Hit@5 | Recall@5 | MRR@10 | nDCG@10 | p50 ms |
|---|---|---|---|---|---|---|
| bm25 | 0.617 | 0.892 | 0.892 | 0.743 | 0.794 | 0.42 |
| bm25+expansion | 0.608 | 0.892 | 0.892 | 0.739 | 0.791 | 0.43 |
| dense | 0.667 | 0.867 | 0.867 | 0.757 | 0.802 | 2298.79 |
| hybrid (RRF) | 0.725 | 0.917 | 0.917 | 0.812 | 0.848 | 3078.61 |

### Question type: lookup

| Config | Hit@1 | Hit@5 | MRR@10 | n |
|---|---|---|---|---|
| bm25 | 0.750 | 1.000 | 0.854 | 60 |
| bm25+expansion | 0.733 | 1.000 | 0.847 | 60 |
| dense | 0.767 | 0.950 | 0.840 | 60 |
| hybrid (RRF) | 0.817 | 0.967 | 0.887 | 60 |

### Question type: paraphrase

| Config | Hit@1 | Hit@5 | MRR@10 | n |
|---|---|---|---|---|
| bm25 | 0.483 | 0.783 | 0.631 | 60 |
| bm25+expansion | 0.483 | 0.783 | 0.631 | 60 |
| dense | 0.567 | 0.783 | 0.674 | 60 |
| hybrid (RRF) | 0.633 | 0.867 | 0.737 | 60 |

## Retrieval-side abstention gate (8 out-of-scope questions)

| Gate | Threshold | Out-of-scope refused | In-scope wrongly refused | Balanced acc. |
|---|---|---|---|---|
| lexical configured | 0.08 | 0.000 | 0.000 | 0.500 |
| lexical best | 0.26 | 1.000 | 0.075 | 0.963 |
| dense best | 0.72 | 1.000 | 0.042 | 0.979 |

## Answer quality (ollama:llama3.1:8b)

| Metric | Value |
|---|---|
| keyword_recall | 0.7486 |
| citation_precision | 0.6792 |
| answers_with_correct_citation | 0.9 |
| citation_coverage | 0.738 |
| false_abstention_rate | 0.0 |
| correct_abstention_rate | 0.25 |
| latency p50 / p95 ms | 9391.4 / 13802.1 |
