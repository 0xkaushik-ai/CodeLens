#!/bin/bash
# ============================================================
# CodeLens — developer setup
#
# Creates .venv with the backend and builds the VS Code extension.
# It does NOT install Ollama: that is optional and up to you (use your package
# manager, Homebrew, or ollama.com). Without Ollama, keyword search works and
# semantic search / Explain are off until you install it.
#
#   ./setup.sh                 # backend + extension
#   ./setup.sh --no-extension  # backend only
# ============================================================
set -e
cd "$(dirname "$0")"

BUILD_EXTENSION=1
for arg in "$@"; do
    case "$arg" in
        --no-extension) BUILD_EXTENSION=0 ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg"; exit 2 ;;
    esac
done

PYTHON=""
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
fail() { echo -e "${RED}❌ $*${NC}"; exit 1; }

echo ""
echo "  CodeLens setup: offline semantic + keyword code search"
echo ""

# ────────────────────────────────────────────────────────────
# 1. Python 3.10+
# ────────────────────────────────────────────────────────────
echo "▶ Looking for Python 3.10+..."
for py in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py" &>/dev/null && \
       [ "$("$py" -c 'import sys; print(sys.version_info >= (3, 10))' 2>/dev/null)" = "True" ]; then
        PYTHON="$py"; ok "Found $($py --version)"; break
    fi
done
[ -z "$PYTHON" ] && fail "Python 3.10+ not found. Install it with your package manager or from https://python.org"

# ────────────────────────────────────────────────────────────
# 2. Backend in .venv
# ────────────────────────────────────────────────────────────
echo "▶ Creating .venv and installing the backend..."
"$PYTHON" -m venv .venv || fail "Could not create a virtualenv with $PYTHON (on Debian/Ubuntu: apt install python3-venv)"
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -e ".[dev]"
ok "Backend installed in .venv"

# ────────────────────────────────────────────────────────────
# 3. Ollama: detected, never installed
# ────────────────────────────────────────────────────────────
echo "▶ Checking for Ollama (optional)..."
if curl -fs "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
    ok "Ollama is running at $OLLAMA_HOST"
    if curl -fs "$OLLAMA_HOST/api/tags" | grep -q "\"$EMBED_MODEL"; then
        ok "$EMBED_MODEL is already pulled"
    elif command -v ollama &>/dev/null; then
        echo "▶ Pulling $EMBED_MODEL (~274 MB, one time)..."
        ollama pull "$EMBED_MODEL" && ok "$EMBED_MODEL ready" || warn "Pull failed; run: ollama pull $EMBED_MODEL"
    else
        warn "Run this to enable semantic search: ollama pull $EMBED_MODEL"
    fi
elif command -v ollama &>/dev/null; then
    warn "Ollama is installed but not running. Start it (ollama serve, or your service manager),"
    warn "then run: ollama pull $EMBED_MODEL"
else
    warn "Ollama not found. That's fine: keyword search works without it."
    echo "   For semantic search and Explain, install Ollama your preferred way, for example:"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "     brew install ollama         (or the app from https://ollama.com/download)"
    else
        echo "     your package manager (pacman -S ollama, nix, ...), or https://ollama.com/download"
    fi
    echo "   then: ollama pull $EMBED_MODEL"
fi

# ────────────────────────────────────────────────────────────
# 4. VS Code extension
# ────────────────────────────────────────────────────────────
if [ "$BUILD_EXTENSION" = "1" ]; then
    if command -v npm &>/dev/null; then
        echo "▶ Building the VS Code extension..."
        npm install --silent
        npm run build && ok "Extension built (press F5 in VS Code, or: npm run package)" \
            || warn "Extension build failed; see the output above"
    else
        warn "npm not found, skipping the extension build (install Node.js 18+ to build it)"
    fi
fi

echo ""
ok "CodeLens is ready. Start the backend with: ./start.sh"
echo "   (The VS Code extension starts it on its own if it isn't running.)"
echo ""
