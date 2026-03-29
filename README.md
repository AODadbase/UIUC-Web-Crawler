# UIUC Knowledge Base Crawler (AI-Powered)

A production-grade, full-cycle web crawler designed to build a comprehensive knowledge base for the University of Illinois Urbana-Champaign (UIUC). This project serves as an ETL pipeline to feed Vertical Large Language Models (LLMs) with high-quality, structured data.

## Key Features

### 1. Hybrid Crawling Engine
* **Speed & Power**: Combines `aiohttp` for high-concurrency static fetching and `Playwright` for dynamic content rendering (React/JS pages).
* **Resilience**: Features automatic retries, `crt.sh` subdomain discovery, and fallback strategies.
* **Stealth**: Built-in middleware for User-Agent rotation and Proxy integration.

### 2. Incremental Crawling & Resumability
* **Content Hashing**: Tracks page content via MD5 hashes in `crawl_state.json`. Only new or changed pages are re-crawled.
* **TTL-Based Re-crawl**: `visited` is a timestamp dictionary, not a plain set. URLs older than `VISITED_TTL_DAYS` (default: 7) are automatically re-queued on the next run, ensuring data stays fresh without manual intervention.
* **Ctrl+C Safe**: Signal handlers ensure state is always saved on interrupt. Resume exactly where you left off.
* **Periodic Checkpointing**: State is flushed to disk every 10 pages, minimizing data loss on crash.
* **Auto-Pruning**: Detects and removes stale content (404/410) and cleans up old `.md` files.
* **Global Blacklist**: Persistent `blacklist.txt` skips known dead, forbidden, or login-only URLs.
* **Fresh-Run Mode**: `--fresh` flag wipes all crawl state before starting, guaranteeing a full re-crawl from scratch.

### 3. Keyword Classification at Crawl Time
* Pages are classified into 24 category folders during crawling using keyword scoring.
* Categories include: `academics`, `housing`, `financial`, `career`, `research`, `isss`, `admissions`, `athletics`, `health`, `safety`, and more.
* When a page's content changes and its category shifts, the old `.md` file is automatically deleted and the new one placed in the correct folder.

### 4. C++ Optimizer Pipeline
High-performance analysis tools written in multithreaded C++17:
* **PageRank** (`pagerank`): Computes page importance scores via parallel PageRank iteration.
* **Log Resolver** (`log_resolver`): Converts raw JSONL into structured Markdown with YAML frontmatter, using the category from Python and injecting PageRank scores.
* **SimHash** (`simhash`): Detects near-duplicate pages using 64-bit SimHash fingerprints.
* **Inverted Index** (`inverted_index`): Builds a parallel inverted index for full-text search.

### 5. AI Reorganization (Fallback)
* `reorganize_ai.py` uses zero-shot BART classification to handle any pages that couldn't be confidently categorized by keyword matching (left in `uncategorized/`).

## Tech Stack

* **Core**: Python 3.10+, C++17
* **Network**: `aiohttp`, `Playwright`
* **AI/NLP**: `Transformers` (Hugging Face), `Trafilatura`
* **State**: `crawl_state.json` (flat file, no database)
* **Build**: `g++` with `-std=c++17 -O3 -Wall -pthread`

## Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/YOUR_USERNAME/UIUC-Crawler.git
    cd UIUC-Crawler
    ```

2.  Create and activate a virtual environment (Python 3.10+):
    ```bash
    python -m venv .venv
    source .venv/bin/activate
    ```

3.  Install Python dependencies:
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

4.  Build the C++ optimizers:
    ```bash
    cd cpp_optimizer && make && cd ..
    ```

## Usage

### Full Pipeline (Recommended)
```bash
chmod +x run_all.sh

# Incremental run — skips URLs crawled within the last 7 days
./run_all.sh

# Fresh run — clears all state and re-crawls every URL from scratch
./run_all.sh --fresh
```

The pipeline executes:
1. **Crawl** -- `main.py` discovers subdomains, crawls pages, classifies them, writes `raw_crawl.jsonl`
2. **Validate** -- `validate_jsonl.py` strips malformed lines
3. **C++ Analysis** -- PageRank computes scores, Log Resolver generates `.md` files into category folders with PageRank metadata
4. **AI Cleanup** -- `reorganize_ai.py` classifies any remaining `uncategorized/` pages

**Output Example** (generated Markdown):
```markdown
---
url: https://admissions.illinois.edu/
title: Undergraduate Admissions
category: admissions
pagerank_score: 8.342156
priority: high
---

# Undergraduate Admissions

[content...]
```

### Run Individual Components
```bash
# Crawl only (safe to Ctrl+C, state is saved)
python main.py

# Build and run C++ analyzers
cd cpp_optimizer && make && cd ..
./build/pagerank
./build/log_resolver

# AI reorganization only
python reorganize_ai.py
```

## Data Flow

```
Python Crawler (main.py)
     |  classifies pages, writes JSONL with category
     v
raw_crawl.jsonl
     |
     v
C++ PageRank --> pagerank_results.txt
     |
     v
C++ Log Resolver --> uiuc_knowledge_base/{category}/*.md
     |                (with YAML frontmatter + PageRank scores)
     v
AI Reorganizer --> moves uncategorized/ leftovers into proper folders
```

## State Files

| File | Purpose | Persists across runs |
|------|---------|---------------------|
| `crawl_state.json` | URL → `{hash, category, last_crawled}` map; drives TTL and incremental logic | Yes |
| `pending_queue.json` | URLs queued but unprocessed at interrupt time; auto-deleted on clean finish | Interrupt only |
| `raw_crawl.jsonl` | Raw page data consumed by C++ pipeline | Yes (appended/updated) |
| `blacklist.txt` | URLs to permanently skip; scrubbed on each run via blacklist recovery | Yes |

To reset all state and force a full re-crawl, either run `./run_all.sh --fresh` or delete `crawl_state.json`, `pending_queue.json`, and `raw_crawl.jsonl` manually.

## Project Structure
```text
├── main.py                # Entry point & hybrid crawler logic
├── database.py            # StorageManager: JSON state + JSONL logging + classification
├── middleware.py           # Proxy & User-Agent rotation
├── reorganize_ai.py       # Zero-shot AI classification (fallback for uncategorized)
├── validate_jsonl.py      # JSONL validation before C++ processing
├── run_all.sh             # Full pipeline automation script
├── blacklist.txt          # Persistent URL blacklist
├── requirements.txt       # Python dependencies
├── cpp_optimizer/
│   ├── Makefile           # Build configuration (C++17, -O3, -Wall)
│   ├── log_resolver.cpp   # JSONL -> Markdown converter (reads category from JSONL)
│   ├── pagerank.cpp       # Parallel PageRank with convergence detection
│   ├── simhash.cpp        # Near-duplicate detection via SimHash
│   └── invert_index.cpp   # Parallel inverted index builder
└── uiuc_knowledge_base/   # [Output] Categorized Markdown files (gitignored)
```
