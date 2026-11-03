import os
import sys
import tempfile
from pathlib import Path

# Fully offline test configuration: no LLM server, no model downloads.
_TMP = tempfile.mkdtemp(prefix="specrag-test-")
os.environ.update({
    "LLM_PROVIDER": "extractive",
    "EMBED_PROVIDER": "lsa",
    "RERANKER": "none",
    "INDEX_DIR": str(Path(_TMP) / "index"),
})

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from app.ingest.chunker import chunk_document  # noqa: E402
from app.ingest.parser import parse_document  # noqa: E402

SAMPLE = ROOT / "data" / "sample"


@pytest.fixture(scope="session")
def sample_chunks():
    chunks = []
    for f in sorted(SAMPLE.glob("*.md")):
        chunks += chunk_document(parse_document(f))
    return chunks


@pytest.fixture(scope="session")
def sample_index(sample_chunks):
    from app.index import SpecIndex
    from app.retrieval.embeddings import LSAEmbedder

    return SpecIndex(LSAEmbedder()).build(sample_chunks)
