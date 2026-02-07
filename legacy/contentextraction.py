import asyncio
import aiohttp
from lxml import html as lxml_html
from lxml import etree
import logging
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(message)s')

class PageParser:
    def __init__(self, html_content, url):
        self.url = url
        self.original_html = html_content
        # 将字符串转为 lxml 的 Element 对象，这是解析的基础
        self.tree = lxml_html.fromstring(html_content)
        
    def clean_noise(self):
        """
        核心清洗逻辑：物理删除无用标签
        对应需求点 5：处理网站无用信息
        """
        # 1. 定义垃圾标签 (黑名单)
        # script/style: 代码，不是内容
        # nav/header/footer: 导航和页脚
        # aside: 侧边栏广告或推荐
        # iframe: 嵌入内容
        noise_xpaths = [
            '//script', '//style', '//noscript', 
            '//nav', '//footer', '//header', '//aside',
            '//div[contains(@class, "nav")]',       # 很多老网站用 class="nav"
            '//div[contains(@class, "footer")]',    # 很多老网站用 class="footer"
            '//div[contains(@class, "sidebar")]',   # 侧边栏
            '//div[contains(@id, "sidebar")]'
        ]
        
        # 2. 遍历并删除这些节点
        clean_tree = self.tree # 引用复制
        
        for xpath in noise_xpaths:
            # 找到所有符合的标签
            elements = clean_tree.xpath(xpath)
            for el in elements:
                # 从父节点中移除该节点 (物理切除)
                if el.getparent() is not None:
                    el.getparent().remove(el)
                    
        return clean_tree

    def extract_metadata(self):
        """
        提取元数据
        对应需求点 6：url, 标题, 分类, 时间
        """
        tree = self.tree
        
        # --- 1. 标题 (优先取 meta og:title，其次取 <title>) ---
        title = tree.xpath('//meta[@property="og:title"]/@content')
        if not title:
            title = tree.xpath('//title/text()')
        title = title[0].strip() if title else "No Title"

        # --- 2. 发布时间 (这是一个难点，不同网站写法不同) ---
        # 我们尝试几种常见的标准写法
        publish_time = "Unknown"
        # 尝试 meta article:published_time
        meta_time = tree.xpath('//meta[@property="article:published_time"]/@content')
        if meta_time:
            publish_time = meta_time[0]
        else:
            # 尝试从 json-ld 或 常见的 class 提取 (示例)
            time_tags = tree.xpath('//time/@datetime')
            if time_tags:
                publish_time = time_tags[0]

        # --- 3. 描述/摘要 ---
        desc = tree.xpath('//meta[@name="description"]/@content')
        description = desc[0].strip() if desc else ""

        return {
            "url": self.url,
            "title": title,
            "publish_time": publish_time,
            "description": description,
            "crawl_time": datetime.now().isoformat()
        }

    def extract_content(self):
        """
        提取正文
        """
        # 1. 先清洗噪音
        clean_tree = self.clean_noise()
        
        # 2. 提取剩余的文本
        # method='text' 会把所有子标签的文本连起来
        # encoding='unicode' 确保返回字符串
        content = lxml_html.tostring(clean_tree, method='text', encoding='unicode')
        
        # 3. 格式化清洗 (去掉多余的空行和空格)
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        clean_text = "\n".join(lines)
        
        return clean_text

async def test_single_page():
    # 找一个典型的新闻/博客页面测试
    target_url = "https://www.python.org/about/" 
    
    print(f"🚀 正在下载测试页面: {target_url} ...")
    async with aiohttp.ClientSession() as session:
        async with session.get(target_url) as response:
            html = await response.text()
            
            # --- 开始解析 ---
            parser = PageParser(html, target_url)
            
            # 1. 获取元数据
            meta = parser.extract_metadata()
            print("\n📊 [Metadata Report]")
            print(f"Title: {meta['title']}")
            print(f"Time:  {meta['publish_time']}")
            print(f"Desc:  {meta['description']}")
            
            # 2. 获取清洗后的正文
            content = parser.extract_content()
            print("\n📝 [Clean Content Sample (Top 500 chars)]")
            print("-" * 40)
            print(content[:500] + "...") # 只打印前500字预览
            print("-" * 40)
            print(f"Total Length: {len(content)} chars")

if __name__ == "__main__":
    asyncio.run(test_single_page())