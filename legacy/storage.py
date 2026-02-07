import os
import json
import aiofiles
from urllib.parse import urlparse
import logging

# UIUC 专属分类关键词库
# 这里的逻辑是：如果正文或URL里包含这些词，就归入该类
UIUC_CATEGORIES = {
    "academics": [
        "course", "syllabus", "curriculum", "major", "minor", "degree", 
        "gpa", "credit", "academic", "catalog", "registrar", "honors",
        "grainger", "las", "engineering" 
    ],
    "housing": [
        "housing", "dorm", "residence hall", "apartment", "lease", "rent", 
        "sherman", "daniels", "illini tower", "private certified", "roommate", "dining"
    ],
    "financial": [
        "scholarship", "tuition", "aid", "grant", "loan", "cost", "bursar", "fee", "funding"
    ],
    "career": [
        "career", "internship", "job", "resume", "handshake", "recruiting", "fair"
    ],
    "student_life": [
        "rso", "club", "union", "activity", "gym", "arc", "crce", "wellness", "health"
    ],
    "research": [
        "research", "lab", "publication", "thesis", "professor", "faculty"
    ]
}

class ContentClassifier:
    def classify(self, url, text):
        """
        基于简单的加权算法判断文章分类
        """
        scores = {k: 0 for k in UIUC_CATEGORIES.keys()}
        text_lower = text.lower()
        url_lower = url.lower()

        for category, keywords in UIUC_CATEGORIES.items():
            for word in keywords:
                # 权重策略：
                # 1. URL 里有关键词？权重 +5 (URL通常最准确，例如 housing.illinois.edu)
                if word in url_lower:
                    scores[category] += 5
                
                # 2. 标题或正文里有关键词？权重 +1
                # 简单的词频统计，限制最大贡献，防止长文干扰
                count = text_lower.count(word)
                scores[category] += min(count, 10) 

        # 找出分值最高的分类
        best_category = max(scores, key=scores.get)
        
        # 如果最高分也是0，归为 "general"
        if scores[best_category] == 0:
            return "general"
            
        return best_category

class StorageManager:
    def __init__(self, base_dir="uiuc_knowledge_base"):
        self.base_dir = base_dir
        self.classifier = ContentClassifier()

    async def save(self, data_dict):
        """
        自动分类并保存为 MD 和 JSONL
        :param data_dict: 包含 url, title, content, publish_time 的字典
        """
        # 1. 判别分类
        category = self.classifier.classify(data_dict['url'], data_dict['title'] + " " + data_dict['content'])
        
        # 2. 准备目录: uiuc_knowledge_base/housing/
        save_dir = os.path.join(self.base_dir, category)
        os.makedirs(save_dir, exist_ok=True)
        
        # 3. 生成文件名 (基于URL的哈希或最后一部分，防止文件名过长)
        filename_base = self._clean_filename(data_dict['url'])
        
        # --- 保存 Markdown (给人看，给 RAG 索引看) ---
        md_path = os.path.join(save_dir, f"{filename_base}.md")
        await self._write_markdown(md_path, data_dict, category)
        
        # --- 保存 JSONL (给微调训练看，所有数据追加到一个文件) ---
        # 通常 JSONL 我们放在根目录的一个大文件里，或者按分类分文件
        jsonl_path = os.path.join(self.base_dir, "full_dataset.jsonl")
        await self._append_jsonl(jsonl_path, data_dict, category)
        
        return category

    def _clean_filename(self, url):
        """把 URL 变成合法的文件名"""
        parsed = urlparse(url)
        # 取 path 部分，比如 /about/contact -> about_contact
        name = parsed.path.strip("/").replace("/", "_")
        if not name:
            name = "index"
        # 加上域名防止冲突
        domain = parsed.netloc.replace(".", "_")
        return f"{domain}_{name}"[:100] # 截断防止文件名过长

    async def _write_markdown(self, path, data, category):
        """生成结构清晰的 Markdown"""
        content = f"""# {data['title']}

> **Source URL**: {data['url']}
> **Category**: {category}
> **Crawl Time**: {data['crawl_time']}

---

{data['content']}
"""
        async with aiofiles.open(path, mode='w', encoding='utf-8') as f:
            await f.write(content)

    async def _append_jsonl(self, path, data, category):
        """追加写入 JSONL"""
        record = {
            "url": data['url'],
            "text": f"Title: {data['title']}\nContent: {data['content']}", # 拼接成适合 LLM 训练的格式
            "meta": {
                "title": data['title'],
                "category": category,
                "timestamp": data['publish_time']
            }
        }
        async with aiofiles.open(path, mode='a', encoding='utf-8') as f:
            await f.write(json.dumps(record, ensure_ascii=False) + "\n")

# --- 测试代码 ---
async def test_storage():
    manager = StorageManager()
    
    # 模拟一条 Housing 数据
    dummy_housing = {
        "url": "https://housing.illinois.edu/living-options/residence-halls",
        "title": "Residence Halls | University Housing",
        "content": "Our residence halls offer a vibrant community. ISR and PAR are popular choices for freshmen...",
        "publish_time": "2023-10-01",
        "crawl_time": "2023-10-01"
    }
    
    # 模拟一条 CS 课程数据
    dummy_course = {
        "url": "https://cs.illinois.edu/academics/courses/cs440",
        "title": "CS 440: Artificial Intelligence",
        "content": "This course introduces the basic ideas and techniques underlying the design of intelligent computer systems.",
        "publish_time": "2023-08-15",
        "crawl_time": "2023-10-01"
    }

    print("💾 正在归档测试数据...")
    cat1 = await manager.save(dummy_housing)
    print(f"✅ Data 1 saved to category: [{cat1}]")
    
    cat2 = await manager.save(dummy_course)
    print(f"✅ Data 2 saved to category: [{cat2}]")
    
    print("\n请查看 'uiuc_knowledge_base' 文件夹，看看结构是否符合预期。")

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_storage())