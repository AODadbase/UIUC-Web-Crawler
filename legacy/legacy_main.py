import asyncio
import aiohttp
import aiofiles
import os
import json
import logging
from urllib.parse import urlparse, urljoin
from datetime import datetime
from lxml import html as lxml_html

# =================配置区域=================
# 目标根域名
ROOT_DOMAIN = "illinois.edu"
# 最大爬取页面数 (防止硬盘写满，测试建议 100-500)
MAX_PAGES_TOTAL = 100 
# 并发数量 (建议 5-10，太高会被封 IP)
CONCURRENCY = 5
# 数据存储目录
DATA_DIR = "uiuc_knowledge_base"

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    datefmt='%H:%M:%S'
)
# =========================================

class StorageManager:
    """负责数据的分类与落地存储"""
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.categories = {
            "academics": ["course", "syllabus", "curriculum", "major", "minor", "degree", "gpa", "credit", "catalog", "class"],
            "housing": ["housing", "dorm", "residence", "hall", "apartment", "lease", "dining", "roommate"],
            "financial": ["scholarship", "tuition", "aid", "grant", "loan", "cost", "fee", "payment"],
            "career": ["career", "internship", "job", "resume", "handshake", "recruiting"],
            "research": ["research", "lab", "publication", "thesis", "professor", "faculty", "project"],
            "news": ["news", "blog", "event", "calendar", "update", "story"]
        }

    def classify(self, url, text):
        scores = {k: 0 for k in self.categories.keys()}
        text_lower = text.lower()
        url_lower = url.lower()
        
        for category, keywords in self.categories.items():
            for word in keywords:
                if word in url_lower: scores[category] += 5 # URL 权重高
                scores[category] += text_lower.count(word)
        
        best = max(scores, key=scores.get)
        return best if scores[best] > 2 else "uncategorized" # 阈值过滤

    async def save_data(self, data):
        # 1. 自动分类
        category = self.classify(data['url'], data['title'] + " " + data['content'])
        
        # 2. 创建目录
        save_dir = os.path.join(self.base_dir, category)
        os.makedirs(save_dir, exist_ok=True)
        
        # 3. 保存 Markdown (人类可读/RAG)
        safe_name = self._clean_filename(data['url'])
        md_path = os.path.join(save_dir, f"{safe_name}.md")
        
        md_content = f"""# {data['title']}

> **Source**: {data['url']}
> **Category**: {category}
> **Date**: {data['timestamp']}

---

{data['content']}
"""
        async with aiofiles.open(md_path, 'w', encoding='utf-8') as f:
            await f.write(md_content)

        # 4. 追加 JSONL (LLM 训练)
        jsonl_path = os.path.join(self.base_dir, "full_dataset.jsonl")
        record = {
            "url": data['url'],
            "meta": {"title": data['title'], "category": category},
            "text": md_content # 这里直接存带 Markdown 格式的文本对模型理解结构更好
        }
        async with aiofiles.open(jsonl_path, 'a', encoding='utf-8') as f:
            await f.write(json.dumps(record, ensure_ascii=False) + "\n")
            
        return category

    def _clean_filename(self, url):
        parsed = urlparse(url)
        name = parsed.path.strip("/").replace("/", "_")
        domain = parsed.netloc.replace(".", "_")
        if not name: name = "index"
        return f"{domain}_{name}"[:100]

class UIUCPageParser:
    """核心清洗逻辑 (已验证通过)"""
    def __init__(self, html_text, url):
        self.url = url
        try:
            self.tree = lxml_html.fromstring(html_text)
        except:
            self.tree = None

    def parse(self):
        if self.tree is None: return None
        
        # 1. 清洗噪音 (保留 table, ul, li)
        noise_xpaths = [
            '//script', '//style', '//noscript', '//nav', '//footer', '//header', '//aside',
            '//div[contains(@class, "cookie")]', '//div[contains(@id, "cookie")]',
            '//a[contains(@class, "skip-link")]'
        ]
        for xpath in noise_xpaths:
            for el in self.tree.xpath(xpath):
                if el.getparent() is not None: el.getparent().remove(el)

        # 2. 提取标题
        titles = self.tree.xpath('//meta[@property="og:title"]/@content') or self.tree.xpath('//title/text()')
        title = titles[0].strip() if titles else "No Title"

        # 3. 提取正文 (Text Density)
        content = lxml_html.tostring(self.tree, method='text', encoding='unicode', with_tail=False)
        # 压缩空行
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        clean_text = "\n".join(lines)

        if len(clean_text) < 100: return None # 忽略无效页面

        return {
            "url": self.url,
            "title": title,
            "content": clean_text,
            "timestamp": datetime.now().isoformat()
        }

class UnifiedCrawler:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.visited = set()
        self.storage = StorageManager(DATA_DIR)
        self.stats = {"scanned": 0, "saved": 0, "errors": 0}

    async def start(self):
        print(f"🚀 启动爬虫 | 目标: {ROOT_DOMAIN} | 存储: {DATA_DIR}")
        
        async with aiohttp.ClientSession() as session:
            # Phase 1: 子域名注入
            await self.inject_subdomains(session)
            
            # Phase 2: 并发爬取
            workers = [asyncio.create_task(self.worker(session, i)) for i in range(CONCURRENCY)]
            
            # 等待队列完成
            await self.queue.join()
            
            # 取消 Worker
            for w in workers: w.cancel()
            
        print("\n" + "="*40)
        print(f"🏆 任务完成!")
        print(f"📄 扫描链接: {self.stats['scanned']}")
        print(f"💾 成功归档: {self.stats['saved']}")
        print(f"❌ 失败/跳过: {self.stats['errors']}")
        print("="*40)

    async def inject_subdomains(self, session):
        print("🔍 正在查询 crt.sh 获取子域名...")
        try:
            url = f"https://crt.sh/?q=%.{ROOT_DOMAIN}&output=json"
            async with session.get(url, timeout=20) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    added = 0
                    for entry in data:
                        # 简单的去重逻辑
                        sub = entry['name_value'].split('\n')[0].replace('*.', '')
                        if sub.endswith(ROOT_DOMAIN) and sub not in self.visited:
                            # 构造 URL
                            full_url = f"https://{sub}"
                            self.queue.put_nowait(full_url)
                            self.visited.add(full_url) # 标记为已入队
                            added += 1
                    print(f"✅ 注入了 {added} 个子域名入口!")
        except Exception as e:
            print(f"⚠️ 子域名查询失败 (使用默认入口): {e}")
            await self.queue.put(f"https://www.{ROOT_DOMAIN}")

    async def worker(self, session, worker_id):
        while True:
            try:
                url = await self.queue.get()
                
                # 终止条件
                if self.stats['saved'] >= MAX_PAGES_TOTAL:
                    self.queue.task_done()
                    continue

                self.stats['scanned'] += 1
                
                # 执行处理
                await self.process_page(session, url)
                
                # 模拟一点点延迟 (Politeness)
                await asyncio.sleep(0.5) 
                self.queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception:
                self.stats['errors'] += 1
                self.queue.task_done()

    async def process_page(self, session, url):
        try:
            async with session.get(url, timeout=10) as response:
                if response.status != 200: return

                # 1. 下载
                html = await response.text()
                
                # 2. 清洗
                parser = UIUCPageParser(html, url)
                data = parser.parse()
                
                if not data: return # 页面太短或解析失败

                # 3. 存储
                cat = await self.storage.save_data(data)
                self.stats['saved'] += 1
                logging.info(f"💾 [Saved-{cat}] {data['title'][:30]}...")

                # 4. 发现新链接 (仅在同域下深挖)
                if self.stats['saved'] < MAX_PAGES_TOTAL:
                    # 重新利用 parser 里的 tree
                    if parser.tree is not None:
                        for link in parser.tree.xpath('//a/@href'):
                            full_url = urljoin(url, link)
                            parsed = urlparse(full_url)
                            
                            # 过滤规则: 必须是 illinois.edu 子域，且是 http 协议
                            if parsed.netloc.endswith(ROOT_DOMAIN) and parsed.scheme in ['http', 'https']:
                                if full_url not in self.visited:
                                    # 简单的文件后缀过滤
                                    if not full_url.endswith(('.pdf', '.jpg', '.png', '.css', '.js')):
                                        self.visited.add(full_url)
                                        self.queue.put_nowait(full_url)

        except Exception as e:
            # logging.warning(f"Error processing {url}: {e}")
            pass

if __name__ == "__main__":
    crawler = UnifiedCrawler()
    asyncio.run(crawler.start())