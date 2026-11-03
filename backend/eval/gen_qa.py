"""Bootstrap an evaluation set from your own indexed specs with the configured LLM.

For each sampled chunk the LLM writes two questions answerable from it: a "lookup"
question that may use the spec's own terms, and a "paraphrase" question phrased the way
an engineer would ask without knowing the field names. The chunk's section becomes the
gold label. Review the output by hand before trusting the numbers.

    python -m eval.gen_qa --n 60 --out eval/datasets/nvme_base_qa.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.generation.testgen import _extract_json  # noqa: E402
from app.index import SpecIndex  # noqa: E402
from app.llm.providers import make_llm  # noqa: E402
from app.retrieval.embeddings import make_embedder  # noqa: E402

PROMPT = """Specification excerpt (section {sid} {title}):

{text}

Write two questions that this excerpt answers on its own:
1. "lookup": a precise question an engineer would ask, may use the excerpt's field names/opcodes.
2. "paraphrase": the same need phrased in plain words, WITHOUT the excerpt's field names, opcodes or section title.
Also give 1-3 short "keywords" (exact strings from the excerpt) that any correct answer must contain.

Return JSON: {{"lookup": "...", "paraphrase": "...", "keywords": ["..."]}}"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index-dir", type=Path, default=settings.index_dir)
    ap.add_argument("--n", type=int, default=50, help="number of chunks to sample")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    index = SpecIndex.load(args.index_dir, make_embedder(settings))
    llm = make_llm(settings)
    # sample substantive prose/table chunks, at most one per section
    by_section = {}
    for c in index.chunks:
        if len(c.text.split()) >= 40 and c.section_id != "0":
            by_section.setdefault((c.doc_id, c.section_id), c)
    random.seed(args.seed)
    sample = random.sample(list(by_section.values()), min(args.n, len(by_section)))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with args.out.open("w", encoding="utf-8") as f:
        for i, c in enumerate(sample, 1):
            try:
                raw = llm.complete("You write evaluation questions. Output only JSON.",
                                   PROMPT.format(sid=c.section_id, title=c.section_title, text=c.text[:2500]),
                                   max_tokens=400, json_mode=True)
                q = _extract_json(raw)
            except Exception as e:  # keep going; a few failures are fine
                print(f"skip {c.id}: {e}")
                continue
            gold = [f"{c.doc_id}:{c.section_id}"]
            for qtype in ("lookup", "paraphrase"):
                if q.get(qtype):
                    f.write(json.dumps({"id": f"g{i:03d}{qtype[0]}", "type": qtype, "question": q[qtype],
                                        "gold": gold, "keywords": q.get("keywords", [])[:3]}) + "\n")
                    written += 1
            print(f"[{i}/{len(sample)}] §{c.section_id} {c.section_title}")
    print(f"wrote {written} questions to {args.out}. Add a few out-of-scope questions with \"gold\": [] "
          "to measure abstention, and review the set by hand.")


if __name__ == "__main__":
    main()
