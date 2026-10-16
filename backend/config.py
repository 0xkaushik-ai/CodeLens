import os


def _env(name: str, default: str, *legacy: str) -> str:
    for key in (name, *legacy):
        value = os.getenv(key)
        if value:
            return value
    return default


class Settings:
    # Where the index lives. Outside the extension folder so updates don't wipe it.
    DB_PATH: str = os.path.expanduser(_env("CODELENS_DB_PATH", "~/.codelens", "VECTORAI_DB_PATH"))
    VECTORAI_DB_PATH: str = DB_PATH  # legacy name

    OLLAMA_HOST: str = _env("OLLAMA_HOST", "http://localhost:11434")
    EMBED_MODEL: str = _env("EMBED_MODEL", "nomic-embed-text")
    EXPLAIN_MODEL: str = _env("EXPLAIN_MODEL", "mistral")

    # Characters of a chunk sent to the embedder (nomic-embed-text: 2048 tokens by default)
    EMBED_MAX_CHARS: int = int(_env("EMBED_MAX_CHARS", "6000"))
    EMBED_BATCH_SIZE: int = int(_env("EMBED_BATCH_SIZE", "32"))
    # Files larger than this are skipped (generated code, bundles, fixtures)
    MAX_FILE_BYTES: int = int(_env("MAX_FILE_BYTES", str(512 * 1024)))
    TOP_K: int = int(_env("TOP_K", "10"))
    # Set to 0 to force the numpy vector backend even when sqlite-vec is installed
    USE_SQLITE_VEC: bool = _env("CODELENS_USE_SQLITE_VEC", "1") != "0"
