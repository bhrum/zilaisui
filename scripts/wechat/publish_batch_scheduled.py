"""
微信公众号统一发布引擎 (CSV 批量 + AI 自动创作)
————————————————————————————————————————————————
核心逻辑：
1. 扫描账号专属目录下的 CSV → 发布未完成的文章任务
2. 当所有 CSV 任务发完（或无 CSV）→ 检查 persona.md 人设文档
3. 如有 persona.md → 启动 CLIProxyAPI → AI 按人设生成文章 → 写入 CSV → 按同样节奏发布
4. 5.5h 超时保护 + 接力触发下一轮

==> 一个 Action，一个脚本，自动处理两种模式。
"""

import asyncio
import os
import sys
import csv
import subprocess
import time
import json
import glob
import random

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
logger = logging.getLogger("UnifiedPublisher")


def round_time_to_next_5_minutes(dt: datetime) -> datetime:
    discard = timedelta(minutes=dt.minute % 5, seconds=dt.second, microseconds=dt.microsecond)
    dt -= discard
    if discard >= timedelta(0):
        dt += timedelta(minutes=5)
    return dt


# ─── CSV 标准化列定义 ────────────────────────────────────────
AI_CSV_FIELDNAMES = ["标题", "正文", "摘要", "作者", "封面", "发布状态", "AI模型", "生成时间"]


def _extract_content_markdown(row: dict) -> str:
    """
    从 CSV 行中提取文章正文 Markdown。
    兼容两种格式：
    - 新格式: 直接有 "正文" 列
    - 旧格式: 从 "梵文原文" / "最终音译" 等列拼装
    """
    # 优先使用 "正文" 列
    if row.get("正文", "").strip():
        return row["正文"].strip()

    # 旧格式兼容：从领域列拼装
    title = row.get("标题", "")
    parts = [f"# {title}\n"]

    for col_name in ["梵文原文", "最终音译", "内容", "content", "body"]:
        val = row.get(col_name, "").strip()
        if val:
            parts.append(f"\n## {col_name}\n{val}\n")

    return "\n".join(parts) if len(parts) > 1 else title


def _load_persona(work_dir: str) -> str | None:
    """
    加载账号专属的 persona.md 人设文档。
    返回文档内容（用作 AI system prompt），若不存在则返回 None。
    """
    persona_path = os.path.join(work_dir, "persona.md")
    if not os.path.exists(persona_path):
        return None

    with open(persona_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        return None

    logger.info(f"  📋 加载人设文档: {persona_path} ({len(content)} 字符)")
    return content


def _load_topics_from_persona(persona_content: str) -> list[str]:
    """
    从 persona.md 的 YAML 前言或特殊标记中提取主题列表。
    支持格式：
    ---
    topics:
      - 主题1
      - 主题2
    ---
    """
    import re
    import yaml

    # 尝试提取 YAML front matter
    fm_match = re.match(r"^---\s*\n(.*?)\n---", persona_content, re.DOTALL)
    if fm_match:
        try:
            fm = yaml.safe_load(fm_match.group(1))
            if isinstance(fm, dict):
                topics = fm.get("topics", fm.get("主题", []))
                if isinstance(topics, list) and topics:
                    return [str(t) for t in topics]
        except Exception:
            pass

    return []


async def _ai_generate_to_csv(
    work_dir: str,
    persona_content: str,
    count: int = 5,
    script_start_time: float = 0,
) -> str | None:
    """
    使用 AI 按 persona 人设生成文章，写入 CSV 文件。
    返回生成的 CSV 文件路径，失败返回 None。
    """
    from scripts.wechat.setup_cliproxy import start_service, get_api_key, stop_service
    from scripts.wechat.article_generator import ArticleGenerator, load_topics

    # 构建 system prompt：将 persona 文档包裹进标准输出格式要求中
    system_prompt = f"""{persona_content}

---
【输出格式要求】（严格遵守 JSON）：
```json
{{
    "title": "文章标题（15-30字，吸引眼球但不标题党）",
    "content_markdown": "完整的 Markdown 格式正文（不少于800字）",
    "digest": "文章摘要（50字以内，概括核心观点）"
}}
```

只输出上述 JSON，不要输出其他内容。"""

    # 启动 CLIProxyAPI
    logger.info("\n📡 启动 AI 代理服务 (CLIProxyAPI)...")
    try:
        api_base_url = start_service()
    except Exception as e:
        logger.error(f"CLIProxyAPI 启动失败: {e}")
        return None

    api_key = get_api_key()
    model = os.environ.get("CPA_MODEL", "gemini-2.5-flash")
    author = os.environ.get("WECHAT_DEFAULT_AUTHOR", "bhrum")

    generator = ArticleGenerator(
        base_url=api_base_url,
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        author=author,
    )

    # 加载主题：persona front matter > topics.yaml > 环境变量
    topics = _load_topics_from_persona(persona_content)
    if not topics:
        topics = load_topics(work_dir)
    if not topics:
        # 没有明确主题 → 让 AI 自己选题
        logger.info("  💡 未找到明确主题列表，让 AI 根据人设自主选题...")
        topics = ["请根据你的人设定位，自主选择一个读者会感兴趣的话题来写"] * count

    # 限制生成数量
    topics = topics[:count]
    logger.info(f"  🤖 准备 AI 生成 {len(topics)} 篇文章 (模型: {model})...")

    # 生成文章并写入 CSV
    csv_filename = f"ai_generated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    csv_path = os.path.join(work_dir, csv_filename)
    generated_count = 0

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=AI_CSV_FIELDNAMES)
        writer.writeheader()

        for i, topic in enumerate(topics):
            # 超时保护
            if script_start_time and (time.time() - script_start_time > 4.5 * 3600):
                logger.info("  ⏰ AI 生成阶段触近时间上限，停止生成")
                break

            logger.info(f"  [{i+1}/{len(topics)}] 生成中... 主题: {topic[:30]}")
            try:
                article = await asyncio.wait_for(
                    generator.generate_article(topic),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                logger.warning(f"  ⚠️ 生成超时，跳过")
                continue

            if not article["success"]:
                logger.warning(f"  ⚠️ 生成失败: {article['error']}")
                continue

            writer.writerow({
                "标题": article["title"],
                "正文": article["content_markdown"],
                "摘要": article["digest"],
                "作者": author,
                "封面": "",  # 发布时自动生成
                "发布状态": "",
                "AI模型": model,
                "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            generated_count += 1
            logger.info(f"  ✅ 已生成: {article['title']}")

            # 生成间隔
            if i < len(topics) - 1:
                await asyncio.sleep(3)

    stop_service()

    if generated_count == 0:
        logger.warning("  ❌ AI 未能生成任何文章")
        try:
            os.remove(csv_path)
        except OSError:
            pass
        return None

    logger.info(f"  📄 AI 生成 {generated_count} 篇文章 → {csv_path}")
    return csv_path


async def main():
    script_start_time = time.time()
    print("=" * 70)
    print("  📝 微信公众号统一发布引擎 (CSV + AI 自动创作)")
    print("=" * 70)

    # ─── 0. 获取目标分配账号标识符 ───────────────────────
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
                print(f"🎯 调度器设定身份: [{target_account_name}] (ID: {target_account_id})")
            else:
                print(f"⚠️ 无法在 {map_path} 查找到 ID [{target_account_id}] 对应名称！程序终止。")
                return
        except Exception as e:
            print(f"⚠️ 致命: 读取账号映射图谱出错: {e}")
            return

    # 确定工作目录
    if target_account_name:
        work_dir = os.path.join(project_root, "sucai", target_account_name)
    else:
        print("💡 当前按【单机单账号全局模式】运行，将处理根 sucai/ 目录下的所有零散表。")
        work_dir = os.path.join(project_root, "sucai")

    os.makedirs(work_dir, exist_ok=True)

    # ─── 1. 扫描 CSV 任务 ─────────────────────────────────
    csv_files = glob.glob(os.path.join(work_dir, "*.csv"))
    articles = []

    if csv_files:
        print(f"\n1. 盘点多文件队列: 找到 {len(csv_files)} 份物料表单...")
        for curr_csv_path in csv_files:
            with open(curr_csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    title = row.get('标题', '').strip()
                    if not title:
                        continue
                    row['__source_csv_path'] = curr_csv_path
                    articles.append(row)

    # 过滤已发布的
    pending_articles = []
    already_published = 0
    for row in articles:
        status = str(row.get('发布状态', '') or '')
        if status.startswith('已定时发表') or status.startswith('已发布'):
            already_published += 1
        else:
            pending_articles.append(row)

    print(f"\n   总任务: {len(articles)} | 已发布: {already_published} | 待发布: {len(pending_articles)}")

    # ─── 2. 如果没有待发布任务 → 检查是否需要 AI 创作 ────
    ai_mode_activated = False
    if not pending_articles:
        persona_content = _load_persona(work_dir)
        if persona_content:
            print("\n🤖 [AI 模式] CSV 任务已清空，检测到 persona.md 人设文档")
            print("   正在启动 AI 自动创作...")

            ai_count = int(os.environ.get("AI_BATCH_SIZE", "5"))
            ai_csv_path = await _ai_generate_to_csv(
                work_dir=work_dir,
                persona_content=persona_content,
                count=ai_count,
                script_start_time=script_start_time,
            )

            if ai_csv_path:
                ai_mode_activated = True
                # 重新加载刚生成的 CSV
                with open(ai_csv_path, 'r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        title = row.get('标题', '').strip()
                        if title:
                            row['__source_csv_path'] = ai_csv_path
                            pending_articles.append(row)
                print(f"   ✅ AI 生成了 {len(pending_articles)} 篇文章，进入发布流程")
            else:
                print("   ⚠️ AI 创作失败，无法生成文章")
        else:
            print("\n🎉 所有 CSV 任务已发布完毕！")
            print("   💡 如需 AI 自动续写，请在此目录创建 persona.md 人设文档：")
            print(f"      {os.path.join(work_dir, 'persona.md')}")
            return

    if not pending_articles:
        print("💡 没有可发布的文章，自动退出。")
        return

    # ─── 3. 计算定时策略 ──────────────────────────────────
    target_total_minutes = 7 * 24 * 60
    interval_minutes = max(5, (target_total_minutes // len(pending_articles) // 5) * 5)
    if interval_minutes == 0:
        interval_minutes = 5

    base_time = round_time_to_next_5_minutes(datetime.now())

    for i, row in enumerate(pending_articles):
        row['__calculated_time'] = base_time + timedelta(minutes=i * interval_minutes)

    mode_label = "AI 创作" if ai_mode_activated else "CSV 批量"
    print(f"\n📅 发布模式: {mode_label} | 待发: {len(pending_articles)} 篇 | 间隔: {interval_minutes} 分钟")
    print(f"   起始时间: {base_time.strftime('%Y-%m-%d %H:%M')}")

    # ─── 4. 初始化浏览器 + 登录 ──────────────────────────
    print("\n🔐 初始化授权环境...")

    wechat_auth_json = os.environ.get("WECHAT_AUTH_STATE_JSON")
    if wechat_auth_json:
        from config.settings import WECHAT_AUTH_STATE_PATH
        print("   注入并发节点的专属登录态凭证...")
        os.makedirs(os.path.dirname(WECHAT_AUTH_STATE_PATH), exist_ok=True)
        with open(WECHAT_AUTH_STATE_PATH, 'w', encoding='utf-8') as f:
            f.write(wechat_auth_json)

    browser = get_wechat_browser()
    is_headless = os.environ.get("WECHAT_HEADLESS", "false").lower() == "true"
    launched = await browser.launch(headless=is_headless)
    if not launched:
        print("❌ 浏览器启动失败")
        return

    print("   校验最终登录状态...")
    logged_in = await browser.login(timeout_seconds=120)
    if not logged_in:
        print("❌ 登录失败或超时，凭证可能已失效！")
        await browser.close()
        return

    print(f"   ✅ 登录成功! 当前账号: {browser.account_name}")

    publisher = WeChatPublisher(browser)
    success_count = 0
    fail_count = 0
    author = os.environ.get("WECHAT_DEFAULT_AUTHOR", "bhrum")

    # ─── 5. 逐篇发布 ─────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  开始发布 ({mode_label})")
    print(f"{'='*70}\n")

    for task_index, row in enumerate(pending_articles, start=1):
        title = row.get('标题', '未命名')
        content_markdown = _extract_content_markdown(row)
        publish_time = row['__calculated_time']
        source_csv_path = row['__source_csv_path']
        time_str = publish_time.strftime('%Y-%m-%d %H:%M')

        print(f"[{task_index}/{len(pending_articles)}] 📤 {title}")
        print(f"   归属: {os.path.basename(source_csv_path)} | 定时: {time_str}")

        # 生成封面图
        cover_path = row.get("封面", "").strip()
        if not cover_path or not os.path.exists(cover_path):
            cover_path = generate_cover_image(title)

        # 超时保护
        elapsed = time.time() - script_start_time
        max_total_seconds = 5.5 * 3600
        time_left = max_total_seconds - elapsed

        if time_left <= 0:
            print("\n✋ 运行时间触及安全防线，提前转移下发队列。")
            break

        # 发布
        try:
            result = await asyncio.wait_for(
                publisher.publish_article(
                    title=title,
                    content_markdown=content_markdown,
                    author=author,
                    digest=row.get("摘要", ""),
                    mode="schedule",
                    cover_image_path=cover_path,
                    publish_time=publish_time
                ),
                timeout=min(time_left, 600)
            )
        except asyncio.TimeoutError:
            print(f"   ❌ 单篇发表超时")
            break

        if result.get("success"):
            print(f"   ✅ 定时发布成功!")
            success_count += 1

            # 更新 CSV 状态 + Git 推送
            _update_csv_status(source_csv_path, title, f"已定时发表 ({time_str})")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                _git_push_status(source_csv_path, target_account_name, title)
        else:
            print(f"   ❌ 失败: {result.get('message')}")
            fail_count += 1
            break

        print("-" * 50)

        # 随机间隔
        if task_index < len(pending_articles):
            delay_minutes = random.uniform(5.0, 10.0)
            print(f"💤 等待 {delay_minutes:.1f} 分钟后继续...")
            await asyncio.sleep(delay_minutes * 60)

        # 最大发布数量检查
        max_publish_count = int(os.environ.get("MAX_PUBLISH_COUNT", "0"))
        if max_publish_count > 0 and success_count >= max_publish_count:
            break

        if time.time() - script_start_time > 5 * 3600:
            break

    # ─── 6. 收尾 ────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"   📊 本轮总结: 成功 {success_count} | 失败 {fail_count} | 模式 {mode_label}")
    print(f"{'='*70}")

    await browser.close()

    # 判断是否需要接力
    if fail_count == 0:
        remaining = _count_remaining(work_dir)
        has_persona = os.path.exists(os.path.join(work_dir, "persona.md"))

        if remaining > 0:
            print(f"\n🚀 余量 {remaining} 条，触发下一轮接力...")
            _set_trigger_next()
        elif has_persona:
            # CSV 全部发完，但有 persona → 下一轮 AI 会继续生成
            print(f"\n🚀 CSV 已清空，但 persona.md 存在 → 触发下一轮 AI 创作...")
            _set_trigger_next()
        else:
            print("\n🎉 该账号所有素材消耗完毕，本节点退役。")


def _update_csv_status(csv_path: str, title: str, new_status: str):
    """更新 CSV 文件中指定标题的发布状态"""
    try:
        latest_rows = []
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            rdr = csv.DictReader(f)
            fieldnames = rdr.fieldnames or []
            latest_rows = list(rdr)

        for curr_row in latest_rows:
            if curr_row.get('标题', '').strip() == title:
                curr_row['发布状态'] = new_status
                break

        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            wrt = csv.DictWriter(f, fieldnames=fieldnames)
            wrt.writeheader()
            wrt.writerows(latest_rows)
    except Exception as e:
        logger.warning(f"  ⚠️ CSV 状态更新失败: {e}")


def _git_push_status(csv_path: str, account_name: str | None, title: str):
    """在 GitHub Actions 中推送状态更新"""
    try:
        max_retries = 3
        for attempt in range(max_retries):
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], capture_output=True, cwd=project_root)
            subprocess.run(["git", "add", csv_path], check=True, capture_output=True, cwd=project_root)
            subprocess.run(["git", "add", "sucai/covers/"], capture_output=True, cwd=project_root)

            commit_msg = f"chore({account_name or 'global'}): published '{title[:30]}'"
            subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, cwd=project_root)

            push_res = subprocess.run(
                ["git", "push", "origin", "HEAD:main"],
                capture_output=True, text=True, cwd=project_root,
            )

            if push_res.returncode == 0:
                print("   📦 进度已同步到仓库")
                break
            else:
                print(f"   🔁 推送重试 ({attempt+1}/{max_retries})...")
                time.sleep(3)
    except Exception as e:
        logger.warning(f"  ⚠️ Git 推送异常: {e}")


def _count_remaining(work_dir: str) -> int:
    """统计工作目录下所有 CSV 中未发布的任务数"""
    remaining = 0
    for cpf in glob.glob(os.path.join(work_dir, "*.csv")):
        try:
            with open(cpf, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    status = str(row.get('发布状态', '') or '')
                    if not status.startswith('已定时发表') and not status.startswith('已发布'):
                        if row.get('标题', '').strip():
                            remaining += 1
        except Exception:
            pass
    return remaining


def _set_trigger_next():
    """输出 GitHub Actions 接力触发信号"""
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("trigger_next=true\n")


if __name__ == "__main__":
    asyncio.run(main())
