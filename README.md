<div align="center">

# 🔍 CodeLens

### *Search your codebase by meaning or by name. 100% offline.*

[![CI](https://github.com/TryingtobeingNikhil/CodeLens/actions/workflows/ci.yml/badge.svg)](https://github.com/TryingtobeingNikhil/CodeLens/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.3+-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://typescriptlang.org)
[![SQLite](https://img.shields.io/badge/SQLite-FTS5%20%2B%20sqlite--vec-003B57?style=flat-square&logo=sqlite&logoColor=white)](https://github.com/asg017/sqlite-vec)
[![Ollama](https://img.shields.io/badge/Ollama-nomic--embed--text-FFA500?style=flat-square)](https://ollama.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

**Type a question or a symbol name. Get the exact function. Jump to the line. No internet, no API keys.**

[Quick start](#-quick-start) · [How it works](#%EF%B8%8F-how-it-works) · [Search syntax](#-search-syntax) · [Numbers](#-measured-numbers) · [API](#-api)

</div>

---

## 🎯 The problem

I join a large codebase. I know there's *some* function that refreshes JWT tokens, but `Ctrl+F "token"` returns 847 matches. I spend 20 minutes hunting. Keyword search matches *text*, not *intent*: it can't tell that **"where do sessions expire"** and `def invalidate_jwt_token()` are the same thing.

Pure semantic search has the opposite problem: ask for `parseConfig` by name and an embedding model may rank a vaguely similar function above it.

**CodeLens does both.** Every query runs a semantic search and a keyword search, and fuses the two rankings.

```
Query: "where do we split camelCase identifiers"          (145 ms)

1  backend/text.py:19            split_identifier       function  both  0.78
2  backend/text.py:27            index_terms            function  both  0.69
3  tests/test_query_parse.py:29  test_split_identifier  function  both  0.73

Query: "embedding failures retry lang:py -in:tests"       (31 ms)

1  backend/embedder.py:34        Embedder._embed              method  both  0.67
2  backend/indexer.py:184        Indexer._embed_missing       method  both  0.65
3  backend/db_client.py:261      CodeLensDB.put_embeddings    method  both  0.67
```

All of it runs locally. Turn the WiFi off; it still works.

---

## ✨ Features

| | |
|---|---|
| **Hybrid search** | Semantic similarity (Ollama embeddings) and BM25 keyword search over symbol names, identifier sub-words, paths and code, merged with reciprocal-rank fusion. An exact symbol-name match always ranks first. |
| **Search as you type** | Results update while you type. The last word is matched as a prefix; stale responses are dropped. |
| **Filters** | `in:api -in:tests lang:py kind:function`, the same `key:value` style as [gyotaku](https://github.com/xevrion/gyotaku). An unfinished filter is ignored, not searched for, so results don't empty mid-keystroke. |
| **Symbol-aware chunks** | tree-sitter splits code into functions, methods, classes and types (Python, TS/TSX, JS, Go, Rust, Java, C/C++), named like `Class.method`. Classes get a compact summary chunk; long functions are split into overlapping parts. |
| **Incremental and live** | Only changed files are re-chunked; only new content is re-embedded. A function that moved keeps its vector and gets its new line numbers. A file watcher picks up saves, creates, renames and deletes, and respects `.gitignore`. |
| **Degrades gracefully** | If Ollama is down, files are still indexed and keyword search keeps working; missing vectors are filled in on the next run. |
| **Per-repo indexes** | Each workspace is indexed and searched separately in one local database. |
| **VS Code native** | Sidebar search, keyboard navigation, jump-to-line with a highlight flash, "Find Similar Code" on a selection, status bar. |

---

## 🏗️ How it works

```
                          ┌──────────────────────────── ~/.codelens/codelens.db (SQLite) ──┐
 git ls-files             │                                                               │
 (honours .gitignore)     │  files        path → content hash (incremental indexing)      │
      │                   │  chunks       symbol, kind, language, lines, code             │
      ▼                   │  chunks_fts   FTS5: symbol · identifier sub-words · path · code│
 tree-sitter chunker ────▶│  embeddings   one float32 vector per distinct chunk content   │
      │                   │  vec_*        sqlite-vec index over those vectors, per repo   │
      ▼                   └───────────────────────────────────────────────────────────────┘
 Ollama nomic-embed-text            ▲                                   ▲
 (batched, 768-dim)  ───────────────┘                                   │
                                                                        │
 query ─▶ parse filters ─┬─▶ embed query ─▶ cosine top-k (sqlite-vec) ──┤
                         └─▶ FTS5 BM25 top-k ───────────────────────────┤
                                                                        ▼
                                      reciprocal-rank fusion + exact-name boost
                                      ─▶ collapse duplicates ─▶ VS Code sidebar
```

### Why SQLite + FTS5 + sqlite-vec

Earlier versions described the store as "Actian VectorAI DB"; it was actually a SQLite table of vectors that was fully read from disk and scanned with numpy on every query. It is now a real hybrid index in one file:

- **One transaction per file.** A file's chunks, its keyword index and its vectors change together, so the indexes never disagree, even if indexing is interrupted.
- **Keyword and vector search in the same place**, filtered by the same SQL `WHERE` clause.
- **[sqlite-vec](https://github.com/asg017/sqlite-vec)** does exact cosine search in C, partitioned by repo. When the Python build can't load SQLite extensions (python.org's macOS builds can't), CodeLens falls back to an in-memory numpy matrix that is rebuilt only when the index changes. Vectors are stored as plain BLOBs, so the sqlite-vec index is rebuilt automatically the next time a capable Python opens the file.
- **Tiny and portable.** No server, no JVM, no cloud: `pip install sqlite-vec` is ~200 KB, and the whole index is one file you can delete or copy.

Search is exact, not approximate: at codebase scale (thousands to low hundreds of thousands of chunks) a brute-force scan in C takes milliseconds, and an approximate index would only cost recall.

---

## 🚀 Quick start

Requirements: Python 3.10+, [Ollama](https://ollama.com), and Node 18+ for building the extension.

```bash
git clone https://github.com/TryingtobeingNikhil/CodeLens.git
cd CodeLens
./setup.sh      # installs Ollama if needed, pulls nomic-embed-text, creates .venv, builds the extension
./start.sh      # starts Ollama and the backend on http://127.0.0.1:8000
```

In VS Code, press F5 to start the Extension Development Host, open a project, click the CodeLens icon, then **Re-index**. The extension reuses a backend that is already running, or starts one from its `.venv`.

Index and search from a terminal:

```bash
curl -N -X POST localhost:8000/index -H 'Content-Type: application/json' -d "{\"repo_path\": \"$PWD\"}"
curl -X POST localhost:8000/query -H 'Content-Type: application/json' \
     -d "{\"query\": \"where do we retry failed requests -in:tests\", \"repo_path\": \"$PWD\"}"
```

`curl` gets coloured terminal output; send `Accept: application/json` for JSON.

---

## 🔎 Search syntax

Type plain words, a question, or a symbol name. Add filters anywhere:

| Filter | Matches |
|---|---|
| `in:<name>` | Paths with a folder or file starting with `<name>`, at any depth, ignoring case. `in:api`, `in:src/auth`, `in:"my dir"`. Several `in:` filters are ORed. |
| `-in:<name>` | Excludes those paths. `-in:tests` |
| `lang:<lang>` | `py`, `ts` (includes TSX), `tsx`, `js`, `go`, `rs`, `java`, `cpp`, `md`, `yaml`, `toml` |
| `kind:<kind>` | `function`, `method`, `class`, `interface`, `type`, `struct`, `impl`, `module`, `text` (docs and config) |

A query made only of filters, such as `kind:class in:models`, lists every matching chunk. Words with a colon that aren't one of these keys (`http://`, `12:30`) are searched as text.

### Keys in the sidebar

| Key | Action |
|---|---|
| Type | Search as you type |
| `↑` `↓` | Select a result |
| `Enter` | Open the selected result, or search |
| `Esc` | Clear the selection, then the query |
| `Cmd/Ctrl+Alt+K` | Focus CodeLens search |
| Shift+click **Re-index** | Rebuild the index from scratch |

Commands: **CodeLens: Search**, **Find Similar Code** (editor context menu on a selection), **Re-index Workspace**, **Rebuild Index From Scratch**, **Restart Backend**, **Show Logs**.

### Settings

| Setting | Default | |
|---|---|---|
| `codelens.pythonPath` | *(empty)* | Python for the backend. Empty: the backend folder's `.venv`, then `python3` |
| `codelens.backendPath` | *(empty)* | Folder containing `backend/main.py`. Empty: the copy bundled in the extension |
| `codelens.port` | `8000` | Backend port |
| `codelens.autoStartBackend` | `true` | Start the backend unless one is already running |

Backend environment variables (or `.env`): `CODELENS_DB_PATH` (default `~/.codelens`), `OLLAMA_HOST`, `EMBED_MODEL`, `EXPLAIN_MODEL` (default `mistral`), `MAX_FILE_BYTES` (default 512 KB), `EMBED_BATCH_SIZE`, `CODELENS_USE_SQLITE_VEC=0` to force the numpy backend.

---

## 📊 Measured numbers

Measured on this repository (24 files) on an Apple Silicon Mac with `nomic-embed-text` in Ollama:

| Metric | Result |
|---|---|
| First index (217 chunks, all embedded) | 9.3 s |
| Re-index with nothing changed | 33 ms |
| File saved → searchable | ~1–2 s (1 s debounce + embedding) |
| Query, warm | 30–60 ms, most of it embedding the query |
| Query with only filters | < 5 ms |
| Index size | 4.6 MB |

---

## 🔌 API

The backend only answers local requests: requests with a non-local `Host` header (DNS rebinding) or a browser `Origin` are rejected, and there is no CORS, so web pages you visit can't read your code through it.

| Endpoint | |
|---|---|
| `POST /index` `{repo_path, force_reindex?}` | Server-sent events: `start`, `progress` (`phase: scan\|embed`, `done`, `total`, `file`), `error` (per file, non-fatal), `complete` (counts, `embed_error`, `duration_ms`). Starts watching the repo. `409` if that repo is already being indexed. |
| `POST /query` `{query, repo_path?, top_k?, explain?, live?}` | `results` (file, lines, symbol, kind, language, code, `score` = cosine similarity or `null`, `match` = `both\|semantic\|keyword\|filter`), parsed `filters`, `terms`, `warnings`, `explain_text`, `query_ms`, `total_indexed` |
| `GET /status?repo_path=` | Chunk, file and embedded counts, vector backend, indexing and watching state, known repos |
| `GET /health` | Ollama reachability, whether the embedding model is pulled, database state |

---

## 🔒 Privacy

Everything runs on your machine: no telemetry, no accounts, no network access except to your local Ollama. The index lives in `~/.codelens/` and can be deleted at any time. Files ignored by git, files over 512 KB, and minified or binary files are never indexed.

---

## 📁 Project structure

```
backend/
  main.py               FastAPI app: /index (SSE), /query, /status, /health; repo registry; local-only guard
  indexer.py            File listing (git-aware), incremental sync, batched embedding, debounced watcher
  tree_sitter_parser.py Symbol-aware chunking for 8 languages, sliding windows for docs and config
  db_client.py          SQLite schema, FTS5 and sqlite-vec indexes, numpy fallback
  query.py              Filter parsing, hybrid search, rank fusion, explain mode
  embedder.py           Batched Ollama embeddings with a query cache
  text.py               Identifier splitting and FTS query building
extension/
  src/extension.ts      Backend discovery and lifecycle, commands, status bar
  src/searchPanel.ts    Sidebar webview provider, jump-to-line, indexing progress
  src/client.ts         HTTP/SSE client (Node's built-in fetch)
  media/panel.html      Sidebar UI (strict CSP, all backend data escaped)
shared/types.ts         API wire types
tests/                  pytest suite (runs against both sqlite-vec and numpy backends)
```

---

## 🧪 Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest          # backend tests, no Ollama needed (a fake embedder is used)

npm install
npm run watch                       # then F5 in VS Code
npm run package                     # builds codelens-<version>.vsix, backend included
```

CI runs the tests on Linux and macOS with Python 3.10 and 3.12, compiles the extension and uploads the `.vsix`.

---

## 🗺️ Roadmap

- [x] Hybrid keyword + semantic search with rank fusion
- [x] `in:` / `lang:` / `kind:` filters
- [x] Search as you type
- [x] Incremental indexing that survives Ollama being offline
- [x] Per-repo indexes, `.gitignore` support
- [ ] Group near-duplicate results (copy-pasted code) into one card, with the rest a key away
- [ ] A `codelens` CLI (`codelens search`, `codelens index`, `codelens stats`)
- [ ] Index the most recently changed files first, so search is useful while a large repo indexes
- [ ] Optional reranking of the top results with a small local cross-encoder
- [ ] "Find usages by meaning": rank callers of the selected function

---

## 📄 License

MIT. See [LICENSE](LICENSE).

---

<div align="center">

**Built For hackthon & I literally killed it.** · April 2026

*CodeLens is proof that developer tools don't need the cloud to be powerful.*

</div>
