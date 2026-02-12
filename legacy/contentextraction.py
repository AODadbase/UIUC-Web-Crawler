import asyncio
import aiohttp
from lxml import html as lxml_html
from lxml import etree
import logging
from datetime import datetime

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

class PageParser:
    def __init__(self, html_content, url):
        self.url = url
        self.original_html = html_content
        # Parse HTML string into an lxml element tree
        self.tree = lxml_html.fromstring(html_content)
        
    def clean_noise(self):
        """Remove boilerplate elements such as navigation, footer, and ads."""
        # 1. Define noisy elements to remove
        noise_xpaths = [
            '//script', '//style', '//noscript', 
            '//nav', '//footer', '//header', '//aside',
            '//div[contains(@class, "nav")]',       # Many legacy sites use class="nav"
            '//div[contains(@class, "footer")]',    # Many legacy sites use class="footer"
            '//div[contains(@class, "sidebar")]',   # Sidebar content
            '//div[contains(@id, "sidebar")]'
        ]
        
        # 2. Remove matching elements from the tree
        clean_tree = self.tree
        
        for xpath in noise_xpaths:
            elements = clean_tree.xpath(xpath)
            for el in elements:
                if el.getparent() is not None:
                    el.getparent().remove(el)
                    
        return clean_tree

    def extract_metadata(self):
        """Extract basic metadata such as URL, title, time, and description."""
        tree = self.tree
        
        # 1. Title (prefer meta og:title, then <title>)
        title = tree.xpath('//meta[@property="og:title"]/@content')
        if not title:
            title = tree.xpath('//title/text()')
        title = title[0].strip() if title else "No Title"

        # 2. Published time (heuristic; sites use many formats)
        publish_time = "Unknown"
        # Try meta article:published_time
        meta_time = tree.xpath('//meta[@property="article:published_time"]/@content')
        if meta_time:
            publish_time = meta_time[0]
        else:
            # Example: fall back to <time> elements
            time_tags = tree.xpath('//time/@datetime')
            if time_tags:
                publish_time = time_tags[0]

        # 3. Description/summary
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
        """Extract main textual content from the cleaned page."""
        # 1. Clean noisy elements
        clean_tree = self.clean_noise()
        
        # 2. Extract remaining text
        # method='text' concatenates text from all descendants
        # encoding='unicode' ensures a string is returned
        content = lxml_html.tostring(clean_tree, method='text', encoding='unicode')
        
        # 3. Normalize whitespace and remove empty lines
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        clean_text = "\n".join(lines)
        
        return clean_text

async def test_single_page():
    # Use a representative news/blog page for testing
    target_url = "https://www.python.org/about/" 
    
    print(f"Downloading test page: {target_url} ...")
    async with aiohttp.ClientSession() as session:
        async with session.get(target_url) as response:
            html = await response.text()
            
            # Start parsing
            parser = PageParser(html, target_url)
            
            # 1. Extract metadata
            meta = parser.extract_metadata()
            print("\n[Metadata report]")
            print(f"Title: {meta['title']}")
            print(f"Time:  {meta['publish_time']}")
            print(f"Desc:  {meta['description']}")
            
            # 2. Extract cleaned main content
            content = parser.extract_content()
            print("\n[Clean content sample (top 500 chars)]")
            print("-" * 40)
            print(content[:500] + "...") # Print only the first 500 characters
            print("-" * 40)
            print(f"Total Length: {len(content)} chars")

if __name__ == "__main__":
    asyncio.run(test_single_page())