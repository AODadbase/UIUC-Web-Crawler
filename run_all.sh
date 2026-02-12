#!/bin/bash
set -e
set -o pipefail

# Resolve the directory this script lives in
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Crawl
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/main.py"

# 2. Compile and run C++ analyzers
cd "$SCRIPT_DIR/cpp_optimizer"
make
cd "$SCRIPT_DIR"

"$SCRIPT_DIR/cpp_optimizer/log_resolver"
"$SCRIPT_DIR/cpp_optimizer/pagerank"
"$SCRIPT_DIR/cpp_optimizer/simhash"
"$SCRIPT_DIR/cpp_optimizer/inverted_index"

# 3. AI-based reorganization
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/reorganize_ai.py"
