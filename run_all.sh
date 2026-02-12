#!/bin/bash

PYTHON_CMD="./.venv/bin/python"

echo "==========================================="
echo "🚀 PHASE 1: HIGH-SPEED CRAWLING (Python)"
echo "   Appending raw data to raw_crawl.jsonl..."
echo "==========================================="
# 确保 main.py 里的 log_mode=True 已经生效
$PYTHON_CMD main.py

echo ""
echo "==========================================="
echo "⚙️ PHASE 2: C++ HIGH-PERFORMANCE RESOLVER"
echo "   Compiling and processing data..."
echo "==========================================="

# 1. 编译 C++ 工具
cd cpp_optimizer
make
cd ..

# 2. 运行 C++ 工具生成 Markdown
./cpp_optimizer/log_resolver

echo ""
echo "==========================================="
echo "🧠 PHASE 3: AI CLASSIFICATION (Python)"
echo "   Organizing files..."
echo "==========================================="
$PYTHON_CMD reorganize_ai.py

echo ""
echo "🎉 PIPELINE COMPLETE!"