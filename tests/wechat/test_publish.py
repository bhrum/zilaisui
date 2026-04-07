"""
WeChat Publisher - Unified Test Suite
=====================================
Single entry point for all WeChat automation testing.

Usage:
    # Full end-to-end publish test (default: save as draft)
    .venv\\Scripts\\python.exe tests/wechat/test_publish.py

    # Quick cover-image-only test
    .venv\\Scripts\\python.exe tests/wechat/test_publish.py --cover-only

    # Probe mode: inspect DOM at each phase, no actual publish
    .venv\\Scripts\\python.exe tests/wechat/test_publish.py --probe

    # Cleanup old debug artifacts
    .venv\\Scripts\\python.exe tests/wechat/test_publish.py --cleanup
"""
import asyncio
import argparse
import glob
import os
import shutil
import sys
import time
from datetime import datetime

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# ── Output Directories ──
# All debug output goes into timestamped run directories under tests/wechat/runs/
RUNS_DIR = os.path.join(os.path.dirname(__file__), "runs")
LATEST_LINK = os.path.join(os.path.dirname(__file__), "latest_run")

def create_run_dir():
    """Create a timestamped run directory and update 'latest_run' pointer."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(RUNS_DIR, ts)
    os.makedirs(run_dir, exist_ok=True)
    # Update latest_run to point to this run
    if os.path.exists(LATEST_LINK):
        if os.path.isdir(LATEST_LINK) and not os.path.islink(LATEST_LINK):
            shutil.rmtree(LATEST_LINK)
        elif os.path.islink(LATEST_LINK):
            os.remove(LATEST_LINK)
    # On Windows, use a file that stores the path instead of symlink
    with open(LATEST_LINK + ".txt", "w") as f:
        f.write(run_dir)
    return run_dir

async def save_state(page, run_dir, name, desc=""):
    """Save screenshot + html at a checkpoint."""
    print(f"  [{name}] {desc}")
    await page.screenshot(path=os.path.join(run_dir, f"{name}.png"))
    html = await page.content()
    with open(os.path.join(run_dir, f"{name}.html"), "w", encoding="utf-8") as f:
        f.write(html)

# ═══════════════════════════════════════════════════════════════════════════
# TEST: Full End-to-End Publish
# ═══════════════════════════════════════════════════════════════════════════
async def test_full_publish(run_dir, headless=False):
    """Full article publish test: title + content + cover + save draft."""
    os.environ["WECHAT_ENABLED"] = "true"
    os.environ["WECHAT_HEADLESS"] = str(headless).lower()
    os.environ["WECHAT_MIN_DELAY"] = "1.5"
    os.environ["WECHAT_MAX_DELAY"] = "3.0"

    from wechat_publisher.browser import get_wechat_browser
    from wechat_publisher.publisher import WeChatPublisher

    print("\n" + "=" * 60)
    print("TEST: Full End-to-End Publish (Draft Mode)")
    print(f"Run dir: {run_dir}")
    print("=" * 60)

    browser = get_wechat_browser()

    print("\n[1] Launching browser...")
    if not await browser.launch(headless=headless):
        print("[FAIL] Browser launch failed")
        return False

    print("[2] Logging in (scan QR if needed)...")
    if not await browser.login(timeout_seconds=120):
        print("[FAIL] Login failed")
        await browser.close()
        return False

    print(f"[OK] Logged in as: {browser.account_name}")

    publisher = WeChatPublisher(browser)
    test_id = os.urandom(2).hex()
    test_title = f"自动化发文测试 {test_id}"
    test_content = """# 自动化测试文章

这是一篇通过 Playwright 自动化脚本生成的测试文章。

## 功能检查点
- [x] Markdown 标题解析
- [x] 加粗：**这里是加粗文本**
- [x] 列表格式
- [x] 封面图自动选择

> 这是一段引用文本，用于测试微信样式渲染。

```python
print("Hello WeChat!")
```
"""
    cover_path = os.path.join(PROJECT_ROOT, "test_cover.jpg")
    if not os.path.exists(cover_path):
        print(f"[WARN] Cover image not found: {cover_path}")
        cover_path = None

    print(f"\n[3] Publishing article: {test_title}")
    t0 = time.time()
    result = await publisher.publish_article(
        title=test_title,
        content_markdown=test_content,
        author="Automation Test",
        mode="draft",
        cover_image_path=cover_path,
    )
    elapsed = time.time() - t0

    # Copy result screenshot into run dir
    if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
        shutil.copy2(result["screenshot_path"], os.path.join(run_dir, "result_final.png"))

    # Copy all phase screenshots from wechat_auth into run dir
    auth_dir = os.path.join(PROJECT_ROOT, "wechat_auth")
    for f in glob.glob(os.path.join(auth_dir, "cover_*.png")):
        shutil.copy2(f, run_dir)

    print(f"\n{'=' * 60}")
    print(f"RESULT: {'PASS' if result['success'] else 'FAIL'}")
    print(f"Message: {result['message']}")
    print(f"Mode: {result['mode']}")
    print(f"Time: {elapsed:.1f}s")
    print(f"Artifacts: {run_dir}")
    print(f"{'=' * 60}")

    await asyncio.sleep(5)
    await browser.close()

    # Reset singleton
    import wechat_publisher.browser as bmod
    bmod._wechat_browser = None

    return result["success"]

# ═══════════════════════════════════════════════════════════════════════════
# TEST: Cover Image Only
# ═══════════════════════════════════════════════════════════════════════════
async def test_cover_only(run_dir, headless=False):
    """Test only the cover image selection flow."""
    os.environ["WECHAT_HEADLESS"] = str(headless).lower()

    from wechat_publisher.browser import WeChatBrowser
    from wechat_publisher.content_formatter import markdown_to_wechat_html
    import wechat_publisher.selectors as sel

    print("\n" + "=" * 60)
    print("TEST: Cover Image Selection Flow Only")
    print(f"Run dir: {run_dir}")
    print("=" * 60)

    browser = WeChatBrowser()
    if not await browser.launch(headless=headless):
        return False
    if not await browser.login(120):
        return False

    page = browser.page
    token = browser.token

    # Navigate to editor
    print("[1] Navigating to editor...")
    editor_url = sel.MP_NEW_ARTICLE_URL.format(token=token)
    await page.goto(editor_url, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(6)

    # Fill minimal content
    print("[2] Filling minimal title + content...")
    title_el = await page.wait_for_selector("#title, input[name='title']", timeout=15000)
    if title_el:
        await title_el.fill(f"cover test {os.urandom(2).hex()}")
    await page.evaluate("""(html) => {
        const e = document.querySelector('.ProseMirror') || document.querySelector('[contenteditable="true"]');
        if (e) { e.innerHTML = html; e.dispatchEvent(new Event('input', {bubbles: true})); }
    }""", markdown_to_wechat_html("# Test\n\nCover test content."))
    await asyncio.sleep(2)
    await save_state(page, run_dir, "00_editor", "Editor loaded")

    # Run cover flow
    print("[3] Running cover image flow...")
    from wechat_publisher.publisher import WeChatPublisher
    pub = WeChatPublisher(browser)
    cover_path = os.path.join(PROJECT_ROOT, "test_cover.jpg")
    await pub._upload_cover_image(page, cover_path if os.path.exists(cover_path) else "")

    await save_state(page, run_dir, "99_final", "After cover flow")

    # Copy phase screenshots
    auth_dir = os.path.join(PROJECT_ROOT, "wechat_auth")
    for f in glob.glob(os.path.join(auth_dir, "cover_*.png")):
        shutil.copy2(f, run_dir)

    # Check result
    cover_text = await page.evaluate("""() => {
        const el = document.querySelector('.js_cover_btn_area, .select-cover__btn');
        return el ? el.textContent.trim() : '';
    }""")
    success = '拖拽或选择封面' not in cover_text

    print(f"\nRESULT: {'PASS - Cover set!' if success else 'FAIL - Cover not set'}")
    print(f"Artifacts: {run_dir}")

    await browser.close()
    return success

# ═══════════════════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════════════════
def cleanup_old_artifacts():
    """Remove scattered debug files from project root and wechat_auth."""
    print("\n" + "=" * 60)
    print("CLEANUP: Removing scattered debug artifacts")
    print("=" * 60)

    # Root-level debug files
    root_patterns = [
        "debug_*.py", "debug_*.png", "debug_*.html", "debug_*.json",
        "parse_menu.py", "test_debug.py", "test_debug2.py",
    ]
    removed = 0
    for pat in root_patterns:
        for f in glob.glob(os.path.join(PROJECT_ROOT, pat)):
            print(f"  Removing: {os.path.basename(f)}")
            os.remove(f)
            removed += 1

    # debug_probe directory
    probe_dir = os.path.join(PROJECT_ROOT, "debug_probe")
    if os.path.isdir(probe_dir):
        print(f"  Removing: debug_probe/ ({len(os.listdir(probe_dir))} files)")
        shutil.rmtree(probe_dir)
        removed += 1

    # wechat_auth: remove old result_* and debug_* (keep state.json and cover_*)
    auth_dir = os.path.join(PROJECT_ROOT, "wechat_auth")
    if os.path.isdir(auth_dir):
        for f in os.listdir(auth_dir):
            fp = os.path.join(auth_dir, f)
            if f == "state.json":
                continue
            if f.startswith("result_") or f.startswith("debug_") or f.startswith("error_") or f.endswith(".html"):
                print(f"  Removing: wechat_auth/{f}")
                os.remove(fp)
                removed += 1

    print(f"\nRemoved {removed} files/dirs. Project is clean.")

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="WeChat Publisher Test Suite")
    parser.add_argument("--cover-only", action="store_true", help="Test cover image flow only")
    parser.add_argument("--probe", action="store_true", help="Probe mode: inspect DOM, no publish")
    parser.add_argument("--cleanup", action="store_true", help="Remove old debug artifacts")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    args = parser.parse_args()

    if args.cleanup:
        cleanup_old_artifacts()
        return

    run_dir = create_run_dir()

    if args.cover_only:
        success = asyncio.run(test_cover_only(run_dir, headless=args.headless))
    else:
        success = asyncio.run(test_full_publish(run_dir, headless=args.headless))

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
