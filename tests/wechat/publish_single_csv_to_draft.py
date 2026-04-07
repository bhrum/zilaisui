import asyncio
import os
import sys
import csv

# 防止在 Windows 终端下 print 时抛出 unicode 编码错误
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv

# 加载配置
load_dotenv()
os.environ["WECHAT_ENABLED"] = "true"
os.environ["WECHAT_HEADLESS"] = "false"  # 强制显示浏览器，以便扫码和观察进度
os.environ["WECHAT_MIN_DELAY"] = "2.0"
os.environ["WECHAT_MAX_DELAY"] = "4.0"

from wechat_publisher.browser import get_wechat_browser
from wechat_publisher.publisher import WeChatPublisher
from utils.image_generator import generate_cover_image

async def main():
    print("=== 开始单条发布到草稿测试 ===")
    
    csv_path = r"e:\自动化\AIstudioProxyAPI\sucai\梵文陀罗尼音译结果(全部) (3).csv"
    if not os.path.exists(csv_path):
        print(f"[FAIL] 找不到CSV文件: {csv_path}")
        return
        
    print(f"1. 读取CSV文件: {csv_path}")
    row_to_publish = None
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_to_publish = row
            break # 仅读取第一条数据

    if not row_to_publish:
        print("[FAIL] CSV文件为空")
        return
        
    title = row_to_publish.get('标题', '未命名')
    sanskrit = row_to_publish.get('梵文原文', '')
    pinyin = row_to_publish.get('最终音译', '')
    
    content_markdown = f"""# {title}

## 梵文原文
{sanskrit}

## 最终音译
{pinyin}
"""
    
    print(f"预备发布内容:\n标题: {title}\n内容概览: {content_markdown[:100]}...\n")
    
    print("生成封面图片...")
    cover_path = generate_cover_image(title)
    print(f"封面图片已生成: {cover_path}")

    browser = get_wechat_browser()
    
    print("2. 启动浏览器...")
    launched = await browser.launch(headless=False)
    if not launched:
        print("[FAIL] 浏览器启动失败")
        return

    print("3. 执行登录 (如果弹出二维码，请使用微信扫码)...")
    logged_in = await browser.login(timeout_seconds=120)
    if not logged_in:
        print("[FAIL] 登录失败或超时")
        await browser.close()
        return

    print(f"[OK] 登录成功! 当前账号: {browser.account_name}")
    
    publisher = WeChatPublisher(browser)
    
    print(f"4. 开始发布文章到草稿: {title}")
    result = await publisher.publish_article(
        title=title,
        content_markdown=content_markdown,
        author="Sanskrit Bot",
        mode="draft",  # 仅存草稿
        cover_image_path=cover_path,
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
