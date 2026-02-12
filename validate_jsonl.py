#!/usr/bin/env python3
"""Validate raw_crawl.jsonl before passing to the C++ pipeline.

Reads raw_crawl.jsonl line by line, validates each line is valid JSON
with the required keys (url, title, content), and writes valid lines
to raw_crawl_validated.jsonl. Malformed lines are logged and skipped.

Exits with code 1 if zero valid lines remain (aborts the pipeline).
"""

import json
import sys
import os

REQUIRED_KEYS = {"url", "title", "content"}
INPUT_FILE = "raw_crawl.jsonl"
OUTPUT_FILE = "raw_crawl_validated.jsonl"

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"ERROR: {INPUT_FILE} not found.")
        sys.exit(1)

    total = 0
    valid = 0
    skipped = 0

    with open(INPUT_FILE, "r", encoding="utf-8") as fin, \
         open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
        for line_num, line in enumerate(fin, start=1):
            total += 1
            stripped = line.strip()
            if not stripped:
                skipped += 1
                print(f"  [SKIP] Line {line_num}: empty line")
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                skipped += 1
                print(f"  [SKIP] Line {line_num}: invalid JSON ({e})")
                continue

            missing = REQUIRED_KEYS - set(obj.keys())
            if missing:
                skipped += 1
                print(f"  [SKIP] Line {line_num}: missing keys {missing}")
                continue

            valid += 1
            fout.write(stripped + "\n")

    print(f"\nValidation summary: {total} total, {valid} valid, {skipped} skipped")

    if valid == 0:
        print("ERROR: No valid lines — aborting pipeline.")
        sys.exit(1)

    # Replace original with validated file so C++ reads clean data
    os.replace(OUTPUT_FILE, INPUT_FILE)
    print(f"Replaced {INPUT_FILE} with validated output.")

if __name__ == "__main__":
    main()
