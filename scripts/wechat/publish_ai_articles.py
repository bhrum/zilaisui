"""
AI 创作 + 微信公众号发布 — 端到端自动化脚本
——————————————————————————————————————
完整流程：
1. 启动 CLIProxyAPI 后台代理服务
2. 从主题配置加载待创作主题列表
3. 逐篇调用 AI 生成文章 (标题 + Markdown 正文 + 摘要)
4. 为每篇文章自动生成封面图
5. 通过 Playwright 自动化发布到微信公众号
6. 更新发布状态并同步到 Git
7. 5.5h 超时保护 + 接力触发下一轮
"""

import asyncio
import csv
import glob
import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta

# 确保项目根目录在 sys.path 中
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(encoding='utf-8')

# 强制开启 WeChat 模块
os.environ["WECHAT_ENABLED"] = "true"
if "WECHAT_HEADLESS" not in os.environ:
    os.environ["WECHAT_HEADLESS"] = "false"
os.environ["WECHAT_MIN_DELAY"] = "2.0"
os.environ["WECHAT_MAX_DELAY"] = "4.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("AIPublisher")

from wechat_publisher.browser import get_wechat_browser
from wechat_publisher.publisher import WeChatPublisher
from utils.image_generator import generate_cover_image
from scripts.wechat.article_generator import ArticleGenerator, load_topics
from scripts.wechat.setup_cliproxy import start_service, stop_service, get_api_key


def round_time_to_next_5_minutes(dt: datetime) -> datetime:
    discard = timedelta(minutes=dt.minute % 5, seconds=dt.second, microseconds=dt.microsecond)
    dt -= discard
    if discard >= timedelta(0):
        dt += timedelta(minutes=5)
    return dt


async def main():
    script_start_time = time.time()
    print("=" * 70)
    print("  🤖 AI 创作 + 微信公众号自动发布 — 全自动化流水线")
    print("=" * 70)

    # ─── 0. 获取目标账号信息 ───────────────────────────────
    target_account_id = os.environ.get("TARGET_ACCOUNT_ID")
    target_account_name = None

    if target_account_id:
        map_path = os.path.join(os.path.dirname(__file__), "wechat_accounts_map.json")
        try:
            with open(map_path, 'r', encoding='utf-8') as f:
                acc_map = json.load(f)
            for name, acc_id in acc_map.items():
                if acc_id == target_account_id.upper():
                    target_account_name = name
                    break
            if target_account_name:
                print(f"🎯 目标账号: [{target_account_name}] (ID: {target_account_id})")
            else:
                print(f"⚠️ 未找到 ID [{target_account_id}] 对应的账号名称！")
                return
        except Exception as e:
            print(f"⚠️ 读取账号映射出错: {e}")
            return

    # 确定工作目录
    if target_account_name:
        work_dir = os.path.join(project_root, "sucai", target_account_name)
    else:
        print("💡 未指定账号，以全局模式运行")
        work_dir = os.path.join(project_root, "sucai")

    os.makedirs(work_dir, exist_ok=True)

    # ─── 1. 启动 CLIProxyAPI 服务 ────────────────────────
    print("\n📡 [Phase 1] 启动 AI 代理服务 (CLIProxyAPI)...")
    try:
        api_base_url = start_service()
    except Exception as e:
        print(f"❌ CLIProxyAPI 启动失败: {e}")
        return

    api_key = get_api_key()
    model = os.environ.get("CPA_MODEL", "gemini-2.5-flash")
    author = os.environ.get("WECHAT_DEFAULT_AUTHOR", "bhrum")
    system_prompt = os.environ.get("AI_SYSTEM_PROMPT", None)

    # ─── 2. 加载待创作主题列表 ────────────────────────────
    print("\n📋 [Phase 2] 加载待创作主题...")
    topics = load_topics(work_dir)

    if not topics:
        print("💡 未找到待创作主题。可以通过以下方式提供：")
        print("   1. 设置环境变量 AI_ARTICLE_TOPICS='[\"主题1\",\"主题2\"]'")
        print(f"   2. 在 {work_dir}/topics.yaml 中配置")
        print(f"   3. 在 {work_dir}/*.csv 中添加 '主题' 列")
        stop_service()
        return

    print(f"  找到 {len(topics)} 个待创作主题")

    # ─── 3. 初始化 AI 文章生成器 ─────────────────────────
    print(f"\n🤖 [Phase 3] 初始化 AI 生成器 (模型: {model})...")
    generator = ArticleGenerator(
        base_url=api_base_url,
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        author=author,
    )

    # ─── 4. 注入微信认证凭证并启动浏览器 ────────────────
    print("\n🔐 [Phase 4] 初始化微信认证环境...")
    wechat_auth_json = os.environ.get("WECHAT_AUTH_STATE_JSON")
    if wechat_auth_json:
        from config.settings import WECHAT_AUTH_STATE_PATH
        print("  注入并发节点登录态凭证...")
        os.makedirs(os.path.dirname(WECHAT_AUTH_STATE_PATH), exist_ok=True)
        with open(WECHAT_AUTH_STATE_PATH, 'w', encoding='utf-8') as f:
            f.write(wechat_auth_json)

    browser = get_wechat_browser()
    is_headless = os.environ.get("WECHAT_HEADLESS", "false").lower() == "true"
    launched = await browser.launch(headless=is_headless)
    if not launched:
        print("❌ 浏览器启动失败")
        stop_service()
        return

    print("  校验登录状态...")
    logged_in = await browser.login(timeout_seconds=120)
    if not logged_in:
        print("❌ 微信登录失败或超时")
        await browser.close()
        stop_service()
        return

    print(f"  ✅ 登录成功! 账号: {browser.account_name}")
    publisher = WeChatPublisher(browser)

    # ─── 5. 计算定时策略 ──────────────────────────────────
    target_total_minutes = 7 * 24 * 60  # 一周内均匀分布
    interval_minutes = max(5, (target_total_minutes // len(topics) // 5) * 5)
    base_time = round_time_to_next_5_minutes(datetime.now())

    print(f"\n  📅 定时策略: 从 {base_time.strftime('%Y-%m-%d %H:%M')} 开始, 间隔 {interval_minutes} 分钟")

    # ─── 6. 逐篇：AI 创作 → 生成封面 → 发布 ──────────────
    success_count = 0
    fail_count = 0
    status_csv_path = os.path.join(work_dir, "ai_publish_status.csv")
    max_publish_count = int(os.environ.get("MAX_PUBLISH_COUNT", "50"))

    print(f"\n{'='*70}")
    print("  开始 AI 创作 + 发布流水线")
    print(f"{'='*70}\n")

    for i, topic in enumerate(topics):
        if max_publish_count > 0 and success_count >= max_publish_count:
            print(f"\n✋ 达到最大发布篇数 ({max_publish_count})，停止")
            break

        # 时间保护
        elapsed = time.time() - script_start_time
        max_total_seconds = 5.5 * 3600
        time_left = max_total_seconds - elapsed
        if time_left <= 300:  # 不足 5 分钟
            print(f"\n✋ 运行时间触及安全防线 ({elapsed/3600:.1f}h)，提前终止")
            break

        publish_time = base_time + timedelta(minutes=i * interval_minutes)
        time_str = publish_time.strftime('%Y-%m-%d %H:%M')

        print(f"[{i+1}/{len(topics)}] 🎯 主题: {topic}")

        # 6a. AI 生成文章
        print(f"  🤖 AI 创作中...")
        try:
            article = await asyncio.wait_for(
                generator.generate_article(topic),
                timeout=min(120, time_left - 60),
            )
        except asyncio.TimeoutError:
            print(f"  ❌ AI 生成超时，跳过")
            fail_count += 1
            continue

        if not article["success"]:
            print(f"  ❌ AI 生成失败: {article['error']}")
            fail_count += 1
            continue

        title = article["title"]
        content_md = article["content_markdown"]
        digest = article["digest"]

        print(f"  📝 标题: {title}")
        print(f"  📄 内容: {len(content_md)} 字符")

        # 6b. 生成封面图
        print(f"  🖼️ 生成封面图...")
        cover_path = generate_cover_image(title)

        # 6c. 调用 Publisher 发布
        print(f"  📤 发布到微信 (定时: {time_str})...")
        try:
            result = await asyncio.wait_for(
                publisher.publish_article(
                    title=title,
                    content_markdown=content_md,
                    author=author,
                    digest=digest,
                    mode="schedule",
                    cover_image_path=cover_path,
                    publish_time=publish_time,
                ),
                timeout=min(time_left - 30, 600),
            )
        except asyncio.TimeoutError:
            print(f"  ❌ 发布超时")
            fail_count += 1
            break

        if result.get("success"):
            print(f"  ✅ 发布成功!")
            success_count += 1

            # 记录到状态 CSV
            _record_status(status_csv_path, topic, title, time_str, "成功", model)

            # Git 推送
            if os.environ.get("GITHUB_ACTIONS") == "true":
                _git_push_status(status_csv_path, target_account_name, title)
        else:
            print(f"  ❌ 发布失败: {result.get('message')}")
            fail_count += 1
            _record_status(status_csv_path, topic, title, time_str, f"失败: {result.get('message', '')[:50]}", model)
            break  # 发布失败通常意味着需要人工干预

        print("-" * 50)

        # 发文间隔 (随机化)
        if i < len(topics) - 1:
            delay = random.uniform(3.0, 8.0) * 60
            print(f"  💤 等待 {delay/60:.1f} 分钟后继续...")
            await asyncio.sleep(delay)

    # ─── 7. 收尾 ──────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  📊 本轮总结: 成功 {success_count} 篇, 失败 {fail_count} 篇")
    print(f"{'='*70}")

    await browser.close()
    stop_service()

    # 检查是否还有剩余任务需要接力
    remaining = len(topics) - success_count - fail_count
    if remaining > 0 and fail_count == 0:
        print(f"\n🚀 尚余 {remaining} 个主题，请求触发下一轮...")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("trigger_next=true\n")
    else:
        print("\n🎉 所有主题处理完毕!")


def _record_status(csv_path: str, topic: str, title: str, scheduled_time: str, status: str, model: str):
    """追加发布状态到 CSV 文件"""
    is_new = not os.path.exists(csv_path)
    fieldnames = ["主题", "标题", "定时发布时间", "发布状态", "AI模型", "生成时间"]
    
    with open(csv_path, 'a', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "主题": topic,
            "标题": title,
            "定时发布时间": scheduled_time,
            "发布状态": status,
            "AI模型": model,
            "生成时间": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        })


def _git_push_status(csv_path: str, account_name: str | None, title: str):
    """在 GitHub Actions 中推送状态更新"""
    try:
        max_retries = 3
        for attempt in range(max_retries):
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], capture_output=True, cwd=project_root)
            subprocess.run(["git", "add", csv_path], check=True, capture_output=True, cwd=project_root)
            subprocess.run(["git", "add", "sucai/covers/"], capture_output=True, cwd=project_root)
            
            commit_msg = f"chore({account_name or 'global'}): AI published '{title[:30]}'"
            subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, cwd=project_root)
            
            push_res = subprocess.run(
                ["git", "push", "origin", "HEAD:main"],
                capture_output=True, text=True, cwd=project_root,
            )

            if push_res.returncode == 0:
                print("  📦 状态已推送到仓库")
                break
            else:
                print(f"  🔁 推送重试 ({attempt+1}/{max_retries})...")
                time.sleep(3)
    except Exception as e:
        print(f"  ⚠️ Git 推送异常: {e}")


if __name__ == "__main__":
    asyncio.run(main())
