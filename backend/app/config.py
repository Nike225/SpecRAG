"""Runtime configuration, read from environment variables (and an optional .env file).

Every component is pluggable: swap the LLM, the embedding model or the reranker
without touching code, e.g.

    LLM_PROVIDER=openai OPENAI_API_KEY=... EMBED_PROVIDER=openai uvicorn app.api:app
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    # Storage
    index_dir: Path = field(default_factory=lambda: Path(_env("INDEX_DIR", str(ROOT / "data" / "index"))))

    # Chunking (approximate tokens; 1 token ~= 0.75 words)
    chunk_tokens: int = int(_env("CHUNK_TOKENS", "350"))
    chunk_overlap: int = int(_env("CHUNK_OVERLAP", "60"))

    # Retrieval
    retrieval_mode: str = _env("RETRIEVAL_MODE", "hybrid")  # bm25 | dense | hybrid
    top_k_candidates: int = int(_env("TOP_K_CANDIDATES", "30"))  # per retriever, before fusion
    top_k_context: int = int(_env("TOP_K_CONTEXT", "6"))  # chunks handed to the LLM
    rrf_k: int = int(_env("RRF_K", "60"))
    # Retrieval-side abstention gate ("not in the spec") applied before calling the LLM.
    # Lexical: best BM25 score normalised by the query's max attainable score (unknown terms
    # count at max IDF). Dense: best cosine similarity (0 disables; tune per embedding model
    # with eval/run_eval.py). The LLM's own INSUFFICIENT_CONTEXT reply is the second gate.
    abstain_threshold: float = float(_env("ABSTAIN_THRESHOLD", "0.08"))
    abstain_dense_threshold: float = float(_env("ABSTAIN_DENSE_THRESHOLD", "0"))

    # Embeddings: lsa (offline, no model download) | ollama | sentence-transformers | openai
    embed_provider: str = _env("EMBED_PROVIDER", "ollama")
    embed_model: str = _env("EMBED_MODEL", "nomic-embed-text")

    # Reranker: none | cross-encoder | llm
    reranker: str = _env("RERANKER", "none")
    rerank_model: str = _env("RERANK_MODEL", "BAAI/bge-reranker-base")

    # LLM: ollama | openai | anthropic | extractive (offline, no LLM)
    llm_provider: str = _env("LLM_PROVIDER", "ollama")
    llm_model: str = _env("LLM_MODEL", "")
    llm_temperature: float = float(_env("LLM_TEMPERATURE", "0.1"))
    llm_timeout_s: float = float(_env("LLM_TIMEOUT_S", "120"))

    ollama_url: str = _env("OLLAMA_URL", "http://localhost:11434")
    openai_api_key: str = _env("OPENAI_API_KEY", "")
    openai_base_url: str = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY", "")
    anthropic_base_url: str = _env("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

    cors_origins: str = _env("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")

    def default_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return {
            "ollama": "llama3.1:8b",
            "openai": "gpt-4o-mini",
            "anthropic": "claude-sonnet-5-5",
        }.get(self.llm_provider, "")


settings = Settings()
