#!/bin/bash
set -e
set -o pipefail

# Resolve the directory this script lives in
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
