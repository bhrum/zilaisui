"""
精准探测 V2: 完整填写文章（含封面图）→ 点击发表 → 捕获原创声明弹窗 DOM
"""
import asyncio
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv

load_dotenv()
os.environ["WECHAT_ENABLED"] = "true"
os.environ["WECHAT_HEADLESS"] = "false"
os.environ["WECHAT_MIN_DELAY"] = "2.0"
os.environ["WECHAT_MAX_DELAY"] = "4.0"

from wechat_publisher.browser import get_wechat_browser
from wechat_publisher.publisher import WeChatPublisher
from utils.image_generator import generate_cover_image
import wechat_publisher.selectors as sel

async def main():
    print("=== 探测 V2: 完整文章 → 发表 → 原创弹窗 ===")
    browser = get_wechat_browser()
    await browser.launch(headless=False)
    await browser.login(timeout_seconds=120)
    
    page = browser.page
    publisher = WeChatPublisher(browser)
    
    # 1. 用 publisher 走完整流程（填标题、正文、封面），但不执行保存/发表
    token = browser.token
    url = sel.MP_NEW_ARTICLE_URL.format(token=token)
    
    print("1. 导航到编辑器...")
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(5)
    
    print("2. 填写标题...")
    await page.evaluate('''() => {
        const t = document.querySelector('#title');
        if(t) { t.value = '探测弹窗用-测试标题'; t.dispatchEvent(new Event('input', {bubbles:true})); }
    }''')
    await asyncio.sleep(1)
    
    print("3. 填写正文（含图片标签以满足发表要求）...")
    # 生成一张测试封面图
    cover_path = generate_cover_image("探测弹窗用测试标题")
    print(f"   封面图路径: {cover_path}")
    
    await page.evaluate('''() => {
        let ed = document.querySelector('.ProseMirror[contenteditable="true"]');
        if (!ed) ed = document.querySelector('[contenteditable="true"]');
        if(ed) {
            ed.innerHTML = "<p>探测测试正文</p>";
            ed.dispatchEvent(new Event('input', {bubbles: true}));
        }
    }''')
    await asyncio.sleep(2)
    
    print("4. 上传封面图片（走完整 8 Phase 流程）...")
    await publisher._upload_cover_image(page, cover_path)
    await asyncio.sleep(2)

    print("5. 截图 — 发表前状态...")
    await page.screenshot(path="wechat_auth/probe_v2_before.png")
    
    print("6. 点击发表按钮...")
    await page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button, a'));
        for (const btn of btns) {
            const text = (btn.innerText || btn.textContent || '').trim();
            if (text === '发表') {
                btn.click();
                return true;
            }
        }
        return false;
    }''')
    
    print("7. 等待 5 秒让弹窗渲染完成...")
    await asyncio.sleep(5)
    
    print("8. 截图 — 发表后弹窗状态...")
    await page.screenshot(path="wechat_auth/probe_v2_after.png")
    
    print("9. 深度扫描所有可见 dialog/弹窗...")
    dialog_info = await page.evaluate('''() => {
        const results = [];
        // 搜索所有 weui-desktop-dialog
        const dialogs = document.querySelectorAll('.weui-desktop-dialog');
        for (const el of dialogs) {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            // 检查是否真的可见
            if (rect.width > 100 && rect.height > 100 && style.display !== 'none') {
                results.push({
                    className: el.className,
                    id: el.id || '',
                    rect: {w: Math.round(rect.width), h: Math.round(rect.height), x: Math.round(rect.x), y: Math.round(rect.y)},
                    display: style.display,
                    visibility: style.visibility,
                    innerText: el.innerText.substring(0, 800),
                    outerHTML_head: el.outerHTML.substring(0, 4000),
                });
            }
        }
        
        // 如果没找到标准 dialog，搜索所有大的浮层
        if (results.length === 0) {
            const overlays = document.querySelectorAll('div[class*="dialog"], div[class*="modal"], div[class*="popup"], div[class*="layer"]');
            for (const el of overlays) {
                const rect = el.getBoundingClientRect();
                if (rect.width > 200 && rect.height > 150) {
                    results.push({
                        className: el.className,
                        id: el.id || '',
                        rect: {w: Math.round(rect.width), h: Math.round(rect.height)},
                        innerText: el.innerText.substring(0, 500),
                        outerHTML_head: el.outerHTML.substring(0, 3000),
                    });
                }
            }
        }
        return results;
    }''')
    
    print(f"\n{'='*60}")
    print(f"发现 {len(dialog_info)} 个可见弹窗:")
    print(f"{'='*60}")
    
    for i, info in enumerate(dialog_info):
        print(f"\n--- 弹窗 #{i} ---")
        print(f"  className: {info['className']}")
        print(f"  id: {info.get('id','')}")
        print(f"  rect: {info['rect']}")
        print(f"  display: {info.get('display','?')}, visibility: {info.get('visibility','?')}")
        print(f"  innerText:\n{info['innerText']}")
        print(f"\n  outerHTML (前4000字):")
        print(f"{info['outerHTML_head']}")
    
    if not dialog_info:
        print("\n⚠️ 仍未检测到任何弹窗，可能发表条件还未满足")
        # 检查页面是否有错误提示
        err = await page.evaluate('''() => {
            const tips = document.querySelectorAll('.tips_global_err, .global_error, .weui-desktop-toast');
            return Array.from(tips).map(t => t.innerText).join(' | ');
        }''')
        if err:
            print(f"   页面错误提示: {err}")
    
    print("\n探测完成。浏览器保持打开 120 秒让你观察...")
    await asyncio.sleep(120)
    await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
