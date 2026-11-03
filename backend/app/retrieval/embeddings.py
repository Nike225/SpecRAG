"""Pluggable embedding providers.

  ollama                local, free (default: nomic-embed-text)
  sentence-transformers local, free (e.g. BAAI/bge-small-en-v1.5)
  openai                hosted (text-embedding-3-small)
  lsa                   TF-IDF + truncated SVD; fully offline, no model download. Used in
                        tests/CI and as a baseline in the ablation.

All providers return L2-normalised float32 vectors so cosine similarity is a dot product.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import httpx
import numpy as np

from app.config import Settings
from app.retrieval.text import normalize, tokenize


def _l2(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x / np.clip(np.linalg.norm(x, axis=1, keepdims=True), 1e-9, None)


class Embedder:
    name = "base"
    needs_fit = False

    def fit(self, texts: list[str]) -> None:  # only for corpus-fitted embedders
        pass

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]

    def save(self, path: Path) -> None:
        pass

    def load(self, path: Path) -> None:
        pass


class OllamaEmbedder(Embedder):
    def __init__(self, model: str, url: str, timeout: float = 120):
        self.name = f"ollama:{model}"
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout
        # nomic-embed-text was trained with task prefixes; they matter for retrieval quality
        self.doc_prefix = "search_document: " if "nomic" in model else ""
        self.query_prefix = "search_query: " if "nomic" in model else ""

    BATCH = 8  # small batches: on a CPU-only machine a batch of 32 long chunks can exceed the timeout
    MAX_CHARS = 6000  # ~1500 tokens; keeps every input inside the model's default context window

    def _post(self, client: httpx.Client, batch: list[str]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = client.post(f"{self.url}/api/embed",
                                json={"model": self.model, "input": batch, "truncate": True, "keep_alive": "30m"})
                if r.status_code == 404:
                    raise RuntimeError(f"Ollama model '{self.model}' not found. Run: ollama pull {self.model}")
                r.raise_for_status()
                return r.json()["embeddings"]
            except httpx.ConnectError as e:
                raise RuntimeError(f"Cannot reach Ollama at {self.url}. Is the Ollama app running?") from e
            except (httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                last = e
                if len(batch) > 1:  # retry slow batches one text at a time
                    return [v for t in batch for v in self._post(client, [t])]
        raise RuntimeError("Ollama timed out 3 times embedding one chunk. Check `ollama ps` and system load.") from last

    def _embed(self, texts: list[str]) -> np.ndarray:
        texts = [t[: self.MAX_CHARS] for t in texts]
        show = len(texts) > 50
        out: list[list[float]] = []
        # generous read timeout: the first request also loads the model into memory
        timeout = httpx.Timeout(connect=10, read=max(self.timeout, 600), write=60, pool=10)
        with httpx.Client(timeout=timeout) as client:
            for i in range(0, len(texts), self.BATCH):
                out.extend(self._post(client, texts[i : i + self.BATCH]))
                if show:
                    done = min(i + self.BATCH, len(texts))
                    print(f"\r  embedding {done}/{len(texts)} chunks", end="", flush=True)
        if show:
            print()
        return _l2(np.asarray(out))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed([self.doc_prefix + t for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([self.query_prefix + text])[0]


class OpenAIEmbedder(Embedder):
    def __init__(self, model: str, api_key: str, base_url: str, timeout: float = 120):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for EMBED_PROVIDER=openai")
        self.name = f"openai:{model}"
        self.model, self.api_key, self.base_url, self.timeout = model, api_key, base_url.rstrip("/"), timeout

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        out = []
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            for i in range(0, len(texts), 128):
                r = client.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": texts[i : i + 128]})
                r.raise_for_status()
                out.extend(d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"]))
        return _l2(np.asarray(out))


class SentenceTransformerEmbedder(Embedder):
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.name = f"st:{model}"
        self.model = SentenceTransformer(model)
        self.query_prefix = "Represent this sentence for searching relevant passages: " if "bge" in model else ""

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return _l2(self.model.encode(texts, batch_size=32, normalize_embeddings=True))

    def embed_query(self, text: str) -> np.ndarray:
        return _l2(self.model.encode([self.query_prefix + text], normalize_embeddings=True))[0]


class LSAEmbedder(Embedder):
    """Latent Semantic Analysis: captures co-occurrence ("wipe" ~ "sanitize" ~ "erase")
    without any pretrained model. Weaker than neural embeddings, but deterministic and
    offline, which makes it ideal for tests and as an ablation baseline."""

    needs_fit = True

    def __init__(self, dim: int = 192):
        self.name = f"lsa:{dim}"
        self.dim = dim
        self.vectorizer = None
        self.svd = None

    def fit(self, texts: list[str]) -> None:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.vectorizer = TfidfVectorizer(
            tokenizer=tokenize, preprocessor=normalize, lowercase=False, token_pattern=None,
            ngram_range=(1, 2), sublinear_tf=True, min_df=1,
        )
        tfidf = self.vectorizer.fit_transform(texts)
        k = max(2, min(self.dim, tfidf.shape[0] - 1, tfidf.shape[1] - 1))
        self.svd = TruncatedSVD(n_components=k, random_state=0).fit(tfidf)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if self.vectorizer is None:
            raise RuntimeError("LSAEmbedder must be fit before use")
        return _l2(self.svd.transform(self.vectorizer.transform(texts)))

    def save(self, path: Path) -> None:
        path.write_bytes(pickle.dumps((self.vectorizer, self.svd)))

    def load(self, path: Path) -> None:
        self.vectorizer, self.svd = pickle.loads(path.read_bytes())


def make_embedder(s: Settings, provider: str | None = None, model: str | None = None) -> Embedder:
    if model is None and (provider is None or provider == s.embed_provider):
        model = s.embed_model  # the configured model belongs to the configured provider
    provider = provider or s.embed_provider
    if provider == "ollama":
        return OllamaEmbedder(model or "nomic-embed-text", s.ollama_url, s.llm_timeout_s)
    if provider == "openai":
        return OpenAIEmbedder(model or "text-embedding-3-small", s.openai_api_key, s.openai_base_url)
    if provider == "sentence-transformers":
        return SentenceTransformerEmbedder(model or "BAAI/bge-small-en-v1.5")
    if provider == "lsa":
        return LSAEmbedder()
    raise ValueError(f"Unknown EMBED_PROVIDER: {provider}")
