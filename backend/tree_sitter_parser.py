"""
Tree-sitter multi-language AST chunker (tree-sitter >= 0.22 API)
----------------------------------------------------------------
Turns a source file into semantic chunks (functions, methods, classes, types)
for embedding and keyword indexing.

Rules:
 • Text is sliced from the UTF-8 *bytes* the parser saw, never from the str,
   so files with non-ASCII characters produce correct chunks.
 • Function-like nodes are leaves: we don't descend into their bodies, so
   nested helpers don't duplicate their parent's text.
 • Class-like nodes produce a compact "summary" chunk (header, docstring,
   fields and member signatures) and each member becomes its own chunk,
   named `Class.member`.
 • Python decorators belong to the definition they decorate (one chunk).
 • Oversized chunks are split into overlapping windows of the same symbol.
"""

import os
from tree_sitter import Language, Parser

import tree_sitter_python
import tree_sitter_typescript
import tree_sitter_javascript
import tree_sitter_go
import tree_sitter_rust
import tree_sitter_java

# ---------------------------------------------------------------------------
# Language registry: ext -> (Language, name). Language.name is None for some
# grammars in tree-sitter 0.25, so we carry the name explicitly.
# ---------------------------------------------------------------------------
_LANG_DEFS: dict[str, tuple[Language, str]] = {
    ".py":   (Language(tree_sitter_python.language()),                "python"),
    ".ts":   (Language(tree_sitter_typescript.language_typescript()), "typescript"),
    ".tsx":  (Language(tree_sitter_typescript.language_tsx()),        "tsx"),
    ".js":   (Language(tree_sitter_javascript.language()),            "javascript"),
    ".jsx":  (Language(tree_sitter_javascript.language()),            "javascript"),
    ".mjs":  (Language(tree_sitter_javascript.language()),            "javascript"),
    ".cjs":  (Language(tree_sitter_javascript.language()),            "javascript"),
    ".go":   (Language(tree_sitter_go.language()),                    "go"),
    ".rs":   (Language(tree_sitter_rust.language()),                  "rust"),
    ".java": (Language(tree_sitter_java.language()),                  "java"),
}

# Optional C/C++
try:
    import tree_sitter_cpp
    _cpp = Language(tree_sitter_cpp.language())
    # C is parsed with the C++ grammar: it is close to a superset for chunking purposes
    for _ext in (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".c"):
        _LANG_DEFS[_ext] = (_cpp, "cpp")
    # CUDA kernels: the C++ grammar recovers around __global__/__device__ and <<<...>>>,
    # so functions are still found. Tagged "cuda" so `lang:cuda` can pick them out.
    for _ext in (".cu", ".cuh"):
        _LANG_DEFS[_ext] = (_cpp, "cuda")
except ImportError:
    pass

# Public: extension -> Language (for the file walker)
LANGUAGES: dict[str, Language] = {ext: lang for ext, (lang, _) in _LANG_DEFS.items()}

# Plain-text formats indexed with a sliding window
TEXT_LANGUAGES: dict[str, str] = {
    ".md": "markdown", ".txt": "text", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml",
}

# ---------------------------------------------------------------------------
# Node types per language, split into functions (leaves) and containers
# ---------------------------------------------------------------------------
_TS_FUNCS = {"function_declaration", "generator_function_declaration", "method_definition",
             "arrow_function", "function_expression"}
_TS_CONTAINERS = {"class_declaration", "abstract_class_declaration", "class"}
_TS_TYPES = {"interface_declaration", "type_alias_declaration", "enum_declaration"}

FUNCTION_TYPES: dict[str, set[str]] = {
    "python":     {"function_definition"},
    "typescript": _TS_FUNCS,
    "tsx":        _TS_FUNCS,
    "javascript": _TS_FUNCS,
    "go":         {"function_declaration", "method_declaration"},
    "rust":       {"function_item"},
    "java":       {"method_declaration", "constructor_declaration"},
    "cpp":        {"function_definition"},
    "cuda":       {"function_definition"},
}
CONTAINER_TYPES: dict[str, set[str]] = {
    "python":     {"class_definition"},
    "typescript": _TS_CONTAINERS,
    "tsx":        _TS_CONTAINERS,
    "javascript": _TS_CONTAINERS,
    "go":         set(),
    "rust":       {"impl_item", "trait_item", "mod_item"},
    "java":       {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"},
    "cpp":        {"class_specifier", "struct_specifier"},
    "cuda":       {"class_specifier", "struct_specifier"},
}
TYPE_TYPES: dict[str, set[str]] = {
    "python":     set(),
    "typescript": _TS_TYPES,
    "tsx":        _TS_TYPES,
    "javascript": set(),
    "go":         {"type_declaration"},
    "rust":       {"struct_item", "enum_item"},
    "java":       set(),
    "cpp":        set(),
    "cuda":       set(),
}

# Kept for backwards compatibility with older imports
TARGET_NODE_TYPES: dict[str, set[str]] = {
    lang: FUNCTION_TYPES[lang] | CONTAINER_TYPES[lang] | TYPE_TYPES[lang] for lang in FUNCTION_TYPES
}

# Anonymous functions only count when bound to a name
_NAMED_BINDINGS = {"variable_declarator", "public_field_definition", "field_definition", "pair",
                   "assignment_expression"}

MAX_CHUNK_LINES = 120      # longer chunks are split...
SPLIT_WINDOW = 60          # ...into windows of this many lines
SPLIT_OVERLAP = 10
SUMMARY_MAX_LINES = 40     # cap on the header part of a class summary
_NAME_TYPES = {"identifier", "name", "type_identifier", "property_identifier",
               "field_identifier", "constant", "simple_identifier"}


def language_for(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in _LANG_DEFS:
        return _LANG_DEFS[ext][1]
    return TEXT_LANGUAGES.get(ext)


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _first_name(node, src: bytes, depth: int = 4) -> str | None:
    """Breadth-first search for the first identifier-like descendant."""
    frontier = [node]
    for _ in range(depth):
        nxt = []
        for n in frontier:
            for c in n.children:
                if c.type in _NAME_TYPES or c.type in {"qualified_identifier", "destructor_name",
                                                       "operator_name"}:
                    return _text(c, src)
                nxt.append(c)
        frontier = nxt
    return None


def _symbol_name(node, src: bytes) -> str:
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        if inner is not None:
            return _symbol_name(inner, src)

    if node.type in {"arrow_function", "function_expression", "class"}:
        parent = node.parent
        if parent is not None:
            name = parent.child_by_field_name("name") or parent.child_by_field_name("key") \
                or parent.child_by_field_name("left")
            if name is not None:
                return _text(name, src)

    if node.type == "impl_item":  # Rust: `impl Trait for Type`
        typ = node.child_by_field_name("type")
        trait = node.child_by_field_name("trait")
        if typ is not None:
            return f"impl {_text(trait, src)} for {_text(typ, src)}" if trait is not None \
                else f"impl {_text(typ, src)}"

    if node.type == "function_definition" and node.child_by_field_name("declarator") is not None:
        # C/C++: the name is buried in the declarator chain
        decl = node.child_by_field_name("declarator")
        while decl is not None and decl.child_by_field_name("declarator") is not None:
            decl = decl.child_by_field_name("declarator")
        if decl is not None:
            return _text(decl, src)

    name = node.child_by_field_name("name")
    if name is not None:
        return _text(name, src)
    return _first_name(node, src) or node.type


def _kind(node, lang: str, inside_container: bool) -> str:
    t = node.type
    if t == "decorated_definition":
        inner = node.child_by_field_name("definition")
        t = inner.type if inner is not None else t
    if t in CONTAINER_TYPES.get(lang, ()):
        if t in {"interface_declaration", "trait_item"}:
            return "interface"
        if t == "mod_item":
            return "module"
        if t == "impl_item":
            return "impl"
        if t in {"struct_specifier"}:
            return "struct"
        return "class"
    if t in TYPE_TYPES.get(lang, ()):
        if t == "interface_declaration":
            return "interface"
        if t in {"struct_item"}:
            return "struct"
        return "type"
    if t in FUNCTION_TYPES.get(lang, ()):
        return "method" if inside_container or t in {"method_definition", "method_declaration",
                                                       "constructor_declaration"} else "function"
    return "block"


def _classify(node, lang: str) -> str | None:
    """Return 'function', 'container', 'type' or None for a node."""
    t = node.type
    if t == "decorated_definition":
        inner = node.child_by_field_name("definition")
        if inner is None:
            return None
        return "container" if inner.type in CONTAINER_TYPES[lang] else "function"
    if t in FUNCTION_TYPES[lang]:
        if t in {"arrow_function", "function_expression"}:
            parent = node.parent
            if parent is None or parent.type not in _NAMED_BINDINGS:
                return None
        return "function"
    if t in CONTAINER_TYPES[lang]:
        if t == "class":  # JS class expression: only when bound to a name
            parent = node.parent
            if parent is None or parent.type not in _NAMED_BINDINGS:
                return None
        return "container"
    if t in TYPE_TYPES[lang]:
        return "type"
    return None


def _split_if_large(chunk: dict) -> list[dict]:
    lines = chunk["chunk_text"].split("\n")
    if len(lines) <= MAX_CHUNK_LINES:
        return [chunk]
    parts = []
    step = SPLIT_WINDOW - SPLIT_OVERLAP
    for i in range(0, len(lines), step):
        window = lines[i:i + SPLIT_WINDOW]
        text = "\n".join(window).strip()
        if text:
            parts.append({
                **chunk,
                "line_start": chunk["line_start"] + i,
                "line_end": chunk["line_start"] + i + len(window) - 1,
                "chunk_text": text,
            })
        if i + SPLIT_WINDOW >= len(lines):
            break
    return parts


def extract_chunks(file_path: str, content: str) -> list[dict]:
    """
    Parse `content` with tree-sitter and return semantic chunk dicts with keys:
    file_path, line_start, line_end (1-based, inclusive), symbol_name, kind,
    language, chunk_text. Falls back to a sliding window when no symbols exist.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _LANG_DEFS:
        return sliding_window(file_path, content, TEXT_LANGUAGES.get(ext, "text"))

    language, lang = _LANG_DEFS[ext]
    src = content.encode("utf-8")
    parser = Parser(language)
    tree = parser.parse(src)
    src_lines = src.split(b"\n")

    chunks: list[dict] = []
    # Iterative DFS: (node, enclosing container name or None)
    stack = [(tree.root_node, None)]
    while stack:
        node, container = stack.pop()
        role = _classify(node, lang)

        if role is None:
            stack.extend((c, container) for c in reversed(node.children))
            continue

        name = _symbol_name(node, src)
        qualified = f"{container}.{name}" if container else name
        start, end = node.start_point.row, node.end_point.row
        base = {
            "file_path": file_path,
            "line_start": start + 1,
            "line_end": end + 1,
            "symbol_name": qualified,
            "kind": _kind(node, lang, container is not None),
            "language": lang,
        }

        if role in {"function", "type"}:
            chunks.extend(_split_if_large({**base, "chunk_text": _text(node, src)}))
            continue

        # Container: find direct members (searching through bodies/declaration lists)
        members = []
        frontier = list(node.children)
        while frontier:
            child = frontier.pop(0)
            if _classify(child, lang) is not None:
                members.append(child)
            else:
                frontier.extend(child.children)
        members.sort(key=lambda n: n.start_byte)

        if members:
            header_end = min(members[0].start_point.row, start + SUMMARY_MAX_LINES)
            header = [l.decode("utf-8", errors="replace") for l in src_lines[start:header_end]]
            if not header:  # member on the same line as the class keyword
                header = [src_lines[start].decode("utf-8", errors="replace")]
            signatures = [src_lines[m.start_point.row].decode("utf-8", errors="replace").rstrip()
                          for m in members]
            summary = "\n".join(header + signatures)
        else:
            summary = _text(node, src)
        chunks.extend(_split_if_large({**base, "chunk_text": summary}))

        stack.extend((m, qualified) for m in reversed(members))

    chunks.sort(key=lambda c: (c["line_start"], c["line_end"]))

    if not chunks and content.strip():
        return sliding_window(file_path, content, lang)
    return chunks


def sliding_window(file_path: str, content: str, lang_name: str,
                   window: int = 40, overlap: int = 10) -> list[dict]:
    """Line-based sliding-window chunking for prose, config and symbol-less code."""
    lines = content.splitlines()
    step = max(1, window - overlap)
    base = os.path.basename(file_path)
    kind = "text" if lang_name in TEXT_LANGUAGES.values() else "block"
    chunks = []
    for i in range(0, len(lines), step):
        chunk_lines = lines[i:i + window]
        text = "\n".join(chunk_lines).strip()
        if text:
            # Name markdown windows after their first heading when there is one
            heading = next((l.lstrip("#").strip() for l in chunk_lines if l.startswith("#")), None)
            chunks.append({
                "file_path": file_path,
                "line_start": i + 1,
                "line_end": i + len(chunk_lines),
                "symbol_name": heading or f"{base}:{i + 1}-{i + len(chunk_lines)}",
                "kind": kind,
                "language": lang_name,
                "chunk_text": text,
            })
        if i + window >= len(lines):
            break
    return chunks


# Backwards-compatible alias
_sliding_window = sliding_window
