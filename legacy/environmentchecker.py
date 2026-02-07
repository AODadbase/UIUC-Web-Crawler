import asyncio
import sys

print(f"Python Path: {sys.executable}")

# 1. 测试 aiohttp
try:
    import aiohttp
    print("✅ aiohttp: Installed")
except ImportError:
    print("❌ aiohttp: NOT FOUND")

# 2. 测试 lxml
try:
    from lxml import etree
    print("✅ lxml: Installed")
except ImportError:
    print("❌ lxml: NOT FOUND")

# 3. 测试 playwright
try:
    from playwright.async_api import async_playwright
    print("✅ playwright: Installed")
except ImportError:
    print("❌ playwright: NOT FOUND")

async def test_browser():
    """测试浏览器能否启动"""
    print("Testing Browser Launch...")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("http://www.example.com")
            title = await page.title()
            print(f"✅ Browser Work! Page Title: {title}")
            await browser.close()
    except Exception as e:
        print(f"❌ Browser Failed: {e}")

if __name__ == "__main__":
    # 如果你也装了 Windows/Mac 的 uvloop 可以打开，否则忽略
    if sys.platform != 'win32':
        try:
            import uvloop
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        except ImportError:
            pass
            
    try:
        asyncio.run(test_browser())
    except NameError:
        print("Skipping browser test due to import error.")