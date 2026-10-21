#!/bin/bash
# ─────────────────────────────────────────────────
#  CodeLens — start the backend on http://127.0.0.1:8000
#
#  Uses Ollama if it is running (semantic search + Explain). If it isn't,
#  the backend still starts and keyword search works. This script never
#  installs or starts Ollama for you.
# ─────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
fail() { echo -e "${RED}❌ $*${NC}"; exit 1; }

PYTHON="./.venv/bin/python"
[ -x "$PYTHON" ] || fail "No .venv found. Run ./setup.sh first"

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"
PORT="${CODELENS_PORT:-8000}"

if curl -fs "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
    if curl -fs "$OLLAMA_HOST/api/tags" | grep -q "\"$EMBED_MODEL"; then
        ok "Ollama running with $EMBED_MODEL"
    else
        warn "Ollama is running but $EMBED_MODEL is not pulled. Run: ollama pull $EMBED_MODEL"
    fi
else
    warn "Ollama is not reachable at $OLLAMA_HOST: keyword search only."
    warn "Start Ollama any time (no restart needed), then Re-index to add the missing vectors."
fi

echo ""
ok "Starting CodeLens backend on http://127.0.0.1:$PORT"
echo "   POST /index        index a repo"
echo "   POST /query        search"
echo "   POST /explain      explain a snippet"
echo "   GET  /suggestions  example searches for a repo"
echo "   GET  /status       chunk count, watcher state"
echo "   GET  /health       Ollama + database health"
echo ""
exec "$PYTHON" -m uvicorn backend.main:app --host 127.0.0.1 --port "$PORT"
