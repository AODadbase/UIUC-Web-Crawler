#!/bin/bash
set -e
set -o pipefail

# Resolve the directory this script lives in
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --fresh flag: wipe all crawl state so every URL is re-crawled from scratch
FRESH=0
for arg in "$@"; do
    if [ "$arg" = "--fresh" ]; then
        FRESH=1
    fi
done

if [ "$FRESH" = "1" ]; then
    echo "[Fresh run] Clearing crawl state..."
    rm -f "$SCRIPT_DIR/crawl_state.json"
    rm -f "$SCRIPT_DIR/pending_queue.json"
    rm -f "$SCRIPT_DIR/raw_crawl.jsonl"
    echo "[Fresh run] State cleared. All URLs will be re-crawled."
    echo ""
fi

echo "Cleaning up orphaned Chromium processes..."
pkill -f chromium || true

echo "Phase 1: Python crawler..."
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/main.py"

echo ""
echo "Phase 1.5: Validating JSONL output..."
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/validate_jsonl.py"

echo ""
echo "Phase 2: C++ optimization pipeline..."

# 2a. Compile C++ components if binaries are missing or source is newer
mkdir -p "$SCRIPT_DIR/build"
for component in pagerank log_resolver; do
    src="$SCRIPT_DIR/cpp_optimizer/${component}.cpp"
    bin="$SCRIPT_DIR/build/${component}"
    if [ ! -f "$bin" ] || [ "$src" -nt "$bin" ]; then
        echo "  Compiling ${component}..."
        g++ -std=c++17 -O2 "$src" -o "$bin"
    fi
done

# 2b. Run PageRank first (generates pagerank_results.txt)
echo "  Running PageRank analysis..."
"$SCRIPT_DIR/build/pagerank"

# 2c. Run log resolver (reads category from JSONL, writes to category folders)
echo "  Generating Markdown with metadata..."
"$SCRIPT_DIR/build/log_resolver"

echo ""
echo "Phase 3: AI-based reorganization (uncategorized leftovers)..."
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/reorganize_ai.py"

echo ""
echo "Pipeline complete! AI-ready knowledge base generated."
