import asyncio
import os
import sys
import csv
from dotenv import load_dotenv

# 防止终端因打印特殊梵文字符而导致报错
sys.stdout.reconfigure(encoding='utf-8')

# 加载配置
load_dotenv()
os.environ["WECHAT_ENABLED"] = "true"
os.environ["WECHAT_HEADLESS"] = "false"  # 强制显示浏览器，保证需要扫码时可见
os.environ["WECHAT_MIN_DELAY"] = "2.0"
os.environ["WECHAT_MAX_DELAY"] = "4.0"

from wechat_publisher.browser import get_wechat_browser
from wechat_publisher.publisher import WeChatPublisher
from utils.image_generator import generate_cover_image

async def main():
    print("=== 开始批量发布到草稿 ===")
    
    csv_path = r"e:\自动化\AIstudioProxyAPI\sucai\梵文陀罗尼音译结果(全部) (3).csv"
    if not os.path.exists(csv_path):
        print(f"[FAIL] 找不到CSV文件: {csv_path}")
        return
        
    print(f"1. 读取CSV文件: {csv_path}")
    articles = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get('标题', '').strip()
            # 根据需要跳过空标题
            if not title:
                continue
            articles.append(row)

    if not articles:
        print("[FAIL] 没有读取到任何需发布的内容")
        return
        
    print(f"总计读取到 {len(articles)} 条内容待发布。")
    print("2. 启动浏览器环境...")

    browser = get_wechat_browser()
    launched = await browser.launch(headless=False)
    if not launched:
        print("[FAIL] 浏览器启动失败")
        return

    print("3. 检查登录状态...")
    logged_in = await browser.login(timeout_seconds=120)
    if not logged_in:
        print("[FAIL] 登录失败或超时，退出程序。")
        await browser.close()
        return

    print(f"[OK] 登录成功! 当前账号: {browser.account_name}")
    
    publisher = WeChatPublisher(browser)
    success_count = 0
    fail_count = 0
    
    print("\n======= 开始执行批量发布 =======\n")
    for i, row in enumerate(articles, start=1):
        title = row.get('标题', '未命名')
        sanskrit = row.get('梵文原文', '')
        pinyin = row.get('最终音译', '')
        
        content_markdown = f"""# {title}\n\n## 梵文原文\n{sanskrit}\n\n## 最终音译\n{pinyin}\n"""
        print(f"[{i}/{len(articles)}] 正在处理: {title}")
        
        # 自动生成封面
        cover_path = generate_cover_image(title)
        
        result = await publisher.publish_article(
            title=title,
            content_markdown=content_markdown,
            author="Sanskrit Bot",
            mode="draft",  # 仅存草稿
            cover_image_path=cover_path,
        )
        
        if result.get("success"):
            print(f"  ✅ 成功保存为草稿")
            success_count += 1
        else:
            print(f"  ❌ 失败: {result.get('message')}")
            fail_count += 1
            
        print("-" * 50)
        # 每篇文章之间留一点时间避免操作过于频繁
        await asyncio.sleep(5)
        
    print(f"\n=== 批量操作结束 ===")
    print(f"总计: {len(articles)}, 成功: {success_count}, 失败: {fail_count}")
    
    print("\n等待 15 秒后关闭浏览器...")
    await asyncio.sleep(15)
    await browser.close()
    print("程序已完全退出。")

if __name__ == "__main__":
    asyncio.run(main())
