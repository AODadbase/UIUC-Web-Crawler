import asyncio
import aiohttp
import aiofiles
import os
import json
import logging
import hashlib
import signal
import socket
import trafilatura
import lxml.html
from urllib.parse import urlparse, urljoin, urldefrag
from datetime import datetime
import time
from database import StorageManager
from middleware import RequestMiddleware
from playwright.async_api import async_playwright

# ================= Configuration =================
ROOT_DOMAIN = "illinois.edu"
MAX_PAGES_TOTAL = 300
CONCURRENCY = 10          # main crawler workers (1 worker ≈ 1 proxy IP)
DATA_DIR = "uiuc_knowledge_base"

POLITENESS_DELAY = 2.0
VISITED_TTL_DAYS = 7
VISITED_TTL_SECONDS = VISITED_TTL_DAYS * 86400
MAX_RETRIES_PER_DOMAIN = 4
RETRY_BACKOFF = [30, 60, 120]   # seconds to wait before retry 1, 2, 3

# Phase 3 — stale-content prune: lightweight HEAD requests, no proxy needed
PRUNE_CONCURRENCY   = 10
# Phase 4 — blacklist recovery: full GET through proxy pool; keep narrow to
# avoid proxy congestion and per-host rate-limit bans
RECOVERY_CONCURRENCY = 3
RECOVERY_HOST_DELAY  = 3.0  # seconds between requests to the same domain

SECURITYTRAILS_API_KEY = os.environ.get("SECURITYTRAILS_API_KEY", "")

DYNAMIC_KEYWORDS = ["courses", "schedule", "calendar", "events", "directory", "apps"]

INTERACTION_RULES = {
    "events": ".pagination-next, .load-more-btn",
    "courses": "#show-details-btn",
}

# Proper ClientTimeout objects — passing bare integers is deprecated in modern aiohttp.
FETCH_TIMEOUT   = aiohttp.ClientTimeout(total=8)
HEAD_TIMEOUT    = aiohttp.ClientTimeout(total=5)
CRTSH_TIMEOUT   = aiohttp.ClientTimeout(total=30)
WAYBACK_TIMEOUT = aiohttp.ClientTimeout(total=15)
HT_TIMEOUT      = aiohttp.ClientTimeout(total=30)
ST_TIMEOUT      = aiohttp.ClientTimeout(total=15)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    datefmt='%H:%M:%S'
)
# =========================================


class DomainRateLimiter:
    """Per-domain rate limiter that enforces a minimum interval between requests."""

    def __init__(self, delay=POLITENESS_DELAY):
        self.delay = delay
        self._last_request = {}
        self._lock = asyncio.Lock()

    async def wait(self, domain):
        # Hold the lock only long enough to read/update the timestamp.
        # Sleeping inside the lock would serialize all workers on one lock.
        async with self._lock:
            now = time.monotonic()
            last = self._last_request.get(domain, 0.0)
            wait_time = self.delay - (now - last)
            # Mark the domain as reserved before releasing the lock so
            # concurrent workers for the same domain don't both pass through.
            self._last_request[domain] = now + max(wait_time, 0)
        if wait_time > 0:
            await asyncio.sleep(wait_time)


class PlaywrightManager:
    """Wrapper around Playwright browser lifecycle and page fetching."""

    # Hard cap on simultaneous browser contexts to prevent resource exhaustion.
    MAX_CONCURRENT_CONTEXTS = 10

    def __init__(self, middleware):
        self.playwright = None
        self.browser = None
        self.middleware = middleware
        self.available = False
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_CONTEXTS)

    async def start(self):
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=True)
            self.available = True
        except Exception as e:
            logging.warning(f"Playwright failed to launch: {e}. Falling back to aiohttp-only mode.")
            self.available = False

    async def stop(self):
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass  # browser process already dead (proxy crash, OOM kill, Ctrl+C race)
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass

    async def fetch_page(self, url):
        if not self.available:
            return None
        ua = self.middleware.get_random_ua()
        proxy = self.middleware.get_playwright_proxy()

        # Semaphore bounds how many browser contexts exist at once.
        async with self._semaphore:
            context = await self.browser.new_context(
                user_agent=ua,
                proxy=proxy,
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            try:
                await page.goto(url, timeout=15000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)
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
        if not self.html_text:
            return None

        content = trafilatura.extract(
            self.html_text, include_tables=True, output_format='markdown', deduplicate=True
        )
        if not content or len(content) < 50:
            return None

        links = []
        try:
            tree = lxml.html.fromstring(self.html_text)
            raw_links = tree.xpath('//a/@href')
            for link in raw_links:
                # Produce absolute, fragment-stripped URLs here so callers
                # don't have to (and shouldn't) re-apply urljoin/urldefrag).
                full_url = urldefrag(urljoin(self.url, link))[0]
                if ROOT_DOMAIN in full_url and not full_url.endswith(('.css', '.js', '.png', '.jpg', '.pdf')):
                    links.append(full_url)
        except Exception:
            pass

        metadata = trafilatura.extract_metadata(self.html_text)
        title = metadata.title if (metadata and metadata.title) else "No Title"
        date = metadata.date if (metadata and metadata.date) else datetime.now().isoformat()

        return {
            "url": self.url,
            "title": title,
            "content": content,
            "timestamp": date,
            "links": links,
        }


class UnifiedCrawler:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.visited = {}   # {url: crawl_timestamp_float}
        self._run_start = time.time()
        self.storage = StorageManager()
        self.middleware = RequestMiddleware()
        self.pw_manager = PlaywrightManager(self.middleware)
        self.rate_limiter = DomainRateLimiter()
        self.retry_counts = {}
        self.stats = {"scanned": 0, "new": 0, "updated": 0, "skipped": 0, "deleted": 0, "blacklisted": 0}
        self._shutting_down = False
        self._shutdown_event = None  # set in start() once the event loop is running
        self._queue_persisted = False  # set True once pending queue has been saved/cleared

        self.blacklist = set()
        self._load_blacklist()

        self.dns_fail_count = {}

        # Restore visited from persisted state so a resumed crawl doesn't
        # re-fetch every URL from scratch.  Pages already in state are skipped;
        # delete crawl_state.json for a full re-crawl.
        restored = 0
        for url, data in self.storage.state.items():
            raw = data.get("last_crawled", "2000-01-01T00:00:00")
            try:
                ts = datetime.fromisoformat(raw).timestamp()
            except ValueError:
                ts = 0.0
            self.visited[url] = ts
            restored += 1
        if restored:
            print(f"Restored {restored} visited URLs from previous crawl state")

        # Restore URLs that were queued but unprocessed when the last run was
        # interrupted.  load_pending_queue() deletes the file after reading so
        # a subsequent clean run won't re-load it.
        restored_pending = 0
        for url in self.storage.load_pending_queue():
            if url not in self.visited:
                self.queue.put_nowait(url)
                self.visited[url] = 0.0   # epoch → stale, re-crawl when re-discovered
                restored_pending += 1
        if restored_pending:
            print(f"Restored {restored_pending} pending URLs from interrupted crawl")

    def _is_fresh(self, url):
        """Return True if url was crawled recently enough to skip re-queuing."""
        ts = self.visited.get(url)
        if ts is None:
            return False
        return (time.time() - ts) < VISITED_TTL_SECONDS

    def _load_blacklist(self):
        """Load blacklist entries from file, ignoring empty lines."""
        if os.path.exists("blacklist.txt"):
            with open("blacklist.txt", "r", encoding="utf-8") as f:
                for line in f:
                    entry = line.strip()
                    if entry:  # guard against empty lines adding "" to the set
                        self.blacklist.add(entry)
            print(f"Loaded {len(self.blacklist)} blacklist rules")

    async def check_dns(self, domain):
        """Check if a domain resolves via DNS."""
        MAX_DNS_FAILURES = 3
        if self.dns_fail_count.get(domain, 0) >= MAX_DNS_FAILURES:
            return False
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, socket.getaddrinfo, domain, None)
            self.dns_fail_count.pop(domain, None)
            return True
        except socket.gaierror:
            self.dns_fail_count[domain] = self.dns_fail_count.get(domain, 0) + 1
            count = self.dns_fail_count[domain]
            logging.warning(f"[DNS fail] {domain} — attempt {count}/{MAX_DNS_FAILURES}")
            return False

    async def add_to_blacklist(self, url):
        """Add a URL to the blacklist (memory and file)."""
        if url not in self.blacklist:
            self.blacklist.add(url)
            self.stats['blacklisted'] += 1
            async with aiofiles.open("blacklist.txt", "a", encoding="utf-8") as f:
                await f.write(url + "\n")
            logging.warning(f"[Blacklisted] {url}")

    async def start(self):
        print(f"Starting hybrid crawler (aiohttp + Playwright). Target domain: {ROOT_DOMAIN}")

        self._shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)

        try:
            await self.pw_manager.start()
        except Exception as e:
            logging.warning(f"Playwright startup error: {e}. Continuing with aiohttp-only mode.")

        try:
            # TCPConnector settings:
            #   limit_per_host  — prevents flooding any single server with connections
            #   ttl_dns_cache   — caches DNS results to avoid repeated lookups
            #   enable_cleanup_closed — reclaims closed sockets promptly
            connector = aiohttp.TCPConnector(
                limit=CONCURRENCY,
                limit_per_host=3,   # DomainRateLimiter enforces real pacing; cap TCP slots tightly
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
            )
            async with aiohttp.ClientSession(connector=connector) as session:
                await self.inject_subdomains(session)
                workers = [asyncio.create_task(self.worker(session, i)) for i in range(CONCURRENCY)]

                # Race: either the queue drains naturally (normal) or a shutdown
                # signal fires first (interrupted).
                queue_done   = asyncio.create_task(self.queue.join())
                shutdown_wait = asyncio.create_task(self._shutdown_event.wait())
                await asyncio.wait([queue_done, shutdown_wait], return_when=asyncio.FIRST_COMPLETED)
                queue_done.cancel()
                shutdown_wait.cancel()

                if self._shutting_down:
                    # --- Graceful shutdown path ---
                    # Snapshot queue contents before workers can drain them, then
                    # persist so the next run can resume exactly where we stopped.
                    saved = self._drain_queue()
                    self.storage.save_pending_queue(saved)
                    self._queue_persisted = True
                    logging.info(f"[Shutdown] Persisted {len(saved)} queued URLs for next run")
                    # Unblock any workers waiting on queue.get() with a sentinel.
                    for _ in range(CONCURRENCY):
                        self.queue.put_nowait(None)
                    await asyncio.gather(*workers, return_exceptions=True)
                    # Run blacklist recovery even on interrupt to keep it clean.
                    await self.recover_blacklist(session)
                else:
                    # --- Normal completion path ---
                    for w in workers:
                        w.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)
                    self.storage.clear_pending_queue()
                    self._queue_persisted = True
                    await self.prune_stale_content(session)
                    await self.recover_blacklist(session)

        finally:
            # Fallback: if an unexpected exception (CancelledError, network crash,
            # etc.) bypassed both branches above, drain and persist whatever is
            # still in the queue so the next run can resume.
            if not self._queue_persisted:
                saved = self._drain_queue()
                self.storage.save_pending_queue(saved)
                logging.warning(f"[Emergency] Persisted {len(saved)} queued URLs after abnormal exit")

            await self.pw_manager.stop()
            self.storage.close()

            print("\n" + "=" * 40)
            print("Crawl completed" if not self._shutting_down else "Crawl interrupted — state saved")
            print(f"Pages scanned:  {self.stats['scanned']}")
            print(f"New pages:      {self.stats['new']}")
            print(f"Updated pages:  {self.stats['updated']}")
            print(f"Skipped pages:  {self.stats['skipped']}")
            print(f"Deleted pages:  {self.stats['deleted']}")
            print("=" * 40)

    def _request_shutdown(self):
        if self._shutting_down:
            # Second signal: executor threads may be blocking exit — force quit.
            logging.warning("Forced exit — terminating immediately")
            os._exit(1)
        self._shutting_down = True
        logging.warning("Shutdown requested — finishing in-flight requests (Ctrl+C again to force quit)")
        if self._shutdown_event:
            self._shutdown_event.set()

    def _drain_queue(self):
        """Synchronously drain all items from the queue and return them.

        Safe to call only while the event loop is at an await point (no
        concurrent coroutines running).  task_done() is called for each item
        so the queue's internal unfinished-tasks counter stays consistent.
        """
        items = []
        while True:
            try:
                url = self.queue.get_nowait()
                self.queue.task_done()
                items.append(url)
            except asyncio.QueueEmpty:
                break
        return items

    async def inject_subdomains(self, session):
        """Seed the crawler with subdomains discovered from multiple sources with fallback."""
        print("[Phase 1] Discovering subdomains...")

        IGNORED_SUBS = [
            "auth", "login", "sso", "shibboleth", "vpn", "mail", "email",
            "outlook", "exchange", "kronos", "canvas", "compass", "moodle",
            "test", "dev", "stage", "staging", "demo", "admin", "intranet",
            "printing", "calendar", "directory", "files", "ftp", "remote",
            "cites", "help", "support", "status",
        ]

        subdomains = set()

        # --- Source 1: crt.sh (Certificate Transparency logs) ---
        for attempt in range(3):
            try:
                url = f"https://crt.sh/?q=%.{ROOT_DOMAIN}&output=json"
                async with session.get(url, timeout=CRTSH_TIMEOUT) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for entry in data:
                            sub = entry['name_value'].split('\n')[0].replace('*.', '')
                            if sub.endswith(ROOT_DOMAIN):
                                subdomains.add(sub)
                        print(f"  crt.sh: found {len(subdomains)} subdomains")
                        break
                    elif attempt < 2:
                        print(f"  crt.sh returned status {resp.status}, retrying ({attempt+1}/3)...")
                        await asyncio.sleep(2)
                    else:
                        print(f"  crt.sh failed after 3 attempts (status {resp.status})")
            except Exception as e:
                if attempt < 2:
                    print(f"  crt.sh failed: {e}, retrying ({attempt+1}/3)...")
                    await asyncio.sleep(2)
                else:
                    print(f"  crt.sh failed after 3 attempts: {e}")

        # --- Source 2: Wayback Machine CDX API ---
        try:
            wayback_url = (
                f"http://web.archive.org/cdx/search/cdx"
                f"?url=*.{ROOT_DOMAIN}&output=json&fl=original&collapse=urlkey&limit=5000"
            )
            async with session.get(wayback_url, timeout=WAYBACK_TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    before = len(subdomains)
                    for row in data[1:]:  # first row is header
                        try:
                            host = urlparse(row[0]).netloc
                            if host and host.endswith(ROOT_DOMAIN):
                                subdomains.add(host)
                        except Exception:
                            pass
                    print(f"  Wayback CDX: added {len(subdomains) - before} new subdomains")
        except Exception as e:
            print(f"  Wayback CDX failed: {e}")

        # --- Source 3: HackerTarget Host Search ---
        try:
            ht_url = f"https://api.hackertarget.com/hostsearch/?q={ROOT_DOMAIN}"
            async with session.get(ht_url, timeout=HT_TIMEOUT) as resp:
                if resp.status == 200:
                    before = len(subdomains)
                    text = await resp.text()
                    if "error" not in text.lower() and "API count exceeded" not in text:
                        for line in text.strip().splitlines():
                            host = line.split(",")[0].strip().lower()
                            if host and host.endswith(ROOT_DOMAIN):
                                subdomains.add(host)
                        print(f"  HackerTarget: added {len(subdomains) - before} new subdomains")
                    else:
                        print(f"  HackerTarget: {text.strip()}")
                else:
                    print(f"  HackerTarget returned status {resp.status}")
        except Exception as e:
            print(f"  HackerTarget failed: {e}")

        # --- Source 4: SecurityTrails API ---
        if SECURITYTRAILS_API_KEY:
            try:
                st_url = f"https://api.securitytrails.com/v1/domain/{ROOT_DOMAIN}/subdomains"
                st_headers = {"APIKEY": SECURITYTRAILS_API_KEY, "Accept": "application/json"}
                async with session.get(st_url, headers=st_headers, timeout=ST_TIMEOUT) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        before = len(subdomains)
                        for prefix in data.get("subdomains", []):
                            subdomains.add(f"{prefix}.{ROOT_DOMAIN}")
                        print(f"  SecurityTrails: added {len(subdomains) - before} new subdomains")
                    else:
                        print(f"  SecurityTrails returned status {resp.status}")
            except Exception as e:
                print(f"  SecurityTrails failed: {e}")
        else:
            print("  SecurityTrails skipped (set SECURITYTRAILS_API_KEY env var to enable)")

        # --- Source 5: DNS bruteforce fallback ---
        if len(subdomains) < 10:
            COMMON_PREFIXES = [
                "www", "cs", "ece", "math", "physics", "chem", "engr", "lib",
                "housing", "academics", "admissions", "grad", "research",
                "news", "athletics", "engineering", "education", "law",
                "business", "medicine", "pharmacy", "arts", "music",
                "provost", "chancellor", "hr", "it", "ics", "gis",
                "data", "api", "web", "blog", "media", "studentaffairs",
            ]
            before = len(subdomains)
            candidates = [f"{p}.{ROOT_DOMAIN}" for p in COMMON_PREFIXES]
            # Use check_dns (run_in_executor) to avoid blocking the event loop.
            results = await asyncio.gather(*[self.check_dns(c) for c in candidates])
            for candidate, alive in zip(candidates, results):
                if alive:
                    subdomains.add(candidate)
            print(f"  DNS bruteforce: added {len(subdomains) - before} new subdomains")

        # --- Filter and enqueue (with DNS pre-check) ---
        seeded = 0
        filtered = [
            sub for sub in subdomains
            if sub.endswith(ROOT_DOMAIN) and not any(bad in sub for bad in IGNORED_SUBS)
        ]

        # If shutdown was requested while we were querying external APIs,
        # skip the DNS pre-check entirely.  run_in_executor threads cannot be
        # cancelled; spawning hundreds of them would block process exit.
        if self._shutting_down:
            return

        print(f"  DNS-checking {len(filtered)} candidate subdomains...")
        dns_tasks = {sub: self.check_dns(sub) for sub in filtered}
        results = await asyncio.gather(*dns_tasks.values())
        for sub, alive in zip(dns_tasks.keys(), results):
            if alive:
                full_url = f"https://{sub}"
                if not self._is_fresh(full_url):
                    self.queue.put_nowait(full_url)
                    self.visited[full_url] = time.time()
                    seeded += 1
            else:
                print(f"  Skipping dead subdomain: {sub}")

        if seeded > 0:
            print(f"Seeded {seeded} subdomains from {len(subdomains)} discovered (filtered)")
        else:
            print("All sources failed or returned 0 results, using fallback seed URLs")
            seeds = [
                f"https://www.{ROOT_DOMAIN}",
                f"https://housing.{ROOT_DOMAIN}",
                f"https://academics.{ROOT_DOMAIN}",
                f"https://cs.{ROOT_DOMAIN}",
            ]
            for seed in seeds:
                if not self._is_fresh(seed):
                    self.queue.put_nowait(seed)
                    self.visited[seed] = time.time()

    async def _fetch_with_retry(self, session, url, _domain):
        """Try fetching a URL up to MAX_RETRIES_PER_DOMAIN times with IP rotation."""
        for attempt in range(1, MAX_RETRIES_PER_DOMAIN + 1):
            headers = self.middleware.get_random_header()
            proxy = self.middleware.get_random_proxy()

            try:
                async with session.get(
                    url, headers=headers, proxy=proxy, timeout=FETCH_TIMEOUT, ssl=False
                ) as response:
                    if response.status == 200:
                        html_text = await response.text()
                        return html_text, False

                    elif response.status in [401, 403]:
                        logging.warning(f"[{response.status}] {url} — attempt {attempt}/{MAX_RETRIES_PER_DOMAIN}")
                        if attempt >= MAX_RETRIES_PER_DOMAIN:
                            await self.add_to_blacklist(url)
                            return "", False

                    elif response.status == 404:
                        logging.warning(f"[404] {url} — attempt {attempt}/{MAX_RETRIES_PER_DOMAIN}")
                        if attempt >= MAX_RETRIES_PER_DOMAIN:
                            await self.add_to_blacklist(url)
                            return "", False

                    elif response.status == 429:
                        logging.warning(f"[429 Rate limit] {url} — attempt {attempt}/{MAX_RETRIES_PER_DOMAIN}")
                        if attempt >= MAX_RETRIES_PER_DOMAIN:
                            await self.add_to_blacklist(url)
                            return "", False

                    else:
                        logging.warning(f"[HTTP {response.status}] {url} — attempt {attempt}/{MAX_RETRIES_PER_DOMAIN}")
                        if attempt >= MAX_RETRIES_PER_DOMAIN:
                            await self.add_to_blacklist(url)
                            return "", False

            except Exception as e:
                logging.warning(f"[Network error] {url} — attempt {attempt}/{MAX_RETRIES_PER_DOMAIN}: {e}")
                if attempt >= MAX_RETRIES_PER_DOMAIN:
                    await self.add_to_blacklist(url)
                    return "", True

            extra_wait = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
            logging.info(f"Waiting {extra_wait}s before retry {attempt + 1} for {url}")
            await asyncio.sleep(extra_wait)

        return "", False

    async def worker(self, session, _worker_id):
        while True:
            try:
                url = await self.queue.get()
            except asyncio.CancelledError:
                break

            # None is the sentinel put by graceful shutdown to unblock queue.get()
            if url is None:
                self.queue.task_done()
                break

            # Shutdown was set after we dequeued this URL — stop processing
            if self._shutting_down:
                self.queue.task_done()
                break

            try:
                if url in self.blacklist:
                    continue

                domain = urlparse(url).netloc

                if not await self.check_dns(domain):
                    await self.add_to_blacklist(url)
                    continue

                await self.rate_limiter.wait(domain)

                # Increment scanned here so MAX_PAGES_TOTAL is actually enforced.
                self.stats['scanned'] += 1

                use_playwright = self.pw_manager.available and any(kw in url for kw in DYNAMIC_KEYWORDS)

                html_text = ""
                network_error = False

                if use_playwright:
                    html_text = await self.pw_manager.fetch_page(url)
                else:
                    html_text, network_error = await self._fetch_with_retry(session, url, domain)

                # Fallback: if static fetch returned too little, try Playwright
                if (
                    self.pw_manager.available
                    and not use_playwright
                    and not network_error
                    and url not in self.blacklist
                    and self.dns_fail_count.get(domain, 0) < 3
                    and (not html_text or len(html_text) < 500)
                ):
                    logging.info(f"[Fallback] Static content too short, switching to Playwright: {url}")
                    html_text = await self.pw_manager.fetch_page(url)

                if html_text and len(html_text) > 100:
                    await self.process_html(html_text, url)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Worker Error: {e}")
            finally:
                self.queue.task_done()

    async def process_html(self, html, url):
        """Parse raw HTML, decide whether to store it, and enqueue new links."""
        parser = UIUCPageParser(html, url)
        data = parser.parse()
        if not data:
            return

        content_hash = hashlib.md5(data['content'].encode('utf-8')).hexdigest()
        should_save, status = self.storage.should_process(url, content_hash)

        if should_save:
            category = self.storage.classify(url, data['title'] + " " + data['content'])
            await self.storage.save_page(data['url'], data['title'], data['content'], data.get('links', []), category)
            self.storage.upsert_page(url, content_hash, category)
            if status == "NEW":
                self.stats['new'] += 1
                logging.info(f"[NEW] {data['title'][:30]}...")
            else:
                self.stats['updated'] += 1
                logging.info(f"[UPDATED] {data['title'][:30]}...")
        else:
            self.stats['skipped'] += 1

        if self.stats['scanned'] < MAX_PAGES_TOTAL and not self._shutting_down:
            for link in data.get('links', []):
                # Links are already absolute and fragment-stripped by UIUCPageParser;
                # do NOT re-apply urljoin/urldefrag here.
                if self._is_fresh(link) or link in self.blacklist:
                    continue
                parsed = urlparse(link)
                if parsed.netloc.endswith(ROOT_DOMAIN) and parsed.scheme in ['http', 'https']:
                    if not link.endswith(('.pdf', '.jpg', '.png', '.css', '.js')):
                        self.visited[link] = time.time()
                        self.queue.put_nowait(link)

    async def prune_stale_content(self, session):
        """Check previously seen URLs concurrently; remove content that no longer exists."""
        print("\n[Phase 3] Pruning stale content...")
        all_urls = self.storage.get_all_urls()
        suspects = [url for url in all_urls if self.visited.get(url, 0) < self._run_start]
        print(f"Found {len(suspects)} previously seen pages not visited in this run. Verifying...")

        sem = asyncio.Semaphore(PRUNE_CONCURRENCY)

        async def check_one(url):
            async with sem:
                try:
                    async with session.head(url, timeout=HEAD_TIMEOUT, allow_redirects=True, ssl=False) as resp:
                        if resp.status in (404, 410):
                            logging.warning(f"[Stale] {url} -> deleted")
                            await self.storage.delete_page(url)
                            self.stats['deleted'] += 1
                except Exception:
                    pass

        await asyncio.gather(*[check_one(url) for url in suspects])

    async def recover_blacklist(self, session):
        """Re-check blacklisted URLs concurrently with per-host throttling.

        Uses a narrow global semaphore (RECOVERY_CONCURRENCY) to avoid flooding
        the proxy pool, plus a per-domain rate limiter (RECOVERY_HOST_DELAY) to
        prevent triggering extended bans on target sites.
        """
        print(f"\n[Phase 4] Blacklist recovery — re-checking {len(self.blacklist)} blacklisted URLs...")

        recovered_urls = []
        surviving = set()
        sem = asyncio.Semaphore(RECOVERY_CONCURRENCY)
        rate_limiter = DomainRateLimiter(delay=RECOVERY_HOST_DELAY)

        async def check_one(url):
            domain = urlparse(url).netloc
            # Wait for per-domain rate limit BEFORE acquiring the semaphore slot.
            # If we slept inside the sem, a slow domain would pin a slot for the
            # entire delay period, blocking unrelated domains from running.
            await rate_limiter.wait(domain)
            async with sem:
                try:
                    headers = self.middleware.get_random_header()
                    proxy = self.middleware.get_random_proxy()
                    async with session.get(
                        url, headers=headers, proxy=proxy,
                        timeout=FETCH_TIMEOUT, ssl=False, allow_redirects=True
                    ) as resp:
                        if resp.status == 200:
                            recovered_urls.append(url)
                            logging.info(f"[Recovered] {url} — now returns 200")
                        else:
                            surviving.add(url)
                except Exception:
                    surviving.add(url)

        await asyncio.gather(*[check_one(url) for url in self.blacklist])

        self.blacklist = surviving
        async with aiofiles.open("blacklist.txt", "w", encoding="utf-8") as f:
            for url in sorted(surviving):
                await f.write(url + "\n")

        print(f"  Recovered: {len(recovered_urls)} URLs (false positives removed)")
        print(f"  Still dead: {len(surviving)} URLs kept in blacklist")
        self.stats['blacklisted'] = len(surviving)

        # Persist recovered URLs so the next crawl run re-fetches their content.
        # load_pending_queue() reads-and-deletes the file (returns [] if absent),
        # so merging works correctly whether we're in the normal or interrupted path.
        if recovered_urls:
            existing = self.storage.load_pending_queue()
            merged = list(dict.fromkeys(existing + recovered_urls))  # deduplicate, preserve order
            self.storage.save_pending_queue(merged)
            print(f"  Queued {len(recovered_urls)} recovered URLs for next crawl run")


if __name__ == "__main__":
    crawler = UnifiedCrawler()
    asyncio.run(crawler.start())
