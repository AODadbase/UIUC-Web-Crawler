import sqlite3
import logging
import json
import os
import hashlib
import aiofiles  # 必须引入这个异步文件库
from datetime import datetime

class StorageManager:
    def __init__(self, base_dir, db_path="crawler_state.db", log_mode=False):
        self.base_dir = base_dir
        self.log_mode = log_mode  # ✅ 开关
        self.log_file = "raw_crawl.jsonl"
        self.db_path = db_path
        
        # 只有在非 log_mode 下，才需要连接数据库
        # 但为了兼容性，我们总是连上，但在 log_mode 下不写入
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        self._init_table()

        # 确保输出目录存在 (Normal Mode 需要)
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
        检查页面是否需要更新。
        在 Log Mode 下，为了速度，通常跳过这个检查（或者由 C++ 后期去重）。
        但为了节省带宽，保留这个检查也是可以的。
        """
        self.cursor.execute('SELECT content_hash FROM pages WHERE url = ?', (url,))
        row = self.cursor.fetchone()
        if row is None: return True, "NEW"
        if row[0] != current_hash: return True, "UPDATED"
        return False, "UNCHANGED"

    # ✅ 新增：核心保存逻辑
    async def save_page(self, url, title, content, category="uncategorized"):
        """
        双模式保存：
        - log_mode=True: 只写 JSONL 日志 (极速)
        - log_mode=False: 写 Markdown 文件 + 更新 SQLite (传统)
        """
        
        # --- 模式 A: 极速日志模式 (配合 C++ 中间件) ---
        if self.log_mode:
            entry = {
                "url": url,
                "title": title,
                "content": content, # 这里的 content 是纯文本或 Markdown
                "timestamp": datetime.now().isoformat()
            }
            # 异步追加写入，不阻塞爬虫
            async with aiofiles.open(self.log_file, "a", encoding="utf-8") as f:
                await f.write(json.dumps(entry) + "\n")
            
            # 在这种模式下，我们通常不更新 SQLite，也不写 .md 文件
            # 因为 C++ 会读取 jsonl 并批量处理这些事情
            return "logged"

        # --- 模式 B: 传统直连模式 (Python 直接生成) ---
        
        # 1. 生成文件名
        filename = self._sanitize_filename(url)
        # 确保分类文件夹存在
        category_dir = os.path.join(self.base_dir, category)
        os.makedirs(category_dir, exist_ok=True)
        
        file_path = os.path.join(category_dir, filename)

        # 2. 写入 Markdown 文件
        md_content = f"> URL: {url}\n> Title: {title}\n> Category: {category}\n\n# {title}\n\n{content}"
        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
            await f.write(md_content)

        # 3. 更新数据库状态
        # 计算 Hash
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        self.upsert_page(url, content_hash, category, title)
        
        return "saved"

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

    # 辅助工具：清理文件名
    def _sanitize_filename(self, url):
        # 简单把 http:// 换成下划线，保留最后一段
        name = url.replace("https://", "").replace("http://", "").replace("/", "_")
        # 限制长度防止报错
        if len(name) > 100:
            name = name[:100]
        return name + ".md"