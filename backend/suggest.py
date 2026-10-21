"""
Search suggestions built from what is actually in the index.

The sidebar used to show fixed examples ("where do we handle authentication?"),
which make no sense in, say, a repo of CUDA kernels. Instead we look at the
indexed symbols of the current repo and build a few queries from them:

  1. a plain-language phrase made from a characteristic function name
     ("tiled matmul kernel"), which shows off meaning-based search,
  2. a common domain word with a `kind:` filter,
  3. a filter-only query over the biggest folder or language.

No LLM involved: it is a single SQL scan plus word counting, so it is instant
and works without Ollama.
"""

from collections import Counter
from typing import Any, Optional

from backend.db_client import CodeLensDB, get_db
from backend.text import STOPWORDS, split_identifier

CODE_KINDS = {"function", "method", "class", "interface", "type", "struct", "impl", "module"}
TEXT_LANGS = {"markdown", "text", "yaml", "toml"}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing"}

# Short names accepted by `lang:` (see query.LANG_ALIASES)
LANG_FILTER = {
    "python": "py", "typescript": "ts", "tsx": "tsx", "javascript": "js", "go": "go",
    "rust": "rs", "java": "java", "cpp": "cpp", "cuda": "cuda", "markdown": "md",
    "yaml": "yaml", "toml": "toml", "text": "txt",
}

# Words that appear in names everywhere and say nothing about this repo
NOISE = STOPWORDS | frozenset("""
get set init new main run self this cls test tests setup teardown impl func fn helper helpers
util utils wrapper inner tmp temp foo bar baz args kwargs val value values obj item items data
str int bool list dict map arr len num count idx index key keys res result results ret out input
output default base type types to from on is has make create update do call build
""".split())

SCAN_LIMIT = 50_000


def _is_test_path(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    return any(p in TEST_DIRS for p in parts[:-1]) or name.startswith("test_") \
        or ".test." in name or ".spec." in name or name.endswith("_test.go")


def _words(symbol: str) -> list[str]:
    """Meaningful sub-words of the last part of a (possibly qualified) symbol."""
    leaf = symbol.split(".")[-1]
    return [w for w in split_identifier(leaf) if len(w) >= 3 and not w.isdigit() and w not in NOISE]


def suggest(repo: Optional[str], db: Optional[CodeLensDB] = None, n: int = 3) -> dict[str, Any]:
    """Suggested queries, plus the languages and kinds present (for the filter hints)."""
    db = db or get_db()
    empty = {"suggestions": [], "languages": [], "kinds": []}
    if not repo:
        return empty
    rows = db._conn().execute(
        "SELECT file_path, symbol_name, kind, language FROM chunks WHERE repo = ? LIMIT ?",
        (repo, SCAN_LIMIT)).fetchall()
    if not rows:
        return empty

    languages: Counter = Counter()
    kinds: Counter = Counter()
    folders: Counter = Counter()
    term_symbols: dict[str, set[str]] = {}
    symbols: dict[str, tuple[str, list[str]]] = {}   # symbol -> (kind, words)
    has_tests = False

    for path, symbol, kind, lang in rows:
        kinds[kind] += 1
        test = _is_test_path(path)
        has_tests = has_tests or test
        if lang not in TEXT_LANGS:
            languages[lang] += 1
        if "/" in path and not test:
            folders[path.split("/")[0]] += 1
        if test or kind not in CODE_KINDS or not symbol:
            continue
        words = _words(symbol)
        if not words:
            continue
        symbols.setdefault(symbol, (kind, words))
        for w in set(words):
            term_symbols.setdefault(w, set()).add(symbol)

    # A term is characteristic if many different symbols use it
    terms = sorted(term_symbols, key=lambda t: (-len(term_symbols[t]), t))
    suggestions: list[str] = []

    def add(q: str):
        if q and q not in suggestions and len(suggestions) < n:
            suggestions.append(q)

    # 1. A function or class whose name covers the most top terms, written as words
    top = set(terms[:8])
    if symbols:
        best = max(symbols, key=lambda s: (
            sum(w in top for w in symbols[s][1]),        # uses characteristic words
            min(len(symbols[s][1]), 4),                   # several words read like a phrase
            symbols[s][0] in {"function", "method"},
            -len(s)))
        words = symbols[best][1]
        if len(words) >= 2:
            add(" ".join(words[:5]))
        elif terms:
            add(" ".join(dict.fromkeys(words + terms[:2]))[:60])

    # 2. A frequent word, narrowed to the most common code kind
    code_kinds = [k for k, _ in kinds.most_common() if k in CODE_KINDS]
    used = set(" ".join(suggestions).split())
    for t in terms:
        if t not in used:
            add(f"{t} kind:{code_kinds[0]}" if code_kinds else t)
            break

    # 3. Filters alone (classes in the main folder), else a second phrase from
    #    another part of the code, else the main language
    folder = folders.most_common(1)[0][0] if folders else None
    used = set(" ".join(suggestions).split())
    others = [s for s in symbols if len(symbols[s][1]) >= 2 and not used & set(symbols[s][1])]
    if kinds.get("class") and folder:
        add(f"kind:class in:{folder}")
    elif others:
        pick = max(others, key=lambda s: (sum(w in top for w in symbols[s][1]), -len(s)))
        add(" ".join(symbols[pick][1][:5]))
    elif has_tests and terms:
        add(f"{terms[0]} -in:tests")
    elif languages:
        lang = LANG_FILTER.get(languages.most_common(1)[0][0])
        if lang:
            add(f"kind:function lang:{lang}")

    # Docs-only repos: fall back to words from file names
    if not suggestions:
        stems = Counter(w for path, *_ in rows
                        for w in split_identifier(path.rsplit("/", 1)[-1].rsplit(".", 1)[0])
                        if len(w) >= 3 and w not in NOISE)
        for w, _ in stems.most_common(n):
            add(w)

    return {
        "suggestions": suggestions,
        "languages": [LANG_FILTER.get(l, l) for l, _ in languages.most_common()],
        "kinds": [k for k, _ in kinds.most_common()],
    }
