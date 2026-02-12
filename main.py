import asyncio
import aiohttp
import aiofiles
import os
import json
import logging
import hashlib
import trafilatura
from urllib.parse import urlparse, urljoin
from datetime import datetime
from database import DBManager
from middleware import RequestMiddleware
# Playwright integration for handling dynamic pages
from playwright.async_api import async_playwright

# ================= Configuration =================
ROOT_DOMAIN = "illinois.edu"
MAX_PAGES_TOTAL = 300 
CONCURRENCY = 5
DATA_DIR = "uiuc_knowledge_base"

# Keywords indicating that a page is likely dynamic and should be fetched with Playwright
DYNAMIC_KEYWORDS = ["courses", "schedule", "calendar", "events", "directory", "apps"]

# Optional click-interaction rules, mapping URL patterns to CSS selectors
# Example: if URL contains "events", try clicking ".pagination-next" or similar
INTERACTION_RULES = {
    "events": ".pagination-next, .load-more-btn", # Example: click pagination or load-more buttons
    "courses": "#show-details-btn",               # Example: expand course details
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    datefmt='%H:%M:%S'
)
# =========================================

class PlaywrightManager:
    """Wrapper around Playwright browser lifecycle and page fetching."""

    def __init__(self, middleware):
        self.playwright = None
        self.browser = None
        self.middleware = middleware

    async def start(self):
        self.playwright = await async_playwright().start()
        # Run Chromium in headless mode for better performance
        self.browser = await self.playwright.chromium.launch(headless=True)

    async def stop(self):
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()

    async def fetch_page(self, url):
        # Randomize user agent and proxy per request
        ua = self.middleware.get_random_ua()
        proxy = self.middleware.get_playwright_proxy()
        
        # Create a context bound to the chosen user agent and proxy
        context = await self.browser.new_context(
            user_agent=ua,
            proxy=proxy, # None is ignored by Playwright
            viewport={'width': 1920, 'height': 1080}
        )
        
        page = await context.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="networkidle")
            
            # TODO: apply INTERACTION_RULES here if active clicking is required
            
            content = await page.content()
            return content
        except Exception as e:
            # logging.error(...)
            return None
        finally:
            await page.close()
            # Always close context to free associated resources
            await context.close()
    

class StorageManager:
    def __init__(self, base_dir):
        self.base_dir = base_dir
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

    def classify(self, url, text):
        scores = {k: 0 for k in self.categories.keys()}
        text_lower = text.lower()
        url_lower = url.lower()
        for category, keywords in self.categories.items():
            for word in keywords:
                if word in url_lower: scores[category] += 5
                scores[category] += text_lower.count(word)
        best = max(scores, key=scores.get)
        if scores[best] <= 2: 
            return "uncategorized"
        return best

    async def save_data(self, data, category):
        save_dir = os.path.join(self.base_dir, category)
        os.makedirs(save_dir, exist_ok=True)
        safe_name = self._clean_filename(data['url'])
        md_path = os.path.join(save_dir, f"{safe_name}.md")
        md_content = f"# {data['title']}\n\n> URL: {data['url']}\n> Category: {category}\n> Updated: {data['timestamp']}\n\n---\n\n{data['content']}"
        async with aiofiles.open(md_path, 'w', encoding='utf-8') as f:
            await f.write(md_content)

    async def delete_data(self, url, category):
        safe_name = self._clean_filename(url)
        filename = f"{safe_name}.md"
        deleted = False
        for cat in os.listdir(self.base_dir):
            cat_dir = os.path.join(self.base_dir, cat)
            if os.path.isdir(cat_dir):
                target_file = os.path.join(cat_dir, filename)
                if os.path.exists(target_file):
                    os.remove(target_file)
                    deleted = True
        return deleted

    def _clean_filename(self, url):
        parsed = urlparse(url)
        name = parsed.path.strip("/").replace("/", "_")
        domain = parsed.netloc.replace(".", "_")
        if not name: name = "index"
        return f"{domain}_{name}"[:100]

class UIUCPageParser:
    """Parse UIUC-related HTML pages into structured text and links."""
    def __init__(self, html_text, url):
        self.url = url
        self.html_text = html_text

    def parse(self):
        if not self.html_text: return None
        
        # Extract main content using Trafilatura
        content = trafilatura.extract(
            self.html_text, include_tables=True, output_format='markdown', deduplicate=True
        )
        if not content or len(content) < 50: return None

        # Extract links so the crawler can continue following pages
        import lxml.html
        try:
            tree = lxml.html.fromstring(self.html_text)
            links = tree.xpath('//a/@href')
        except:
            links = []

        metadata = trafilatura.extract_metadata(self.html_text)
        title = metadata.title if (metadata and metadata.title) else "No Title"
        date = metadata.date if (metadata and metadata.date) else datetime.now().isoformat()

        return {
            "url": self.url,
            "title": title,
            "content": content,
            "timestamp": date,
            "links": links
        }

class UnifiedCrawler:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.visited = set()
        self.storage = StorageManager(DATA_DIR)
        self.db = DBManager()
        self.middleware = RequestMiddleware()
        self.pw_manager = PlaywrightManager(self.middleware)
        self.stats = {"scanned": 0, "new": 0, "updated": 0, "skipped": 0, "deleted": 0, "blacklisted": 0}
        
        # Initialize in-memory blacklist
        self.blacklist = set()
        self._load_blacklist()

    def _load_blacklist(self):
        """Load blacklist entries from file"""
        if os.path.exists("blacklist.txt"):
            with open("blacklist.txt", "r", encoding="utf-8") as f:
                for line in f:
                    self.blacklist.add(line.strip())
            print(f"Loaded {len(self.blacklist)} blacklist rules")

    async def add_to_blacklist(self, url):
        """Add a URL to the blacklist (memory and file)"""
        if url not in self.blacklist:
            self.blacklist.add(url)
            self.stats['blacklisted'] += 1
            # Asynchronously append to file to avoid blocking
            async with aiofiles.open("blacklist.txt", "a", encoding="utf-8") as f:
                await f.write(url + "\n")
            logging.warning(f"[Blacklisted] {url}")

    async def start(self):
        print(f"Starting hybrid crawler (aiohttp + Playwright). Target domain: {ROOT_DOMAIN}")
        
        # Start Playwright browser engine
        await self.pw_manager.start()

        async with aiohttp.ClientSession() as session:
            await self.inject_subdomains(session)
            workers = [asyncio.create_task(self.worker(session, i)) for i in range(CONCURRENCY)]
            await self.queue.join()
            for w in workers: w.cancel()
            await self.prune_stale_content(session)
            
        # Shut down Playwright browser engine
        await self.pw_manager.stop()
        self.db.close()
        
        print("\n" + "="*40)
        print("Crawl completed")
        print(f"Pages scanned: {self.stats['scanned']}")
        print(f"New pages: {self.stats['new']}")
        print(f"Updated pages: {self.stats['updated']}")
        print(f"Skipped pages: {self.stats['skipped']}")
        print(f"Deleted pages: {self.stats['deleted']}")
        print("="*40)

    async def inject_subdomains(self, session):
        """Seed the crawler with subdomains discovered from crt.sh or fallbacks."""
        print("[Phase 1] Discovering subdomains...")
        crt_success = False
        try:
            url = f"https://crt.sh/?q=%.{ROOT_DOMAIN}&output=json"
            async with session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for entry in data:
                        sub = entry['name_value'].split('\n')[0].replace('*.', '')
                        if sub.endswith(ROOT_DOMAIN):
                            full_url = f"https://{sub}"
                            if full_url not in self.visited:
                                self.queue.put_nowait(full_url)
                                self.visited.add(full_url)
                    crt_success = True
                    print("Seeded subdomains from crt.sh")
        except:
            pass

        if not crt_success or self.queue.qsize() == 0:
            print("Using fallback seed URLs")
            seeds = [f"https://www.{ROOT_DOMAIN}", f"https://housing.{ROOT_DOMAIN}", f"https://academics.{ROOT_DOMAIN}", f"https://cs.{ROOT_DOMAIN}"]
            for seed in seeds:
                if seed not in self.visited:
                    self.queue.put_nowait(seed)
                    self.visited.add(seed)

    async def worker(self, session, worker_id):
        while True:
            try:
                url = await self.queue.get()

                # ============================================================
                # [Upgrade 1] Pre-flight check: blacklist
                # If the URL is already in the blacklist, skip it immediately.
                # ============================================================
                if url in self.blacklist:
                    self.queue.task_done()
                    continue

                # Prepare request headers and proxy
                headers = self.middleware.get_random_header()
                proxy = self.middleware.get_random_proxy()
                
                use_playwright = False
                for kw in DYNAMIC_KEYWORDS:
                    if kw in url:
                        use_playwright = True
                        break
                
                html_text = ""
                
                # --- Execution Path A: Playwright (Heavy) ---
                if use_playwright:
                    html_text = await self.pw_manager.fetch_page(url)
                    # If Playwright returns None (failed rendering), we might consider blacklisting it
                    if html_text is None:
                        # Optional: aggressive blacklisting on render failure
                        pass

                # --- Execution Path B: Aiohttp (Fast) ---
                else:
                    try:
                        # ssl=False helps avoid some proxy-related certificate issues
                        async with session.get(url, headers=headers, proxy=proxy, timeout=15, ssl=False) as response:
                            if response.status == 200:
                                html_text = await response.text()

                            # ============================================================
                            # [Upgrade 2] Permanent blacklist for invalid pages
                            # 401/403: Forbidden (IP ban or login required)
                            # 404: Not Found (dead link)
                            # ============================================================
                            elif response.status in [401, 403, 404]:
                                logging.warning(f"[Auto-blacklist] Status {response.status} - {url}")
                                await self.add_to_blacklist(url)
                            
                            # ============================================================
                            # [Upgrade 3] Retry logic for temporary rate limits
                            # 429: Too Many Requests (rate limited) -> put back in queue
                            # ============================================================
                            elif response.status == 429:
                                logging.warning(f"[Rate limit] 429 - {url} (retrying later)")
                                self.queue.put_nowait(url) 

                    except Exception as e:
                        # Network errors (timeout, connection reset) are not necessarily blacklist-worthy
                        # We just skip them for now
                        pass
                
                # --- Fallback Logic ---
                # If static fetch returns too little content (e.g. < 500 chars), it might be JS-protected.
                # Retry with Playwright.
                if not use_playwright and (not html_text or len(html_text) < 500):
                     # logging.info(f"[Fallback] Static content too short, switching to Playwright: {url}")
                     html_text = await self.pw_manager.fetch_page(url)

                # ============================================================
                 # [Upgrade 4] Final validation and storage
                # ============================================================
                if html_text and len(html_text) > 100:
                    await self.process_html(html_text, url)
                else:
                    # If it's STILL empty after Fallback, it's likely a broken/empty page.
                    # We can choose to blacklist it to save future resources.
                    if html_text is not None: # Only blacklist if we actually got a response (empty string)
                        # logging.warning(f"[Empty page] Blacklisting {url}")
                        # await self.add_to_blacklist(url)
                        pass

                await asyncio.sleep(0.5)
                self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                # logging.error(f"Worker Error: {e}")
                self.queue.task_done()

    async def process_html(self, html, url):
        """Parse raw HTML, decide whether to store it, and enqueue new links."""
        parser = UIUCPageParser(html, url)
        data = parser.parse()
        
        if not data: return 

        content_hash = hashlib.md5(data['content'].encode('utf-8')).hexdigest()
        should_save, status = self.db.should_process(url, content_hash)
        category = self.storage.classify(url, data['title'] + " " + data['content'])

        if should_save:
            await self.storage.save_data(data, category)
            self.db.upsert_page(url, content_hash, category, data['title'])
            if status == "NEW":
                self.stats['new'] += 1
                logging.info(f"[NEW] {data['title'][:20]}...")
            else:
                self.stats['updated'] += 1
                logging.info(f"[UPDATED] {data['title'][:20]}...")
        else:
            self.stats['skipped'] += 1

        if self.stats['scanned'] < MAX_PAGES_TOTAL:
            for link in data.get('links', []):
                full_url = urljoin(url, link)
                parsed = urlparse(full_url)
                if parsed.netloc.endswith(ROOT_DOMAIN) and parsed.scheme in ['http', 'https']:
                    if full_url not in self.visited:
                        if not full_url.endswith(('.pdf', '.jpg', '.png', '.css', '.js')):
                            self.visited.add(full_url)
                            self.queue.put_nowait(full_url)

    async def prune_stale_content(self, session):
        """Check previously seen URLs and remove content that no longer exists."""
        print("\n🧹 [Phase 3] 开始清理过期内容...")
        all_records = self.db.get_all_urls()
        suspects = []
        for row in all_records:
            url, category, title = row
            if url not in self.visited:
                suspects.append((url, category, title))
        print(f"🔍 发现 {len(suspects)} 个未访问页面，正在验证...")
        for url, category, title in suspects:
            try:
                async with session.head(url, timeout=5, allow_redirects=True) as resp:
                    if resp.status == 404 or resp.status == 410:
                        logging.warning(f"❌ [已失效] {title} -> 删除...")
                        await self.storage.delete_data(url, category)
                        self.db.delete_page(url)
                        self.stats['deleted'] += 1
            except:
                pass

if __name__ == "__main__":
    crawler = UnifiedCrawler()
    asyncio.run(crawler.start())