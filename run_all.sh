#!/bin/bash

# 定义虚拟环境里的 Python 路径 (这是最稳妥的写法)
PYTHON_CMD="./.venv/bin/python"

echo "🚀 第一阶段：启动极速爬虫..."
$PYTHON_CMD main.py

echo "--------------------------------"
echo "⏸️ 爬取完成。休息 3 秒..."
sleep 3

echo "🧠 第二阶段：启动 AI 智能归类..."
$PYTHON_CMD reorganize_ai.py

echo "🎉 全部完成！知识库已更新。"