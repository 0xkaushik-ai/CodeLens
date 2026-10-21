"""
Index this repo with real Ollama and record the results the demo video shows.

    .venv/bin/python scripts/demo_video/capture.py      # writes scripts/demo_video/out/data.json
    node scripts/demo_video/render.mjs                  # writes scripts/demo_video/out/codelens-demo.mp4

Uses a throwaway index under out/, so your real index in ~/.codelens is untouched.
"""
import json, os, sys, time
sys.path.insert(0, os.getcwd())
from backend.db_client import CodeLensDB, set_db
from backend.indexer import Indexer
from backend.query import search_with_explain, explain_code, ExplainError
from backend.suggest import suggest

out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(out_dir, exist_ok=True)
db = CodeLensDB(os.path.join(out_dir, "db")); set_db(db)
t = time.time()
idx = Indexer(os.getcwd(), db=db).index()
print("index", idx["files"], "files", idx["total_chunks"], "chunks", idx["duration_ms"], "ms")
again = Indexer(os.getcwd(), db=db).index()
print("reindex", again["duration_ms"], "ms")

QUERIES = {
    "semantic": "open the file at the matching line",
    "filters": "embedding retry lang:py -in:tests",
    "exact": "parse_query",
}
data = {"index": idx, "reindex_ms": again["duration_ms"], "queries": {}}
for key, q in QUERIES.items():
    words = q.split(" ")
    steps = {}
    # live results at each word boundary, as search-as-you-type would show
    for i in range(1, len(words) + 1):
        prefix = " ".join(words[:i])
        if len(prefix.replace(" ", "")) < 3:
            continue
        res = search_with_explain(prefix, repo=os.path.realpath(os.getcwd()), top_k=4, live=i < len(words))
        steps[prefix] = res
    if key == "exact":
        for pre in ("parse", "parse_q"):
            steps[pre] = search_with_explain(pre, repo=os.path.realpath(os.getcwd()), top_k=4, live=True)
    data["queries"][key] = {"query": q, "steps": steps}
    final = steps[q]
    print(f"\n{q!r} ({final['query_ms']}ms)")
    for r in final["results"]:
        print(f"   {r['file_path']}:{r['line_start']} {r['symbol_name']} {r['match']} {r['score'] and round(r['score'], 2)}")

# file contents for the "jump to line" scene
for f in ("extension/src/searchPanel.ts", "backend/embedder.py", "backend/query.py"):
    data.setdefault("files", {})[f] = open(f).read()

# Suggestions built from this repo's own symbols (empty-state chips)
repo_path = os.path.realpath(os.getcwd())
data["suggestions"] = suggest(repo_path, db=db)
print(f"\nsuggestions: {data['suggestions']['suggestions']}")

# Real explanation of the top result from the semantic query (for the Explain scene)
semantic_final = data["queries"]["semantic"]["steps"][QUERIES["semantic"]]
explain_text = None
explain_error = None
if semantic_final["results"]:
    top = semantic_final["results"][0]
    print(f"\nexplaining: {top['file_path']}:{top['line_start']} {top['symbol_name']}")
    try:
        explain_text = explain_code(
            top["chunk_text"],
            file_path=top["file_path"],
            symbol_name=top["symbol_name"],
            language=top["language"],
        )
        print(f"explanation ({len(explain_text)} chars): {explain_text[:120]}...")
    except ExplainError as e:
        explain_error = str(e)
        print(f"explain unavailable: {e}")
    except Exception as e:
        explain_error = str(e)
        print(f"explain failed: {e}")

data["explain"] = {
    "text": explain_text,
    "error": explain_error,
    "symbol": semantic_final["results"][0]["symbol_name"] if semantic_final["results"] else "",
    "file": semantic_final["results"][0]["file_path"] if semantic_final["results"] else "",
}

json.dump(data, open(os.path.join(out_dir, "data.json"), "w"), indent=1, default=str)
print(f"\nwrote {os.path.join(out_dir, 'data.json')}")
