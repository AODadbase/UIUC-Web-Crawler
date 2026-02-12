#!/bin/bash

# Define Python path inside the virtual environment
PYTHON_CMD="./.venv/bin/python"

echo "Phase 1: starting high-speed crawler..."
$PYTHON_CMD main.py

echo "--------------------------------"
echo "Crawling finished. Sleeping for 3 seconds..."
sleep 3

echo "Phase 2: starting AI-based reorganization..."
$PYTHON_CMD reorganize_ai.py

echo "All tasks completed. Knowledge base has been updated."