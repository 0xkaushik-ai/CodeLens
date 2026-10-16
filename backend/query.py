"""
Query engine: hybrid search with filters.

    "retry logic in:backend -in:tests lang:py kind:function"

1. Filters (`in:`, `-in:`, `lang:`, `kind:`) are split from the text and become
   one SQL WHERE clause, applied to both searches below.
2. Semantic search: the text is embedded and compared to every chunk vector.
3. Keyword search: BM25 over symbol names, identifier sub-words, paths and code.
4. Reciprocal-rank fusion merges the two lists; an exact symbol-name match is
   promoted to the top. Overlapping windows of the same text are collapsed.

Either search alone still returns results: if Ollama is down, keyword search
answers and the response carries a warning.
"""

import re
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ollama import Client

from backend.config import Settings
from backend.db_client import get_db
from backend.embedder import EmbeddingError, get_embedder
from backend.text import fts_match_expression, query_tokens

logger = logging.getLogger(__name__)

LANG_ALIASES: dict[str, list[str]] = {
    "py": ["python"], "python": ["python"],
    "ts": ["typescript", "tsx"], "typescript": ["typescript", "tsx"], "tsx": ["tsx"],
    "js": ["javascript"], "javascript": ["javascript"], "jsx": ["javascript"],
    "go": ["go"], "golang": ["go"],
    "rs": ["rust"], "rust": ["rust"],
    "java": ["java"],
    "c": ["cpp"], "cc": ["cpp"], "cpp": ["cpp"], "c++": ["cpp"], "h": ["cpp"],
    "md": ["markdown"], "markdown": ["markdown"], "docs": ["markdown", "text"],
    "yaml": ["yaml"], "yml": ["yaml"], "toml": ["toml"], "txt": ["text"], "text": ["text"],
}

KIND_ALIASES: dict[str, list[str]] = {
    "function": ["function"], "fn": ["function"], "func": ["function"], "def": ["function"],
    "method": ["method"],
    "class": ["class"], "interface": ["interface"], "trait": ["interface"],
    "type": ["type", "struct", "interface"], "struct": ["struct"], "enum": ["type"],
    "impl": ["impl"], "module": ["module"],
    "text": ["text"], "doc": ["text"], "docs": ["text"],
    "block": ["block"],
}

_FILTER = re.compile(r'(?:(?<=\s)|^)(-?)(in|lang|kind):(?:"([^"]*)"?|(\S*))', re.IGNORECASE)

RRF_K = 60
EXACT_SYMBOL_BONUS = 0.05     # beats a first place in both lists (2/61 ≈ 0.033)
PARTIAL_SYMBOL_BONUS = 0.01


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass
class ParsedQuery:
    text: str
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)

    @property
    def has_filters(self) -> bool:
        return bool(self.include_paths or self.exclude_paths or self.languages or self.kinds)

    def sql(self) -> tuple[str, list]:
        """WHERE clause over the `chunks c` alias."""
        clauses, params = [], []
        path_expr = "('/' || lower(c.file_path)) LIKE ? ESCAPE '\\'"
        if self.include_paths:
            clauses.append("(" + " OR ".join([path_expr] * len(self.include_paths)) + ")")
            params += [f"%/{_like_escape(p.lower())}%" for p in self.include_paths]
        for p in self.exclude_paths:
            clauses.append("NOT " + path_expr)
            params.append(f"%/{_like_escape(p.lower())}%")
        if self.languages:
            clauses.append(f"c.language IN ({','.join('?' * len(self.languages))})")
            params += self.languages
        if self.kinds:
            clauses.append(f"c.kind IN ({','.join('?' * len(self.kinds))})")
            params += self.kinds
        return (" AND ".join(clauses) or "1"), params

    def describe(self) -> dict[str, list[str]]:
        return {"in": self.include_paths, "not_in": self.exclude_paths,
                "lang": self.languages, "kind": self.kinds}


def parse_query(raw: str) -> ParsedQuery:
    """
    Split filters from text. A filter whose value is empty or unknown (still
    being typed, e.g. `lang:pyth`) is dropped rather than searched as text, so
    results don't empty out mid-keystroke. Unknown keys (`http:`, `12:30`) stay text.
    """
    pq = ParsedQuery(text="")

    def take(m: re.Match) -> str:
        neg, key, quoted, bare = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        value = (quoted if quoted is not None else bare or "").strip().strip("/")
        if not value:
            return " "
        if key == "in":
            (pq.exclude_paths if neg else pq.include_paths).append(value)
        elif key == "lang":
            for lang in LANG_ALIASES.get(value.lower(), []):
                if lang not in pq.languages:
                    pq.languages.append(lang)
        elif key == "kind":
            for kind in KIND_ALIASES.get(value.lower(), []):
                if kind not in pq.kinds:
                    pq.kinds.append(kind)
        return " "

    pq.text = re.sub(r"\s+", " ", _FILTER.sub(take, raw)).strip()
    return pq


def _overlap(a: dict, b: dict) -> float:
    lo, hi = max(a["line_start"], b["line_start"]), min(a["line_end"], b["line_end"])
    if hi < lo:
        return 0.0
    shorter = min(a["line_end"] - a["line_start"], b["line_end"] - b["line_start"]) + 1
    return (hi - lo + 1) / max(1, shorter)


def _collapse(results: list[dict]) -> list[dict]:
    """Drop repeats: parts of the same symbol, and overlapping text windows."""
    kept: list[dict] = []
    seen_symbols: set[tuple[str, str]] = set()
    for r in results:
        key = (r["file_path"], r["symbol_name"])
        if key in seen_symbols:
            continue
        if r["kind"] in {"text", "block"} and any(
                k["file_path"] == r["file_path"] and k["kind"] in {"text", "block"}
                and _overlap(k, r) > 0.5 for k in kept):
            continue
        seen_symbols.add(key)
        kept.append(r)
    return kept


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def search(query: str, repo: Optional[str] = None, top_k: int = 10,
           live: bool = False) -> dict[str, Any]:
    """Synchronous hybrid search. `live` treats the last word as a prefix (search-as-you-type)."""
    db = get_db()
    pq = parse_query(query)
    where, params = pq.sql()
    text = pq.text
    warnings: list[str] = []

    if not text:
        if not pq.has_filters:
            return {"results": [], "filters": pq.describe(), "terms": [], "warnings": []}
        ids = db.list_chunks(where, params, repo, k=top_k)
        rows = db.get_chunks(ids)
        results = [{**rows[i], "score": None, "match": "filter"} for i in ids if i in rows]
        return {"results": results, "filters": pq.describe(), "terms": [], "warnings": []}

    candidates = max(40, top_k * 5)

    # Semantic
    qvec = None
    vector_hits: list[tuple[int, float]] = []
    try:
        qvec = get_embedder().embed_query(text)
        vector_hits = db.vector_search(qvec, where, params, repo, k=candidates)
    except EmbeddingError as e:
        warnings.append(f"Semantic search unavailable, showing keyword matches only: {e}")

    # Keyword
    match = fts_match_expression(text, prefix_last=live)
    keyword_hits = db.keyword_search(match, where, params, repo, k=candidates) if match else []

    # Reciprocal-rank fusion
    fused: dict[int, float] = {}
    sources: dict[int, set[str]] = {}
    for source, hits in (("semantic", vector_hits), ("keyword", keyword_hits)):
        for rank, (cid, _) in enumerate(hits):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            sources.setdefault(cid, set()).add(source)

    rows = db.get_chunks(list(fused))
    compact_query = _compact(text)
    # Only a name-like query (one or two words) boosts symbols matching one of its words;
    # in a sentence, an ordinary word that happens to be a helper's name must not win
    tokens = {t for t in query_tokens(text) if len(t) >= 4} if len(text.split()) <= 2 else set()
    for cid, row in rows.items():
        sym = _compact((row["symbol_name"] or "").split(".")[-1])
        if sym and sym == compact_query:
            fused[cid] += EXACT_SYMBOL_BONUS
        elif sym and sym in tokens:
            fused[cid] += PARTIAL_SYMBOL_BONUS

    similarity = dict(vector_hits)
    if qvec is not None:
        keyword_only = [cid for cid in fused if cid not in similarity]
        similarity.update(db.vector_scores(qvec, keyword_only))

    ordered = sorted((cid for cid in fused if cid in rows), key=lambda c: -fused[c])
    results = []
    for cid in ordered:
        src = sources[cid]
        sim = similarity.get(cid)
        results.append({
            **rows[cid],
            "score": None if sim is None else max(0.0, min(1.0, sim)),
            "rank_score": round(fused[cid], 5),
            "match": "both" if len(src) == 2 else next(iter(src)),
        })
    results = _collapse(results)[:top_k]
    return {"results": results, "filters": pq.describe(), "terms": query_tokens(text),
            "warnings": warnings}


def explain(query: str, results: list[dict]) -> str:
    context = "\n\n".join(
        f"--- {r['file_path']}:{r['line_start']}-{r['line_end']} ({r['symbol_name']})\n{r['chunk_text'][:2000]}"
        for r in results[:3])
    messages = [
        {"role": "system", "content": "You are a code navigation assistant. Answer in 2-3 sentences. "
                                      "Reference specific function names and files. Plain text only."},
        {"role": "user", "content": f"Codebase context:\n{context}\n\nQuestion: {query}"},
    ]
    try:
        resp = Client(host=Settings.OLLAMA_HOST, timeout=120).chat(
            model=Settings.EXPLAIN_MODEL, messages=messages)
        return resp.message.content
    except Exception as e:
        logger.warning(f"Explain failed: {e}")
        return (f"Explain mode unavailable. Is the model pulled? "
                f"Run: ollama pull {Settings.EXPLAIN_MODEL}")


def search_with_explain(query: str, repo: Optional[str] = None, top_k: int = 10,
                        explain_mode: bool = False, live: bool = False) -> dict[str, Any]:
    start = time.time()
    data = search(query, repo=repo, top_k=top_k, live=live)
    data["explain_text"] = explain(parse_query(query).text or query, data["results"]) \
        if explain_mode and data["results"] else None
    data["query_ms"] = int((time.time() - start) * 1000)
    return data


async def run_query(query: str, top_k: int = 10, explain: bool = False,
                    repo: Optional[str] = None, live: bool = False) -> dict[str, Any]:
    return await asyncio.to_thread(search_with_explain, query, repo, top_k, explain, live)


def run_query_sync(query: str, top_k: int = 10, explain: bool = False,
                   repo: Optional[str] = None) -> dict[str, Any]:
    return search_with_explain(query, repo, top_k, explain)
