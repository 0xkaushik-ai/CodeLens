import hashlib
import os
import subprocess

import numpy as np
import pytest

from backend.db_client import CodeLensDB, set_db
from backend.embedder import EmbeddingError, set_embedder
from backend.text import query_tokens

DIM = 256


class FakeEmbedder:
    """Deterministic bag-of-words embedder: shared words -> similar vectors."""

    model = "fake"

    def __init__(self):
        self.calls = 0
        self.texts_embedded = 0
        self.fail = False

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(DIM, dtype=np.float32)
        for tok in query_tokens(text):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1.0
        if not v.any():
            v[0] = 1.0
        return (v / np.linalg.norm(v)).tolist()

    def embed_documents(self, texts):
        if self.fail:
            raise EmbeddingError("ollama is down")
        self.calls += 1
        self.texts_embedded += len(texts)
        return [self._vec(t) for t in texts]

    def embed_query(self, query):
        if self.fail:
            raise EmbeddingError("ollama is down")
        return self._vec(query)


@pytest.fixture(params=["sqlite-vec", "numpy"])
def db(request, tmp_path):
    database = CodeLensDB(str(tmp_path / "db"), use_vec=request.param == "sqlite-vec")
    if request.param == "sqlite-vec" and not database.use_vec:
        pytest.skip("sqlite-vec cannot be loaded by this Python")
    set_db(database)
    yield database
    set_db(None)
    database.close()


@pytest.fixture
def embedder():
    fake = FakeEmbedder()
    set_embedder(fake)
    yield fake
    set_embedder(None)


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return str(root)


@pytest.fixture
def client(db, embedder, tmp_path, monkeypatch):
    """FastAPI test client with an isolated repo registry."""
    from fastapi.testclient import TestClient
    from backend import main

    monkeypatch.setattr(main, "REPOS_FILE", str(tmp_path / "repos.json"))
    monkeypatch.setattr(main, "LEGACY_SIDECAR", str(tmp_path / "nope.json"))
    monkeypatch.setattr(main, "_indexers", {})
    with TestClient(main.app) as c:
        yield c
    for ix in main._indexers.values():
        ix.stop_watchdog()
