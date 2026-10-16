"""Identifier splitting and full-text query building shared by indexer and search."""

import re

_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_WORD = re.compile(r"[A-Za-z0-9_$]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

# Words that carry no meaning in a code search query ("where is the ... handled")
STOPWORDS = frozenset("""
a an and are as at be by can do does for from how i in into is it its me my of on or our
show that the their them there these this to was we what when where which who why will with
you your all any find get code function functions method methods file files handle handles
""".split())

MAX_TERMS = 400


def split_identifier(ident: str) -> list[str]:
    """`parseHTTPConfig_v2` -> ['parse', 'http', 'config', 'v', '2']"""
    parts: list[str] = []
    for piece in ident.replace("$", "_").split("_"):
        parts.extend(p.lower() for p in _CAMEL.findall(piece))
    return parts


def index_terms(*texts: str) -> str:
    """
    Space-joined sub-words of compound identifiers (camelCase, snake_case) so
    that a query for "parse config" finds `parseConfig`. The FTS tokenizer
    already indexes the whole identifier and splits on underscores.
    """
    seen: dict[str, None] = {}
    for text in texts:
        for ident in _IDENT.findall(text):
            parts = split_identifier(ident)
            if len(parts) > 1:
                for p in parts:
                    if len(p) > 1 and p not in seen:
                        seen[p] = None
                        if len(seen) >= MAX_TERMS:
                            return " ".join(seen)
    return " ".join(seen)


def query_tokens(text: str) -> list[str]:
    """Lower-cased search tokens of a query, with stopwords removed and compounds split."""
    tokens: dict[str, None] = {}
    for word in _WORD.findall(text):
        lw = word.lower()
        if lw in STOPWORDS:
            continue
        tokens[lw] = None
        parts = split_identifier(word)
        if len(parts) > 1:
            for p in parts:
                if len(p) > 1 and p not in STOPWORDS:
                    tokens[p] = None
    return list(tokens)


def fts_match_expression(text: str, prefix_last: bool = False) -> str | None:
    """
    Build an FTS5 MATCH expression. Every token is quoted so user input is
    always literal (`c++`, `NOT`, unbalanced quotes are fine). Tokens are ORed
    and BM25 rewards chunks that match more of them.
    """
    tokens = query_tokens(text)
    if not tokens:
        return None
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    # While typing, the last word is probably incomplete: match it as a prefix
    last_word = _WORD.findall(text)[-1].lower() if _WORD.findall(text) else ""
    if prefix_last and last_word and len(last_word) >= 2 and last_word in tokens:
        i = tokens.index(last_word)
        quoted[i] = quoted[i] + "*"
    return " OR ".join(quoted)
