import asyncio
import sys

print(f"Python Path: {sys.executable}")

# 1. Test aiohttp
try:
    import aiohttp
    print("✅ aiohttp: Installed")
except ImportError:
    print("❌ aiohttp: NOT FOUND")

# 2. Test lxml
try:
    from lxml import etree
    print("✅ lxml: Installed")
except ImportError:
    print("❌ lxml: NOT FOUND")

# 3. Test Playwright
try:
    from playwright.async_api import async_playwright
    print("✅ playwright: Installed")
except ImportError:
    print("❌ playwright: NOT FOUND")

async def test_browser():
    """Test whether the browser can start."""
    print("Testing Browser Launch...")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("http://www.example.com")
            title = await page.title()
            print(f"✅ Browser works. Page title: {title}")
            await browser.close()
    except Exception as e:
        print(f"❌ Browser failed: {e}")

if __name__ == "__main__":
    # Optionally enable uvloop on non-Windows platforms
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