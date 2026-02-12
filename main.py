import asyncio
import aiohttp
import aiofiles
import os
import json
import logging
import hashlib
import trafilatura
import lxml.html
from urllib.parse import urlparse, urljoin
from datetime import datetime
from database import StorageManager
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
            proxy=proxy,
            viewport={'width': 1920, 'height': 1080}
        )

        page = await context.new_page()
        try:
            # Shortened timeout to 15s; domcontentloaded is faster than networkidle
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")

            # Wait 1.5s for JS to render content
            await page.wait_for_timeout(1500)

            # Scroll to bottom to trigger lazy-loaded content
            # Many UIUC course listings only render on scroll
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)  # Wait 1s after scrolling

            content = await page.content()
            return content
        except Exception as e:
            logging.error(f"Playwright fetch failed: {e}")
            return None
        finally:
            await page.close()
            await context.close()

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
        links = []
        try:
            tree = lxml.html.fromstring(self.html_text)
            # Extract all href attributes and convert to absolute URLs
            raw_links = tree.xpath('//a/@href')
            for link in raw_links:
                full_url = urljoin(self.url, link)
                # Keep only same-domain links, excluding static assets
                if ROOT_DOMAIN in full_url and not full_url.endswith(('.css', '.js', '.png', '.jpg', '.pdf')):
                    links.append(full_url)
        except:
            pass

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
            logging.warning(f"⚠️ [Blacklisted] {url}")

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
        self.storage.close()

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

        # Subdomain blacklist: skip subdomains containing these keywords
        # to avoid crawling internal login pages, VPNs, etc.
        IGNORED_SUBS = [
            "auth", "login", "sso", "shibboleth", "vpn", "mail", "email",
            "outlook", "exchange", "kronos", "canvas", "compass", "moodle",
            "test", "dev", "stage", "staging", "demo", "admin", "intranet",
            "printing", "calendar", "directory", "files", "ftp", "remote",
            "cites", "help", "support", "status"
        ]

        crt_success = False
        try:
            # Timeout set to 10s to avoid long waits
            url = f"https://crt.sh/?q=%.{ROOT_DOMAIN}&output=json"
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for entry in data:
                        sub = entry['name_value'].split('\n')[0].replace('*.', '')

                        # Filter out unwanted subdomains
                        should_skip = False
                        for bad_word in IGNORED_SUBS:
                            if bad_word in sub:
                                should_skip = True
                                break

                        if not should_skip and sub.endswith(ROOT_DOMAIN):
                            full_url = f"https://{sub}"
                            if full_url not in self.visited:
                                self.queue.put_nowait(full_url)
                                self.visited.add(full_url)

                    crt_success = True
                    print("✅ Seeded subdomains from crt.sh (Filtered)")
        except Exception as e:
            print(f"⚠️ crt.sh failed: {e}")
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
                        async with session.get(url, headers=headers, proxy=proxy, timeout=3, ssl=False) as response:
                            if response.status == 200:
                                html_text = await response.text()

                            # ============================================================
                            # [Upgrade 2] Permanent blacklist for invalid pages
                            # 401/403: Forbidden (IP ban or login required)
                            # 404: Not Found (dead link)
                            # ============================================================
                            elif response.status in [401, 403, 404]:
                                logging.warning(f"⚠️ [Auto-blacklist] Status {response.status} - {url}")
                                await self.add_to_blacklist(url)

                            # ============================================================
                            # [Upgrade 3] Retry logic for temporary rate limits
                            # 429: Too Many Requests (rate limited) -> put back in queue
                            # ============================================================
                            elif response.status == 429:
                                logging.warning(f"⚠️ [Rate limit] 429 - {url} (retrying later)")
                                self.queue.put_nowait(url)

                    except Exception as e:
                        # Network errors (timeout, connection reset) are not necessarily blacklist-worthy
                        # We just skip them for now
                        pass

                # --- Fallback Logic ---
                # If static fetch returns too little content (e.g. < 500 chars), it might be JS-protected.
                # Retry with Playwright.
                if not use_playwright and (not html_text or len(html_text) < 500):
                    logging.info(f"[Fallback] Static content too short, switching to Playwright: {url}")
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
                logging.error(f"Worker Error: {e}")
                self.queue.task_done()

    async def process_html(self, html, url):
        """Parse raw HTML, decide whether to store it, and enqueue new links."""
        parser = UIUCPageParser(html, url)
        data = parser.parse()

        if not data: return

        content_hash = hashlib.md5(data['content'].encode('utf-8')).hexdigest()
        should_save, status = self.storage.should_process(url, content_hash)
        category = self.storage.classify(url, data['title'] + " " + data['content'])

        if should_save:
            await self.storage.save_page(data['url'], data['title'], data['content'], data.get('links', []), category)
            self.storage.upsert_page(url, content_hash, category, data['title'])
            if status == "NEW":
                self.stats['new'] += 1
                logging.info(f"✅ [NEW] {data['title'][:20]}...")
            else:
                self.stats['updated'] += 1
                logging.info(f"✅ [UPDATED] {data['title'][:20]}...")
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
        print("\n[Phase 3] Pruning stale content...")
        all_records = self.storage.get_all_urls()
        suspects = []
        for row in all_records:
            url, category, title = row
            if url not in self.visited:
                suspects.append((url, category, title))
        print(f"⚠️ Found {len(suspects)} previously seen pages not visited in this run. Verifying...")
        for url, category, title in suspects:
            try:
                async with session.head(url, timeout=5, allow_redirects=True) as resp:
                    if resp.status == 404 or resp.status == 410:
                        logging.warning(f"❌ [Stale] {title} -> deleted")
                        await self.storage.delete_data(url, category)
                        self.storage.delete_page(url)
                        self.stats['deleted'] += 1
            except:
                pass

if __name__ == "__main__":
    crawler = UnifiedCrawler()
    asyncio.run(crawler.start())
