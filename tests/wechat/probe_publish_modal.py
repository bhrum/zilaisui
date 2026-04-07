import asyncio
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv

load_dotenv()
os.environ["WECHAT_HEADLESS"] = "true"  # Can be headless for just dumping HTML

from wechat_publisher.browser import get_wechat_browser
import wechat_publisher.selectors as sel

async def main():
    print("Starting probe...")
    browser = get_wechat_browser()
    await browser.launch(headless=True)
    await browser.login(timeout_seconds=60)
    
    page = browser.page
    token = browser.token
    url = sel.MP_NEW_ARTICLE_URL.format(token=token)
    
    print(f"Navigating to {url}...")
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)
    
    print("Filling basic content to satisfy publish requirements...")
    await page.fill("#title", "Test Title for Modal Probe")
    await page.evaluate("""() => {
        let ed = document.querySelector('.ProseMirror[contenteditable="true"]');
        if(ed) { ed.innerHTML = "<p>Test Content</p>"; ed.dispatchEvent(new Event('input', {bubbles: true})); }
    }""")
    
    print("Clicking '发表' (Publish) button at the bottom...")
    # There are multiple buttons with '发表', the one at the bottom is usually `.js_send` or similar
    # Let's try to click the one that brings up the modal
    try:
        await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const pbtn = btns.find(b => b.textContent && b.textContent.includes('发表'));
            if(pbtn) pbtn.click();
        }""")
        await asyncio.sleep(3)
    except Exception as e:
        print(f"Error clicking publish: {e}")
        
    print("Dumping HTML...")
    html = await page.content()
    with open("publish_modal_probe.html", "w", encoding="utf-8") as f:
        f.write(html)
        
    print("Probe finished. HTML saved to publish_modal_probe.html")
    await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
