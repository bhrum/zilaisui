import asyncio
import os
import sys
import csv
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 防止终端因打印特殊梵文字符而导致报错
sys.stdout.reconfigure(encoding='utf-8')

# 加载配置
load_dotenv()
os.environ["WECHAT_ENABLED"] = "true"
os.environ["WECHAT_HEADLESS"] = "false"  # 强制显示浏览器，保证需要扫码时可见
os.environ["WECHAT_MIN_DELAY"] = "2.0"
os.environ["WECHAT_MAX_DELAY"] = "4.0"

import logging
from wechat_publisher.browser import get_wechat_browser
from wechat_publisher.publisher import WeChatPublisher
from utils.image_generator import generate_cover_image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

def round_time_to_next_5_minutes(dt: datetime) -> datetime:
    # WeChat requires scheduled times to be multiples of 5 minutes
    discard = timedelta(minutes=dt.minute % 5,
                        seconds=dt.second,
                        microseconds=dt.microsecond)
    dt -= discard
    if discard >= timedelta(0):
        dt += timedelta(minutes=5)
    return dt

async def main():
    print("=== 开始 断点续传+定时发表 批量任务 ===")
    
    csv_path = r"e:\自动化\AIstudioProxyAPI\sucai\梵文陀罗尼音译结果(全部) (3).csv"
    if not os.path.exists(csv_path):
        print(f"[FAIL] 找不到CSV文件: {csv_path}")
        return
        
    print(f"1. 读取CSV文件并分析状态: {csv_path}")
    articles = []
    fieldnames = []
    
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        if '发布状态' not in fieldnames:
            fieldnames.append('发布状态')
            
        for row in reader:
            title = row.get('标题', '').strip()
            if not title:
                continue
            articles.append(row)

    if not articles:
        print("[FAIL] 没有读取到任何内容")
        return
        
    # Calculate interval automatically over roughly 1 week
    # e.g., 790 items / 7 days ~ 1 item every 12-13 mins. Round to 10 or 15.
    target_total_minutes = 7 * 24 * 60
    interval_minutes = max(5, (target_total_minutes // len(articles) // 5) * 5)
    if interval_minutes == 0:
        interval_minutes = 5
        
    # Start time logic:
    # Base it on current time rounded to next 5 minutes.
    base_time = round_time_to_next_5_minutes(datetime.now())
    
    pending_articles = []
    already_published = 0
    
    for i, row in enumerate(articles):
        status = str(row.get('发布状态', '') or '')
        if status.startswith('已定时发表'):
            already_published += 1
        else:
            # Assign a calculated scheduled time based on order in the pending list
            scheduled_time = base_time + timedelta(minutes=len(pending_articles) * interval_minutes)
            row['__calculated_time'] = scheduled_time
            pending_articles.append((i, row))
            
    print(f"总计读取到 {len(articles)} 条内容。")
    print(f"已跳过 {already_published} 条已记录发送的内容。")
    print(f"剩余 {len(pending_articles)} 条待定时发表。")
    print(f"自动计算发文间隔为: {interval_minutes} 分钟。")
    
    if not pending_articles:
        print("所有内容均已发布完毕！")
        return

    print(f"首条预定发表时间: {pending_articles[0][1]['__calculated_time'].strftime('%Y-%m-%d %H:%M')}")
    print(f"末条预定发表时间: {pending_articles[-1][1]['__calculated_time'].strftime('%Y-%m-%d %H:%M')}")
    print("\n2. 启动浏览器环境...")

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
    
    print("\n======= 开始执行批量发表 =======\n")
    for task_index, (orig_index, row) in enumerate(pending_articles, start=1):
        title = row.get('标题', '未命名')
        sanskrit = row.get('梵文原文', '')
        pinyin = row.get('最终音译', '')
        publish_time = row['__calculated_time']
        
        content_markdown = f"""# {title}\n\n## 梵文原文\n{sanskrit}\n\n## 最终音译\n{pinyin}\n"""
        
        time_str = publish_time.strftime('%Y-%m-%d %H:%M')
        print(f"[{task_index}/{len(pending_articles)}] 正在处理: {title}")
        print(f"   => 计划定时时间: {time_str}")
        
        # 自动生成封面
        cover_path = generate_cover_image(title)
        
        result = await publisher.publish_article(
            title=title,
            content_markdown=content_markdown,
            author="bhrum",
            mode="schedule",  # 定时模式
            cover_image_path=cover_path,
            publish_time=publish_time
        )
        
        if result.get("success"):
            print(f"  ✅ 成功定时发表该图文！")
            success_count += 1
            # 更新状态并立即写入原 CSV
            articles[orig_index]['发布状态'] = f"已定时发表 ({time_str})"
            
            # 回写CSV
            with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for a in articles:
                    # Remove temporary internal keys before writing
                    clean_row = {k: v for k, v in a.items() if not k.startswith('__')}
                    writer.writerow(clean_row)
        else:
            print(f"  ❌ 失败: {result.get('message')}")
            fail_count += 1
            # 打印诊断信息
            debug = result.get('debug', {})
            if debug:
                print(f"  📸 截图: {debug.get('screenshot', 'N/A')}")
                if debug.get('html'):
                    print(f"  📄 HTML导出: {debug['html']}")
                if debug.get('dialog_text'):
                    print(f"  💬 弹窗内容: {debug['dialog_text'][:300]}")
                if debug.get('buttons'):
                    print(f"  🔘 页面按钮: {debug['buttons'][:5]}")
            print("\n  ⛔ 首次失败，中断批量流程以便分析问题。")
            print("  请查看上面的截图和HTML文件来诊断卡在了哪里。")
            break
            
        print("-" * 50)
        await asyncio.sleep(5)
        
    print(f"\n=== 批量操作结束 ===")
    print(f"本次运行: 剩余待处理 {len(pending_articles)}, 成功: {success_count}, 失败: {fail_count}")
    
    print("\n等待 15 秒后关闭浏览器...")
    await asyncio.sleep(15)
    await browser.close()
    print("程序已完全退出。")

if __name__ == "__main__":
    asyncio.run(main())
