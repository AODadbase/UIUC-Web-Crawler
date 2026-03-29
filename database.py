import json
import logging
import os
import asyncio
import aiofiles
from datetime import datetime

CATEGORIES = {
    "people": [
        "professor", "faculty", "staff", "phd", "instructor", "lecturer",
        "directory", "profile", "postdoc", "candidate", "dean", "chair"
    ],
    "academics": [
        "course", "syllabus", "curriculum", "major", "minor", "degree",
        "gpa", "credit", "catalog", "class", "registrar"
    ],
    "housing": [
        "housing", "dorm", "residence", "hall", "apartment", "lease",
        "dining", "roommate"
    ],
    "financial": [
        "scholarship", "tuition", "aid", "grant", "loan", "cost", "fee", "bursar"
    ],
    "career": [
        "career", "internship", "job", "resume", "handshake", "recruiting"
    ],
    "research": [
        "research", "lab", "publication", "thesis", "citation"
    ],
    "library": [
        "library", "collection", "borrow", "archive"
    ],
    "events": [
        "event", "calendar", "schedule", "workshop", "seminar"
    ],
    "policies": [
        "policy", "regulation", "code", "conduct", "privacy"
    ],
    "about": [
        "about", "history", "mission", "contact"
    ],
    "news": [
        "news", "blog", "story"
    ],
    "isss": [
        "isss", "international student", "international students",
        "international office", "international services", "immigration",
        "visa", "visas", "sevis", "i-20", "i20", "ds-2019", "ds2019",
        "f-1", "f1", "j-1", "j1", "opt", "cpt", "stem opt",
        "optional practical training", "curricular practical training",
        "international scholar", "exchange visitor"
    ],
    "admissions": [
        "admission", "admissions", "apply", "application", "applicant",
        "deadline", "deadlines", "required documents", "requirements",
        "test score", "test scores", "sat", "act", "toefl", "ielts",
        "gre", "gmat", "transfer", "transfer student", "readmission",
        "deferral", "defer", "prospective student"
    ],
    "student_support": [
        "orientation", "new student", "welcome days", "welcome week",
        "student success", "success program", "tutoring", "tutor",
        "advising", "advisor", "academic advising", "writing center",
        "first-gen", "first generation", "mentoring", "mentor", "coaching"
    ],
    "student_life": [
        "student life", "student organization", "student organizations",
        "club", "clubs", "rso", "registered student organization",
        "campus recreation", "rec center", "student government",
        "leadership program", "volunteer", "volunteering", "service project"
    ],
    "athletics": [
        "athletic", "athletics", "sport", "sports", "varsity",
        "intramural", "intramurals", "gym", "fitness", "workout",
        "recreation", "stadium", "arena", "pool"
    ],
    "accessibility": [
        "disability", "dres", "accessible", "accessibility",
        "accommodation", "accommodations", "assistive", "wheelchair",
        "exam accommodation", "testing accommodation"
    ],
    "diversity": [
        "diversity", "equity", "inclusion", "inclusive", "dei",
        "bias report", "bias incident", "cultural center",
        "cultural centers", "lgbt", "lgbtq"
    ],
    "alumni": [
        "alumni", "alumnus", "alumna", "alumnae", "alumni association",
        "alumni network", "giving", "give", "donor", "donors",
        "fundraising", "advancement", "development office",
        "homecoming", "alumni event", "alumni events"
    ],
    "campus_services": [
        "id card", "i-card", "icard", "campus card", "printing",
        "printer", "copying", "mail", "mailroom", "package", "packages",
        "campus store", "bookstore", "lost and found", "lost & found"
    ],
    "transportation": [
        "parking", "transportation", "bus", "shuttle", "transit", "commute"
    ],
    "health": [
        "health", "wellness", "counseling", "mental health", "immunization",
        "vaccination", "clinic", "medical"
    ],
    "safety": [
        "safety", "police", "emergency", "campus safety", "security"
    ],
    "it_services": [
        "it support", "software", "technology services", "netid", "vpn",
        "wifi", "network", "computer lab"
    ],
}

CHECKPOINT_INTERVAL = 10

BASE_DIR = "uiuc_knowledge_base"


def sanitize_filename(url):
    """Convert a URL into a safe filename (must match C++ sanitize_filename)."""
    name = url
    for c in "://.":
        name = name.replace(c, "_")
    if len(name) > 100:
        name = name[:100]
    return name + ".md"


class StorageManager:
    def __init__(self, state_file="crawl_state.json"):
        self.log_file = "raw_crawl.jsonl"
        self.state_file = state_file
        self.state = self._load_state()
        self._upsert_count = 0

        # Single lock serializing all JSONL file reads and writes.
        # Without this, concurrent coroutines performing rewrite operations
        # would clobber each other's updates.
        self._jsonl_lock = asyncio.Lock()

        self._logged_urls = set()
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entry = json.loads(line)
                            self._logged_urls.add(entry.get("url", ""))
                print(f"Loaded {len(self._logged_urls)} existing entries from {self.log_file}")
            except Exception as e:
                print(f"Warning: could not read {self.log_file}: {e}")

    def _load_state(self):
        """Load crawl state from JSON file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        return {}
                    return json.loads(content)
            except json.JSONDecodeError:
                print(f"Warning: {self.state_file} is corrupted. Starting with empty state.")
                return {}
        return {}

    def _save_state(self):
        """Atomically write crawl state to JSON file."""
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.state_file)

    def should_process(self, url, current_hash):
        """Check whether a page needs to be crawled based on content hash."""
        entry = self.state.get(url)
        if entry is None:
            return True, "NEW"
        if entry["hash"] != current_hash:
            return True, "UPDATED"
        return False, "UNCHANGED"

    def classify(self, url, text):
        """Keyword-based classification into category folders."""
        scores = {k: 0 for k in CATEGORIES}
        text_lower = text.lower()
        url_lower = url.lower()
        for category, keywords in CATEGORIES.items():
            for word in keywords:
                if word in url_lower:
                    scores[category] += 5
                scores[category] += text_lower.count(word)
        best = max(scores, key=scores.get)
        return best if scores[best] > 2 else "uncategorized"

    async def save_page(self, url, title, content, links=None, category="uncategorized"):
        """Write page data to JSONL for C++ to process downstream."""
        if links is None:
            links = []
        entry = {
            "url": url,
            "title": title,
            "content": content,
            "category": category,
            "links": links,
            "timestamp": datetime.now().isoformat(),
        }

        # The entire check-then-write sequence must be atomic to prevent
        # two concurrent workers from both reading the old file state and
        # each writing a conflicting version.
        async with self._jsonl_lock:
            if url in self._logged_urls:
                await self._rewrite_jsonl_entry(url, entry)
            else:
                async with aiofiles.open(self.log_file, "a", encoding="utf-8") as f:
                    await f.write(json.dumps(entry) + "\n")
                self._logged_urls.add(url)
        return "logged"

    async def _rewrite_jsonl_entry(self, url, new_entry):
        """Replace an existing JSONL entry in-place.

        Must only be called while self._jsonl_lock is held.
        """
        lines = []
        async with aiofiles.open(self.log_file, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing = json.loads(line)
                    if existing.get("url") == url:
                        lines.append(json.dumps(new_entry))
                    else:
                        lines.append(line)
                except json.JSONDecodeError:
                    lines.append(line)
        async with aiofiles.open(self.log_file, "w", encoding="utf-8") as f:
            await f.write("\n".join(lines) + "\n")

    def upsert_page(self, url, content_hash, category="uncategorized"):
        old = self.state.get(url)
        if old and old.get("category") and old["category"] != category:
            old_path = os.path.join(BASE_DIR, old["category"], sanitize_filename(url))
            if os.path.exists(old_path):
                os.remove(old_path)
                logging.info(f"[Moved] {old['category']} -> {category}: {url}")

        self.state[url] = {
            "hash": content_hash,
            "category": category,
            "last_crawled": datetime.now().isoformat(),
        }
        self._upsert_count += 1
        if self._upsert_count % CHECKPOINT_INTERVAL == 0:
            self._save_state()

    def get_all_urls(self):
        return list(self.state.keys())

    async def delete_page(self, url):
        """Remove a page from state, disk, and JSONL log."""
        old = self.state.pop(url, None)
        if old and old.get("category"):
            old_path = os.path.join(BASE_DIR, old["category"], sanitize_filename(url))
            if os.path.exists(old_path):
                os.remove(old_path)
        self._logged_urls.discard(url)
        await self._remove_jsonl_entry(url)

    async def _remove_jsonl_entry(self, url):
        """Asynchronously remove a URL's entry from the JSONL file."""
        async with self._jsonl_lock:
            if not os.path.exists(self.log_file):
                return
            try:
                lines = []
                async with aiofiles.open(self.log_file, "r", encoding="utf-8") as f:
                    async for line in f:
                        line_stripped = line.strip()
                        if not line_stripped:
                            continue
                        try:
                            entry = json.loads(line_stripped)
                            if entry.get("url") != url:
                                lines.append(line_stripped)
                        except json.JSONDecodeError:
                            lines.append(line_stripped)
                async with aiofiles.open(self.log_file, "w", encoding="utf-8") as f:
                    await f.write("\n".join(lines) + "\n")
            except Exception as e:
                logging.warning(f"Failed to remove {url} from JSONL: {e}")

    def save_pending_queue(self, urls):
        """Atomically persist the pending queue to disk for resume on next run."""
        tmp = "pending_queue.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(urls), f)
        os.replace(tmp, "pending_queue.json")

    def load_pending_queue(self):
        """Load and delete the pending queue file from a previous interrupted run."""
        path = "pending_queue.json"
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                urls = json.load(f)
            os.remove(path)
            return urls
        except Exception as e:
            print(f"Warning: could not load pending queue: {e}")
            return []

    def clear_pending_queue(self):
        """Remove the pending queue file after a clean crawl completion."""
        try:
            os.remove("pending_queue.json")
        except FileNotFoundError:
            pass

    def close(self):
        self._save_state()
