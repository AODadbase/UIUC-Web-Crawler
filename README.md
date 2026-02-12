# UIUC Knowledge Base Crawler (AI-Powered)

A production-grade, full-cycle web crawler designed to build a comprehensive knowledge base for the University of Illinois Urbana-Champaign (UIUC). This project serves as an ETL pipeline to feed Vertical Large Language Models (LLMs) with high-quality, structured data.

## Key Features

### 1. Hybrid Crawling Engine
* **Speed & Power**: Combines `aiohttp` for high-concurrency static fetching and `Playwright` for dynamic content rendering (React/JS pages).
* **Resilience**: Features automatic retries, `crt.sh` subdomain discovery, and fallback strategies.
* **Stealth**: Built-in middleware for User-Agent rotation and Proxy integration.

### 2. Intelligent Data Processing
* **Smart Extraction**: Uses `Trafilatura` to algorithmically extract main content, removing ads, navigation bars, and boilerplate noise.
* **Zero-Shot AI Classification**: Utilizes a pre-trained NLI model (BART) to semantically categorize pages into specific domains (e.g., *Faculty Profiles, Course Syllabi, Research Labs*) with high precision.

### 3. C++ Optimizer Pipeline
High-performance analysis tools written in multithreaded C++17:
* **Log Resolver** (`log_resolver`): Converts raw JSONL crawl logs into structured Markdown files using a thread-safe producer-consumer queue.
* **PageRank** (`pagerank`): Computes page importance scores via parallel PageRank iteration with early convergence detection. Results saved to `pagerank_results.txt`.
* **SimHash** (`simhash`): Detects near-duplicate pages using 64-bit SimHash fingerprints and Hamming distance comparison. Results saved to `simhash_results.txt`.
* **Inverted Index** (`inverted_index`): Builds a parallel inverted index for full-text search with per-word URL deduplication. Results saved to `inverted_index.txt`.

### 4. Lifecycle Management
* **Incremental Updates**: Uses SQLite (WAL mode) and content hashing (via `database.py` / `StorageManager`) to track changes, ensuring only new or updated pages are processed.
* **Auto-Pruning**: Automatically detects and removes stale content (404/410) to maintain data integrity.
* **Global Blacklist**: Maintains a persistent `blacklist.txt` file and skips URLs that are known to be login-only, forbidden, or dead.

## Tech Stack

* **Core**: Python 3.10+, C++17
* **Network**: `aiohttp`, `Playwright`
* **AI/NLP**: `Transformers` (Hugging Face), `Trafilatura`
* **Storage**: `SQLite` (WAL mode), `aiofiles`
* **Build**: `g++` with `-std=c++17 -O3 -Wall -pthread`

## Installation & Requirements

1.  Clone the repository:
    ```bash
    git clone https://github.com/YOUR_USERNAME/UIUC-Crawler.git
    cd UIUC-Crawler
    ```

2.  Create and activate a virtual environment (Python 3.10+ recommended):
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # Windows: .venv\Scripts\activate
    ```

3.  Install Python dependencies:
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

4.  Build the C++ optimizers (requires `g++` with C++17 support):
    ```bash
    cd cpp_optimizer
    make
    cd ..
    ```

## Usage

### One-Click Run (Recommended)
Runs the full pipeline: crawl, C++ analysis, then AI classification.

```bash
chmod +x run_all.sh
./run_all.sh
```

The pipeline executes three phases:
1. **Crawl** — `main.py` discovers subdomains and crawls pages into `raw_crawl.jsonl`
2. **C++ Analysis** — Compiles and runs log resolver, PageRank, SimHash, and inverted index
3. **AI Reorganization** — `reorganize_ai.py` classifies uncategorized pages into topic folders

### Run Individual Components

```bash
# Crawl only
python main.py

# Build and run C++ analyzers only
cd cpp_optimizer && make && cd ..
./cpp_optimizer/log_resolver
./cpp_optimizer/pagerank
./cpp_optimizer/simhash
./cpp_optimizer/inverted_index

# AI reorganization only
python reorganize_ai.py
```

## Project Structure
```text
├── main.py                # Entry point & hybrid crawler logic
├── database.py            # StorageManager: SQLite state + Markdown persistence
├── middleware.py           # Proxy & User-Agent rotation
├── reorganize_ai.py       # Zero-shot AI classification & folder reorganization
├── run_all.sh             # Full pipeline automation script
├── blacklist.txt          # Persistent URL blacklist
├── requirements.txt       # Python dependencies
├── cpp_optimizer/
│   ├── Makefile           # Build configuration (C++17, -O3, -Wall)
│   ├── log_resolver.cpp   # JSONL → Markdown converter (multithreaded)
│   ├── pagerank.cpp       # Parallel PageRank with convergence detection
│   ├── simhash.cpp        # Near-duplicate detection via SimHash
│   └── invert_index.cpp   # Parallel inverted index builder
└── uiuc_knowledge_base/   # [Output] Structured data (ignored by Git)
```
