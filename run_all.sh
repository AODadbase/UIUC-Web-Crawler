#!/bin/bash
set -e
set -o pipefail

# Resolve the directory this script lives in
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "✅ Phase 1: Python crawler..."
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/main.py"

echo ""
echo "✅ Phase 2: C++ optimization pipeline..."

# 2a. Compile C++ components if needed
if [ ! -f "$SCRIPT_DIR/build/pagerank" ] || [ ! -f "$SCRIPT_DIR/build/log_resolver" ]; then
    echo "  Compiling C++ components..."
    mkdir -p "$SCRIPT_DIR/build"
    g++ -std=c++17 -O2 "$SCRIPT_DIR/cpp_optimizer/pagerank.cpp" -o "$SCRIPT_DIR/build/pagerank"
    g++ -std=c++17 -O2 "$SCRIPT_DIR/cpp_optimizer/log_resolver.cpp" -o "$SCRIPT_DIR/build/log_resolver"
fi

# 2b. Run PageRank first (generates pagerank_results.txt)
echo "  Running PageRank analysis..."
"$SCRIPT_DIR/build/pagerank"

# 2c. Run log resolver (injects PageRank scores into Markdown)
echo "  Generating Markdown with metadata..."
"$SCRIPT_DIR/build/log_resolver"

echo ""
echo "✅ Phase 3: AI-based reorganization..."
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/reorganize_ai.py"

echo ""
echo "✅ Pipeline complete! AI-ready knowledge base generated."
