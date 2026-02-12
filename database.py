import sqlite3
import logging
import json
import os
import hashlib
import aiofiles  # Async file I/O library
from datetime import datetime

class StorageManager:
    def __init__(self, base_dir, db_path="crawler_state.db", log_mode=False):
        self.base_dir = base_dir
        self.log_mode = log_mode  # Toggle between log mode and direct mode
        self.log_file = "raw_crawl.jsonl"
        self.db_path = db_path
        self.categories = {
            "people": [
                "professor", "faculty", "staff", "phd", "instructor", "lecturer",
                "directory", "profile", "postdoc", "candidate", "dean", "chair"
            ],
            "academics": ["course", "syllabus", "curriculum", "major", "minor", "degree", "gpa", "credit", "catalog", "class", "registrar"],
            "housing": ["housing", "dorm", "residence", "hall", "apartment", "lease", "dining", "roommate"],
            "financial": ["scholarship", "tuition", "aid", "grant", "loan", "cost", "fee", "bursar"],
            "career": ["career", "internship", "job", "resume", "handshake", "recruiting"],
            "research": ["research", "lab", "publication", "thesis", "citation"], # "professor" removed to avoid over-matching
            "library": ["library", "database", "collection", "borrow", "archive"],
            "events": ["event", "calendar", "schedule", "workshop", "seminar"],
            "policies": ["policy", "regulation", "code", "conduct", "privacy"],
            "about": ["about", "history", "mission", "contact"],
            "news": ["news", "blog", "story"],
            "isss": [
                "isss", "international student", "international students", "international office",
                "international services", "immigration", "visa", "visas", "sevis",
                "i-20", "i20", "ds-2019", "ds2019", "f-1", "f1", "j-1", "j1",
                "opt", "cpt", "stem opt", "optional practical training",
                "curricular practical training", "international scholar", "exchange visitor"
            ],
            "admissions": [
                "admission", "admissions", "apply", "application", "applicant",
                "deadline", "deadlines", "required documents", "requirements",
                "test score", "test scores", "sat", "act", "toefl", "ielts", "gre", "gmat",
                "transfer", "transfer student", "readmission", "deferral", "defer", "prospective student"
            ],
            "student_support": [
                "orientation", "new student", "welcome days", "welcome week",
                "student success", "success program", "tutoring", "tutor",
                "advising", "advisor", "academic advising", "writing center",
                "first-gen", "first generation", "mentoring", "mentor", "coaching"
            ],
            "student_life": [
                "student life", "student organization", "student organizations", "club", "clubs",
                "rso", "registered student organization", "campus recreation", "rec center",
                "student government", "leadership program", "volunteer", "volunteering", "service project"
            ],
            "athletics": [
                "athletic", "athletics", "sport", "sports", "varsity", "intramural", "intramurals",
                "gym", "fitness", "workout", "recreation", "rec center", "stadium", "arena", "pool"
            ],
            "accessibility": [
                "disability", "dres", "accessible", "accessibility", "accommodation", "accommodations",
                "assistive", "wheelchair", "exam accommodation", "testing accommodation"
            ],
            "diversity": [
                "diversity", "equity", "inclusion", "inclusive", "dei", "bias report", "bias incident",
                "cultural center", "cultural centers", "lgbt", "lgbtq"
            ],
            "alumni": [
                "alumni", "alumnus", "alumna", "alumnae", "alumni association", "alumni network",
                "giving", "give", "donor", "donors", "fundraising", "advancement", "development office",
                "homecoming", "alumni event", "alumni events"
            ],
            "campus_services": [
                "id card", "i-card", "icard", "campus card", "printing", "printer", "copying",
                "mail", "mailroom", "package", "packages", "campus store", "bookstore",
                "lost and found", "lost & found"
            ]
        }

        # Always connect to DB for compatibility; in log_mode we skip writing .md files
        self.conn = sqlite3.connect(self.db_path)
        # Enable WAL mode for better concurrent write performance
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.cursor = self.conn.cursor()
        self._init_table()

        # Ensure output directory exists (needed for normal mode)
        if not self.log_mode and not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)

    def _init_table(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS pages (
                url TEXT PRIMARY KEY,
                content_hash TEXT,
                category TEXT,
                title TEXT,
                last_crawled TIMESTAMP
            )
        ''')
        self.conn.commit()

    def should_process(self, url, current_hash):
        """
        Check whether a page needs to be updated.
        In log mode, this check is optional (C++ handles dedup later),
        but we keep it to save bandwidth.
        """
        self.cursor.execute('SELECT content_hash FROM pages WHERE url = ?', (url,))
        row = self.cursor.fetchone()
        if row is None: return True, "NEW"
        if row[0] != current_hash: return True, "UPDATED"
        return False, "UNCHANGED"

    async def save_page(self, url, title, content, links=[], category="uncategorized"):
        """Core save logic: write page data to JSONL (log mode) or Markdown (normal mode)."""
        if self.log_mode:
            entry = {
                "url": url,
                "title": title,
                "content": content,
                "links": links,
                "timestamp": datetime.now().isoformat()
            }
            # Async append to avoid blocking the crawler
            async with aiofiles.open(self.log_file, "a", encoding="utf-8") as f:
                await f.write(json.dumps(entry) + "\n")

            # In log mode, skip SQLite and .md writes; C++ will batch-process the JSONL
            return "logged"

        # --- Mode B: Traditional direct mode (Python generates .md files) ---

        # Generate filename
        filename = self._sanitize_filename(url)
        # Ensure category subdirectory exists
        category_dir = os.path.join(self.base_dir, category)
        os.makedirs(category_dir, exist_ok=True)

        file_path = os.path.join(category_dir, filename)

        # Write Markdown file
        md_content = f"> URL: {url}\n> Title: {title}\n> Category: {category}\n\n# {title}\n\n{content}"
        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
            await f.write(md_content)

        # Update database state
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        self.upsert_page(url, content_hash, category, title)

        return "saved"

    async def save_data(self, data_dict, category="uncategorized"):
        """Wrapper that maps main.py's call signature (data_dict, category) to save_page()."""
        return await self.save_page(
            url=data_dict.get("url", ""),
            title=data_dict.get("title", ""),
            content=data_dict.get("content", ""),
            links=data_dict.get("links", []),
            category=category
        )

    async def delete_data(self, url, category):
        """Delete the markdown file and DB record for a given URL."""
        # Remove the .md file if it exists
        filename = self._sanitize_filename(url)
        file_path = os.path.join(self.base_dir, category, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        # Remove DB record
        self.delete_page(url)

    def classify(self, url, text):
        scores = {k: 0 for k in self.categories.keys()}
        text_lower = text.lower()
        url_lower = url.lower()
        for category, keywords in self.categories.items():
            for word in keywords:
                if word in url_lower: scores[category] += 5
                scores[category] += text_lower.count(word)

        if not scores: return "uncategorized"
        best = max(scores, key=scores.get)
        return best if scores[best] > 2 else "uncategorized"

    def upsert_page(self, url, content_hash, category, title):
        now = datetime.now().isoformat()
        try:
            self.cursor.execute('''
                INSERT INTO pages (url, content_hash, category, title, last_crawled)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    category=excluded.category,
                    title=excluded.title,
                    last_crawled=excluded.last_crawled
            ''', (url, content_hash, category, title, now))
            self.conn.commit()
        except Exception as e:
            logging.error(f"DB Error: {e}")

    def get_all_urls(self):
        self.cursor.execute('SELECT url, category, title FROM pages')
        return self.cursor.fetchall()

    def delete_page(self, url):
        self.cursor.execute('DELETE FROM pages WHERE url = ?', (url,))
        self.conn.commit()

    def close(self):
        self.conn.close()

    def _sanitize_filename(self, url):
        """Helper: sanitize a URL into a safe filename."""
        name = url.replace("https://", "").replace("http://", "").replace("/", "_")
        # Truncate to avoid filesystem errors
        if len(name) > 100:
            name = name[:100]
        return name + ".md"
