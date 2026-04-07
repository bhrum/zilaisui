import asyncio
import os
import sys
from dotenv import load_dotenv

# 加载配置
load_dotenv()
os.environ["WECHAT_ENABLED"] = "true"
os.environ["WECHAT_HEADLESS"] = "false"  # 强制显示浏览器，以便扫码和观察进度
# 增大一点延时方便观察
os.environ["WECHAT_MIN_DELAY"] = "2.0"
os.environ["WECHAT_MAX_DELAY"] = "4.0"

from wechat_publisher.browser import get_wechat_browser
from wechat_publisher.publisher import WeChatPublisher

async def main():
    print("=== 开始微信公众号自动化发文测试 ===")
    browser = get_wechat_browser()
    
    print("1. 启动浏览器...")
    launched = await browser.launch(headless=False)
    if not launched:
        print("[FAIL] 浏览器启动失败")
        return

    print("2. 执行登录 (如果弹出二维码，请使用微信扫码)...")
    # 设置 120 秒超时等待扫码
    logged_in = await browser.login(timeout_seconds=120)
    if not logged_in:
        print("[FAIL] 登录失败或超时")
        await browser.close()
        return

    print(f"[OK] 登录成功! 当前账号: {browser.account_name}")
    
    publisher = WeChatPublisher(browser)
    
    test_title = f"自动化发文测试 {os.urandom(2).hex()}"
    test_content = """# 自动化测试文章

这是一篇通过 Playwright 自动化脚本生成的测试文章。

## 功能检查点
- [x] Markdown 标题解析
- [x] 加粗：**这里是加粗文本**
- [x] 列表格式
- [ ] 封面图（本次不测试）

> 这是一段引用文本，用于测试微信样式渲染。

```python
print("Hello WeChat!")
```
"""
    
    print(f"3. 开始发布测试文章: {test_title}")
    result = await publisher.publish_article(
        title=test_title,
        content_markdown=test_content,
        author="Automation Test",
        mode="draft",  # 仅存草稿
        cover_image_path="test_cover.jpg",
    )
    
    print("\n=== 操作结果 ===")
    print(result)
    
    screenshot = result.get("screenshot_path")
    if screenshot and os.path.exists(screenshot):
        print(f"\n📸 正在为您自动打开操作结束后的截图: {screenshot}")
        try:
            os.startfile(screenshot)
        except Exception:
            pass
    else:
        print("\n⚠️ 未能获取到截图，可能是执行过程中出错。")
    
    print("\n等待 15 秒后关闭浏览器，您可以直接在浏览器或截图中观察结果...")
    await asyncio.sleep(15)
    await browser.close()
    print("测试结束。")

if __name__ == "__main__":
    asyncio.run(main())
