from backend.tree_sitter_parser import extract_chunks, sliding_window, MAX_CHUNK_LINES


def by_name(chunks):
    return {c["symbol_name"]: c for c in chunks}


def test_non_ascii_text_is_sliced_correctly():
    src = "# héllo wörld 🚀🚀🚀\ndef foo():\n    return 1\n\ndef bar():\n    return 2\n"
    chunks = by_name(extract_chunks("x.py", src))
    assert chunks["foo"]["chunk_text"] == "def foo():\n    return 1"
    assert chunks["bar"]["chunk_text"] == "def bar():\n    return 2"
    assert (chunks["foo"]["line_start"], chunks["foo"]["line_end"]) == (2, 3)


def test_decorated_function_is_one_chunk_including_decorator():
    src = '@app.get("/")\ndef route():\n    pass\n'
    chunks = extract_chunks("y.py", src)
    assert len(chunks) == 1
    assert chunks[0]["symbol_name"] == "route"
    assert chunks[0]["chunk_text"].startswith('@app.get("/")')
    assert chunks[0]["line_start"] == 1


def test_class_gets_summary_and_member_chunks():
    src = (
        "class Repo:\n"
        '    """Stores things."""\n'
        "    limit = 3\n"
        "\n"
        "    def save(self, item):\n"
        "        return item\n"
        "\n"
        "    @property\n"
        "    def size(self):\n"
        "        def helper():\n"
        "            return 0\n"
        "        return helper()\n"
    )
    chunks = by_name(extract_chunks("r.py", src))
    assert set(chunks) == {"Repo", "Repo.save", "Repo.size"}  # nested helper not duplicated
    summary = chunks["Repo"]["chunk_text"]
    assert "Stores things" in summary and "def save(self, item):" in summary
    assert "return item" not in summary
    assert chunks["Repo"]["kind"] == "class"
    assert chunks["Repo.save"]["kind"] == "method"
    assert chunks["Repo.size"]["line_start"] == 8


def test_typescript_symbols():
    src = (
        "export interface User { id: string }\n"
        "export const fetchUser = async (id: string) => {\n  return id;\n};\n"
        "export class Api {\n  get(path: string) { return path; }\n}\n"
        "const xs = [1, 2].map((x) => x * 2);\n"
    )
    names = by_name(extract_chunks("a.ts", src))
    assert {"User", "fetchUser", "Api", "Api.get"} <= set(names)
    assert names["fetchUser"]["kind"] == "function"
    assert names["User"]["kind"] == "interface"
    assert not any(n.startswith("arrow") for n in names)  # anonymous callback skipped


def test_go_and_rust_names():
    go = "package main\n\ntype Server struct{}\n\nfunc (s *Server) Start() error {\n\treturn nil\n}\n"
    assert {"Server", "Start"} <= set(by_name(extract_chunks("m.go", go)))
    rs = "struct P;\nimpl Display for P {\n    fn fmt(&self) -> u8 { 1 }\n}\n"
    names = set(by_name(extract_chunks("m.rs", rs)))
    assert "impl Display for P" in names and "impl Display for P.fmt" in names


def test_long_function_is_split_with_correct_lines():
    body = "\n".join(f"    x{i} = {i}" for i in range(MAX_CHUNK_LINES + 50))
    src = "def big():\n" + body + "\n"
    parts = extract_chunks("b.py", src)
    assert len(parts) > 1
    assert all(p["symbol_name"] == "big" for p in parts)
    assert parts[0]["line_start"] == 1
    assert parts[-1]["line_end"] == MAX_CHUNK_LINES + 51
    for p in parts:
        first = p["chunk_text"].split("\n")[0].strip()
        expected = src.split("\n")[p["line_start"] - 1].strip()
        assert first == expected


def test_symbol_less_code_falls_back_to_windows():
    chunks = extract_chunks("s.py", "import os\nprint(os.getcwd())\n")
    assert len(chunks) == 1 and chunks[0]["kind"] == "block"


def test_markdown_window_named_after_heading():
    chunks = sliding_window("README.md", "# Install\n\nrun it\n", "markdown")
    assert chunks[0]["symbol_name"] == "Install"
    assert chunks[0]["kind"] == "text"


def test_parses_real_project_files():
    """Regression: tree-sitter 0.26.0 segfaulted on these (crashing the whole backend)."""
    import glob
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = glob.glob(os.path.join(root, "backend", "*.py")) + glob.glob(os.path.join(root, "extension", "src", "*.ts"))
    assert files
    for path in files:
        with open(path, encoding="utf-8") as f:
            chunks = extract_chunks(os.path.basename(path), f.read())
        assert chunks, path
