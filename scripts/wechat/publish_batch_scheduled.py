import asyncio
import os
import sys
import csv
import subprocess
import time
import json
import glob

# Ensure project root is in sys.path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

os.environ["WECHAT_ENABLED"] = "true"
if "WECHAT_HEADLESS" not in os.environ:
    os.environ["WECHAT_HEADLESS"] = "false"
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
    discard = timedelta(minutes=dt.minute % 5, seconds=dt.second, microseconds=dt.microsecond)
    dt -= discard
    if discard >= timedelta(0):
        dt += timedelta(minutes=5)
    return dt

async def main():
    script_start_time = time.time()
    print("=== 开始 断点续传+定时发表 (底层物理隔离存储模式) ===")
    
    # 获取目标分配账号标识符 (如 ACC_XXX)
    target_account_id = os.environ.get("TARGET_ACCOUNT_ID")
    target_account_name = None
    
    if target_account_id:
        map_path = os.path.join(os.path.dirname(__file__), "wechat_accounts_map.json")
        try:
            with open(map_path, 'r', encoding='utf-8') as f:
                acc_map = json.load(f)
            # 反向映射 ID 到中文名
            for name, acc_id in acc_map.items():
                if acc_id == target_account_id.upper():
                    target_account_name = name
                    break
            
            if target_account_name:
                print(f"🎯 调度器设定身份: [{target_account_name}] (ID: {target_account_id})")
            else:
                print(f"⚠️ 无法在 {map_path} 查找到 ID [{target_account_id}] 对应名称！程序终止。")
                return
        except Exception as e:
            print(f"⚠️ 致命: 读取账号映射图谱出错: {e}")
            return
            
    # 【核心架构改动】确定工作扫描目录
    if target_account_name:
        work_dir = os.path.join(project_root, "sucai", target_account_name)
    else:
        # Fallback to general root if manually run without target
        print("💡 当前按【单机单账号全局模式】运行，将处理根 sucai/ 目录下的所有零散表。")
        work_dir = os.path.join(project_root, "sucai")
        
    if not os.path.isdir(work_dir):
        print(f"✋ 该账号尚未放置任何素材表。已自动退出探测。({work_dir} 目录不存在)")
        return
        
    # 动态扫描该账号专属工作目录下的所有 CSV 文件
    csv_files = glob.glob(os.path.join(work_dir, "*.csv"))
    if not csv_files:
        print(f"[FAIL] 在专属工作区未扫描到任何 CSV 文件: {work_dir}")
        return
        
    print(f"1. 盘点多文件队列: 找到 {len(csv_files)} 份物料表单...")
    articles = []
    
    # 将所有的任务抓取到一个汇总数组，但附带其来源文件路径，以便准确写入
    for curr_csv_path in csv_files:
        with open(curr_csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                title = row.get('标题', '').strip()
                if not title:
                    continue
                # 加入元数据 tracking 字段，保证回溯
                row['__source_csv_path'] = curr_csv_path
                articles.append(row)

    if not articles:
        print("💡 当前所有表单均为空或无数值，自动停止。")
        return
        
    # 为当前账号独立计算延迟分发，使得并发隔离
    target_total_minutes = 7 * 24 * 60
    interval_minutes = max(5, (target_total_minutes // len(articles) // 5) * 5)
    if interval_minutes == 0:
        interval_minutes = 5
        
    base_time = round_time_to_next_5_minutes(datetime.now())
    
    pending_articles = []
    already_published = 0
    
    for i, row in enumerate(articles):
        status = str(row.get('发布状态', '') or '')
        if status.startswith('已定时发表'):
            already_published += 1
        else:
            scheduled_time = base_time + timedelta(minutes=len(pending_articles) * interval_minutes)
            row['__calculated_time'] = scheduled_time
            pending_articles.append((i, row))
            
    print(f"属于节点 [{target_account_name or '全局'}] 的总任务数: {len(articles)}")
    print(f"已过滤 {already_published} 条发送记录，剩余 {len(pending_articles)} 条。间隔为 {interval_minutes} 分钟。")
    
    if not pending_articles:
        print("🎉 该专属物料仓下的所有名下内容均已发布完毕！工作节点退役。")
        return

    print("\n2. 初始化授权环境...")
    
    wechat_auth_json = os.environ.get("WECHAT_AUTH_STATE_JSON")
    if wechat_auth_json:
        from config.settings import WECHAT_AUTH_STATE_PATH
        print(f"注入并发节点的专属登录态凭证...")
        os.makedirs(os.path.dirname(WECHAT_AUTH_STATE_PATH), exist_ok=True)
        with open(WECHAT_AUTH_STATE_PATH, 'w', encoding='utf-8') as f:
            f.write(wechat_auth_json)

    browser = get_wechat_browser()
    is_headless = os.environ.get("WECHAT_HEADLESS", "false").lower() == "true"
    launched = await browser.launch(headless=is_headless)
    if not launched:
        print("[FAIL] 浏览器启动失败")
        return

    print("3. 校验最终登录状态...")
    logged_in = await browser.login(timeout_seconds=120)
    if not logged_in:
        print("[FAIL] 登录失败或超时，凭证可能随时失效！")
        await browser.close()
        return

    print(f"[OK] 登录成功! 确认当前实体账号身份: {browser.account_name}")
    
    publisher = WeChatPublisher(browser)
    success_count = 0
    fail_count = 0
    
    print("\n======= 下发并发跨表发表指令 =======\n")
    for task_index, (orig_index, row) in enumerate(pending_articles, start=1):
        title = row.get('标题', '未命名')
        sanskrit = row.get('梵文原文', '')
        pinyin = row.get('最终音译', '')
        publish_time = row['__calculated_time']
        source_csv_path = row['__source_csv_path']
        
        content_markdown = f"""# {title}\n\n## 梵文原文\n{sanskrit}\n\n## 最终音译\n{pinyin}\n"""
        time_str = publish_time.strftime('%Y-%m-%d %H:%M')
        
        print(f"[{task_index}/{len(pending_articles)}] 节点推送: {title}")
        print(f"   => 归属档案箱: {os.path.basename(source_csv_path)}")
        cover_path = generate_cover_image(title)
        
        elapsed = time.time() - script_start_time
        max_total_seconds = 5.5 * 3600
        time_left = max_total_seconds - elapsed
        
        if time_left <= 0:
            print("\n✋ 运行时间触及安全防线，提前转移下发队列。")
            break
            
        try:
            result = await asyncio.wait_for(
                publisher.publish_article(
                    title=title,
                    content_markdown=content_markdown,
                    author="bhrum",
                    mode="schedule",
                    cover_image_path=cover_path,
                    publish_time=publish_time
                ),
                timeout=time_left
            )
        except asyncio.TimeoutError:
            print(f"\n✋ [Timeout] 单篇文章发表超时强杀保护。")
            break
        
        if result.get("success"):
            print(f"  ✅ 并发节点状态核实: 成功定时图文！")
            success_count += 1
            
            # 使用高并发文件状态刷新回源，抵抗多 GithubAction Runner 合并冲突
            if os.environ.get("GITHUB_ACTIONS") == "true":
                try:
                    subprocess.run(["git", "config", "--global", "user.name", "github-actions[bot]"], check=True, capture_output=True)
                    subprocess.run(["git", "config", "--global", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True, capture_output=True)
                    
                    max_retries = 3
                    for attempt in range(max_retries):
                        subprocess.run(["git", "pull", "--rebase", "origin", "main"], capture_output=True)
                        
                        latest_rows = []
                        with open(source_csv_path, 'r', encoding='utf-8-sig') as f:
                            rdr = csv.DictReader(f)
                            fieldnames = rdr.fieldnames or []
                            latest_rows = list(rdr)
                            
                        # 只覆盖自己专属文件夹下的那张表！物理隔离意味着绝对安全
                        for curr_row in latest_rows:
                            if curr_row.get('标题', '').strip() == title:
                                curr_row['发布状态'] = f"已定时发表 ({time_str})"
                                break
                                
                        with open(source_csv_path, 'w', encoding='utf-8-sig', newline='') as f:
                            wrt = csv.DictWriter(f, fieldnames=fieldnames)
                            wrt.writeheader()
                            wrt.writerows(latest_rows)
                            
                        subprocess.run(["git", "add", source_csv_path], check=True, capture_output=True)
                        commit_msg = f"chore({target_account_name}): scheduled '{title}' successfully in isolated pod"
                        subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True)
                        push_res = subprocess.run(["git", "push", "origin", "HEAD:main"], capture_output=True, text=True)
                        
                        if push_res.returncode == 0:
                            print("  📦 独立物理节点进度锁定并入全球仓库。")
                            break
                        else:
                            print(f"  🔁 推送拥挤抢占锁 (第 {attempt+1} 次)，正在智能重连（物理文件不冲突）...")
                            time.sleep(3)
                except Exception as e:
                    print(f"  ⚠️ GitHub 进度回推出现异常: {e}")
            else:
                # Local edit mode
                with open(source_csv_path, 'r', encoding='utf-8-sig') as f:
                    rdr = csv.DictReader(f)
                    fieldnames = rdr.fieldnames or []
                    latest_rows = list(rdr)
                    
                for curr_row in latest_rows:
                    if curr_row.get('标题', '').strip() == title:
                        curr_row['发布状态'] = f"已定时发表 ({time_str})"
                        break
                        
                with open(source_csv_path, 'w', encoding='utf-8-sig', newline='') as f:
                    wrt = csv.DictWriter(f, fieldnames=fieldnames)
                    wrt.writeheader()
                    wrt.writerows(latest_rows)
        else:
            print(f"  ❌ 失败: {result.get('message')}")
            fail_count += 1
            break
            
        print("-" * 50)
        import random
        delay_minutes = random.uniform(5.0, 10.0)
        print(f"💤 等待 {delay_minutes:.1f} 分钟后继续派发当前物理区下一篇图文...")
        await asyncio.sleep(delay_minutes * 60)
        
        max_publish_count = int(os.environ.get("MAX_PUBLISH_COUNT", "0"))
        if max_publish_count > 0 and success_count >= max_publish_count:
            break
        
        if time.time() - script_start_time > 5 * 3600:
            break
        
    print(f"\n=== 当前物理节点工作区处理完毕 ===")
    
    await browser.close()
    
    # 判断该工作区是否还有未完待续的任务
    if fail_count == 0:
        remaining = 0
        csv_files_end = glob.glob(os.path.join(work_dir, "*.csv"))
        for cpf in csv_files_end:
            with open(cpf, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if not row.get('发布状态', '').startswith('已定时发表'):
                        remaining += 1
        
        if remaining > 0:
            print(f"\n🚀 当前物理专区余量 {remaining} 条，发出请求激活下一个接力轮回...")
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                    f.write("trigger_next=true\n")
        else:
            print("\n🎉 该账号专属名下所有素材消耗完毕，本隔离节点退役休眠。")
            
if __name__ == "__main__":
    asyncio.run(main())
