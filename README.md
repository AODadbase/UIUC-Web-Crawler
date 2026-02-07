# UIUC Knowledge Base Crawler for LLM 🎓

A robust, full-cycle web crawler designed to build a comprehensive knowledge base for the University of Illinois Urbana-Champaign (UIUC). This project serves as an ETL pipeline to feed Vertical Large Language Models (LLMs) with high-quality, structured data.

## Key Features

* **Hybrid Architecture**: Combines `aiohttp` for high-concurrency static fetching and `Playwright` for dynamic content rendering (JavaScript/React pages).
* **Intelligent Extraction**: Uses `Trafilatura` and custom algorithms to extract main content while filtering out navigation, ads, and boilerplate noise.
* **Incremental Updates**: Implements a SQLite-based state management system to track content hashes, ensuring only new or updated pages are processed.
* **Lifecycle Management**: Automatically detects and prunes 404/stale content to verify data integrity.
* **Anti-Detection**: Built-in middleware for User-Agent rotation and Proxy integration.
* **Structured Output**: Exports data in Markdown (for RAG) and JSONL (for LLM Fine-tuning), automatically categorized by domain (Academics, Housing, etc.).

## 🛠️ Tech Stack

* **Core**: Python 3.10+
* **Network**: aiohttp, Playwright
* **Parsing**: lxml, Trafilatura
* **Storage**: SQLite, aiofiles (Async I/O)

## Installation

1.  Clone the repository:
    ```bash
    git clone [https://github.com/YOUR_USERNAME/UIUC-Crawler.git](https://github.com/YOUR_USERNAME/UIUC-Crawler.git)
    cd UIUC-Crawler
    ```

2.  Create and activate a virtual environment:
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    ```

3.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

## Usage

1.  (Optional) Add your proxy servers to `proxies.txt` (one per line).
2.  Run the crawler:
    ```bash
    python main.py
    ```
3.  Output data will be saved in `uiuc_knowledge_base/` organized by categories.

## Project Structure

```text
├── main.py              # Entry point & Hybrid Crawler logic
├── database.py          # SQLite state management
├── middleware.py        # Proxy & User-Agent rotation
├── uiuc_knowledge_base/ # Output Data (Ignored by Git)
└── requirements.txt     # Dependencies