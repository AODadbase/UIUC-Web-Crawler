# C++ Optimizer Pipeline

High-performance analysis tools for the UIUC Knowledge Base Crawler.

## Components

### 1. PageRank (`pagerank.cpp`)
Computes page authority scores using parallel PageRank iteration.

**Input:** `raw_crawl.jsonl`
**Output:** `pagerank_results.txt` (format: `<score>\t<url>`)

### 2. Log Resolver (`log_resolver.cpp`)
Converts raw JSONL into structured Markdown files with metadata injection.

**Input:**
- `raw_crawl.jsonl` (crawl data with `category` field from Python)
- `pagerank_results.txt` (authority scores, optional)

**Output:** `uiuc_knowledge_base/{category}/*.md` (Markdown with YAML frontmatter)

Files are written directly into their category folder (e.g., `academics/`, `housing/`). Falls back to `uncategorized/` if the category field is missing.

**Example output:**
```markdown
---
url: https://admissions.illinois.edu/
title: Undergraduate Admissions
category: admissions
pagerank_score: 6.891234
priority: high
---

# Undergraduate Admissions

[content...]
```

### 3. SimHash (`simhash.cpp`)
Detects near-duplicate pages using 64-bit SimHash fingerprints.

**Input:** `raw_crawl.jsonl`
**Output:** `simhash_results.txt`

### 4. Inverted Index (`inverted_index.cpp`)
Builds a parallel inverted index for full-text search.

**Input:** `raw_crawl.jsonl`
**Output:** `inverted_index.txt`

## Build

```bash
# Use Makefile
make

# Or manual
g++ -std=c++17 -O2 pagerank.cpp -o pagerank -pthread
g++ -std=c++17 -O2 log_resolver.cpp -o log_resolver -pthread
g++ -std=c++17 -O2 simhash.cpp -o simhash -pthread
g++ -std=c++17 -O2 invert_index.cpp -o inverted_index -pthread
```

## Pipeline Integration

```
Python Crawler --> raw_crawl.jsonl (with category per page)
                        |
                        v
                   C++ PageRank --> pagerank_results.txt
                        |
                        v
                  C++ Log Resolver --> uiuc_knowledge_base/{category}/*.md
                        |               (with PageRank metadata)
                        v
                  AI Reorganizer --> handles uncategorized/ leftovers
```

Or use the automated pipeline: `cd .. && ./run_all.sh`
