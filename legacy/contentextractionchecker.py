import asyncio
import aiohttp
from lxml import html as lxml_html
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(message)s')

class UIUCPageParser:
    def __init__(self, html_content, url):
        self.url = url
        self.original_html = html_content
        try:
            self.tree = lxml_html.fromstring(html_content)
        except:
            self.tree = None
            
    def clean_noise(self):
        """
        针对教育类网站优化的清洗逻辑
        """
        if self.tree is None: return None
        
        clean_tree = self.tree # 引用

        # 1. 强力去除：绝对是噪音的标签
        # 注意：这里我们保留了 table, ul, li，因为课程表和清单很重要
        noise_xpaths = [
            '//script', '//style', '//noscript', 
            '//nav', '//footer', '//header', 
            '//aside',  # 侧边栏通常是无关链接
            '//div[contains(@id, "cookie")]', # Cookie 弹窗
            '//div[contains(@class, "cookie")]',
            '//div[contains(@class, "menu")]', # 菜单
            '//div[contains(@class, "navigation")]',
            '//a[contains(@class, "skip-link")]', # "Skip to content" 按钮
            '//div[@role="navigation"]'
        ]
        
        for xpath in noise_xpaths:
            elements = clean_tree.xpath(xpath)
            for el in elements:
                if el.getparent() is not None:
                    el.getparent().remove(el)
                    
        return clean_tree

    def extract_content(self):
        # 1. 执行清洗
        self.clean_noise()
        
        if self.tree is None: return "Parse Error"

        # 2. 提取正文
        # method='text' 会提取所有子节点的文本
        # 我们使用 " " (空格) 连接，防止单词粘连，然后自己在下面处理换行
        content = lxml_html.tostring(self.tree, method='text', encoding='unicode', with_tail=False)
        
        # 3. 文本密度重组 (Text Density Reformating)
        # 很多网页提取出来会有大量空行，我们需要压缩它们
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            # 只有当这一行有实质内容时才保留
            if len(stripped) > 0:
                lines.append(stripped)
        
        return "\n".join(lines)

    def extract_title(self):
        if self.tree is None: return "No Title"
        # 优先取 og:title (通常更干净)
        titles = self.tree.xpath('//meta[@property="og:title"]/@content')
        if not titles:
            titles = self.tree.xpath('//title/text()')
        return titles[0].strip() if titles else "No Title"

async def check_url_quality(session, url, type_tag):
    print(f"\n🔎 正在测试类型: [{type_tag}]")
    print(f"   URL: {url}")
    
    try:
        async with session.get(url, timeout=10) as response:
            if response.status != 200:
                print(f"❌ 访问失败: {response.status}")
                return

            html = await response.text()
            original_len = len(html)
            
            # --- 执行核心清洗 ---
            parser = UIUCPageParser(html, url)
            clean_text = parser.extract_content()
            title = parser.extract_title()
            # ------------------

            clean_len = len(clean_text)
            reduction = (1 - clean_len/original_len) * 100
            
            print(f"✅ 解析成功: {title}")
            print(f"   原始大小: {original_len} chars -> 清洗后: {clean_len} chars (噪音减少 {reduction:.1f}%)")
            
            print("-" * 20 + " 内容预览 (前 500 字) " + "-" * 20)
            print(clean_text[:500])
            print("..." + "\n" + "-" * 60)
            
            # 自动质量判断建议
            if "Skip to main content" in clean_text:
                print("⚠️ 警告: 清洗不彻底，检测到导航栏残留词汇。")
            if clean_len < 200:
                print("⚠️ 警告: 内容过短，可能被误删或页面为空。")

    except Exception as e:
        print(f"❌ 发生错误: {e}")

async def main():
    # 我们选取 UIUC 三个极具代表性的页面进行“压力测试”
    test_targets = [
        # 1. 文本类 (Housing): 测试 ISR 宿舍介绍页，包含大量正文和图片描述
        ("Housing", "https://housing.illinois.edu/living-communities/halls/isr"),
        
        # 2. 结构化数据类 (Academics): 
        # 这是一个极好的测试样本！它包含巨大的课程表格 (Table)。
        # 我们的目标是：确保 'Composition I', 'Calculus I' 这些表格内容没被当成噪音删掉。
        ("Academics", "https://catalog.illinois.edu/undergraduate/engineering/computer-science-bs/"),
        
        # 3. 混合类 (Research): 包含列表和简介
        ("Research", "https://siebelschool.illinois.edu/research/areas")
    ]

    async with aiohttp.ClientSession() as session:
        for tag, url in test_targets:
            await check_url_quality(session, url, tag)

if __name__ == "__main__":
    asyncio.run(main())