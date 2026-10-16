"""
Ollama embedding client.

Synchronous on purpose: indexing runs on worker threads and queries call it via
asyncio.to_thread, so there is no async client to get bound to a dead event
loop. Failures raise instead of returning zero vectors, so a chunk whose
embedding failed is retried on the next index run rather than stored as noise.
"""

import threading
from collections import OrderedDict
from typing import Optional

from ollama import Client

from backend.config import Settings


class EmbeddingError(RuntimeError):
    pass


class Embedder:
    # nomic-embed-text is trained with task prefixes
    DOC_PREFIX = "search_document: "
    QUERY_PREFIX = "search_query: "

    def __init__(self, host: Optional[str] = None, model: Optional[str] = None):
        self.model = model or Settings.EMBED_MODEL
        self.client = Client(host=host or Settings.OLLAMA_HOST, timeout=300)
        self._query_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self.client.embed(model=self.model, input=texts, truncate=True)
        except Exception as e:
            raise EmbeddingError(f"Ollama embedding failed ({self.model}): {e}") from e
        vectors = [list(v) for v in resp.embeddings]
        if len(vectors) != len(texts) or any(not v for v in vectors):
            raise EmbeddingError("Ollama returned an incomplete embedding batch")
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self.DOC_PREFIX + t for t in texts])

    def embed_query(self, query: str) -> list[float]:
        with self._lock:
            if query in self._query_cache:
                self._query_cache.move_to_end(query)
                return self._query_cache[query]
        vec = self._embed([self.QUERY_PREFIX + query])[0]
        with self._lock:
            self._query_cache[query] = vec
            if len(self._query_cache) > 256:
                self._query_cache.popitem(last=False)
        return vec


_embedder: Optional[Embedder] = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    global _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = Embedder()
        return _embedder


def set_embedder(embedder: Optional[Embedder]):
    """Swap the process-wide embedder (tests)."""
    global _embedder
    with _embedder_lock:
        _embedder = embedder
