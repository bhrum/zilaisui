"""
AI 文章生成器
——————————————
通过 CLIProxyAPI (OpenAI 兼容 API) 调用大语言模型，自动生成
适合微信公众号发布的高质量文章。

支持：
• 单篇 / 批量生成
• 可配置的 system prompt 和写作风格
• 结构化输出 (标题 / Markdown 正文 / 摘要)
• 主题从 CSV / YAML / 环境变量读取
"""

import asyncio
import json
import logging
import os
import sys
import csv
import yaml
from typing import Optional

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

logger = logging.getLogger("ArticleGenerator")

# ─── 默认 System Prompt ────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = """你是一位资深的微信公众号内容创作者，擅长撰写高质量、有深度的原创文章。

写作要求：
1. 文章格式为 Markdown
2. 必须包含清晰的标题层级（## 和 ### 小标题）
3. 正文不少于 800 字，但不超过 2000 字
4. 语风要专业但不生硬，适合大众阅读
5. 段落之间留空行，方便排版
6. 适当使用列表、引用等格式增强可读性
7. 结尾可加入简短总结或思考引导

输出格式（严格遵守 JSON）：
```json
{
    "title": "文章标题（15-30字，吸引眼球但不标题党）",
    "content_markdown": "完整的 Markdown 格式正文",
    "digest": "文章摘要（50字以内，概括核心观点）"
}
```

只输出上述 JSON，不要输出其他内容。"""


class ArticleGenerator:
    """
    通过 OpenAI 兼容 API 生成微信公众号文章。
    底层通过 CLIProxyAPI 代理到 Gemini / Claude / GPT 等模型。
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8317/v1",
        api_key: str = "wechat-auto-publish-key",
        model: str = "gemini-2.5-flash",
        system_prompt: Optional[str] = None,
        author: str = "AI Studio",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.author = author

        # 延迟导入 openai，避免在未安装时就报错
        try:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=120.0,
            )
        except ImportError:
            raise ImportError(
                "需要安装 openai SDK: pip install openai\n"
                "或: poetry add openai"
            )

    async def generate_article(
        self,
        topic: str,
        extra_instructions: str = "",
        temperature: float = 0.8,
        max_tokens: int = 4096,
    ) -> dict:
        """
        根据主题生成一篇微信公众号文章。

        Args:
            topic: 文章主题（如 "人工智能在教育中的应用"）
            extra_instructions: 额外的写作指令
            temperature: 创意温度 (0.0 - 1.0)
            max_tokens: 最大输出 token 数

        Returns:
            {
                "title": str,
                "content_markdown": str,
                "digest": str,
                "author": str,
                "topic": str,
                "model": str,
                "success": bool,
                "error": str | None
            }
        """
        user_message = f"请围绕以下主题撰写一篇微信公众号文章：\n\n主题：{topic}"
        if extra_instructions:
            user_message += f"\n\n补充要求：{extra_instructions}"

        logger.info(f"🤖 正在生成文章 | 主题: {topic} | 模型: {self.model}")

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )

            raw_content = response.choices[0].message.content
            logger.info(f"  📄 收到响应: {len(raw_content)} 字符")

            # 解析 JSON 输出
            article = self._parse_article_json(raw_content)
            article["author"] = self.author
            article["topic"] = topic
            article["model"] = self.model
            article["success"] = True
            article["error"] = None

            logger.info(f"  ✅ 文章生成成功: {article['title']}")
            return article

        except Exception as e:
            logger.error(f"  ❌ 文章生成失败: {e}")
            return {
                "title": "",
                "content_markdown": "",
                "digest": "",
                "author": self.author,
                "topic": topic,
                "model": self.model,
                "success": False,
                "error": str(e),
            }

    async def generate_batch(
        self,
        topics: list[str],
        delay_seconds: float = 5.0,
        **kwargs,
    ) -> list[dict]:
        """
        批量生成文章。每篇之间插入延迟以避免速率限制。

        Args:
            topics: 主题列表
            delay_seconds: 每篇之间的延迟（秒）
            **kwargs: 传递给 generate_article 的额外参数

        Returns:
            文章列表
        """
        articles = []
        total = len(topics)

        for i, topic in enumerate(topics, 1):
            logger.info(f"\n[{i}/{total}] 生成文章...")
            article = await self.generate_article(topic, **kwargs)
            articles.append(article)

            if i < total:
                logger.info(f"  💤 等待 {delay_seconds}s 后继续...")
                await asyncio.sleep(delay_seconds)

        success_count = sum(1 for a in articles if a["success"])
        logger.info(f"\n📊 批量生成完成: {success_count}/{total} 篇成功")
        return articles

    def _parse_article_json(self, raw_text: str) -> dict:
        """
        从 LLM 原始输出中提取 JSON 结构的文章数据。
        处理各种可能的格式问题（代码块包裹、多余文本等）。
        """
        text = raw_text.strip()

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试从 ```json ... ``` 代码块中提取
        import re
        json_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_block:
            try:
                return json.loads(json_block.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 尝试提取第一个 { ... } 块
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        # 所有方法都失败：将原始文本作为正文返回
        logger.warning("  ⚠️ 无法解析 JSON 输出，将原始文本作为正文使用")
        # 尝试从原始文本中提取标题（第一行非空行）
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        title = lines[0].lstrip("# ").strip()[:64] if lines else "AI 文章"
        content = text

        return {
            "title": title,
            "content_markdown": content,
            "digest": title[:54],
        }


# ─── 主题加载工具 ────────────────────────────────────────────

def load_topics_from_csv(csv_path: str, topic_column: str = "主题") -> list[str]:
    """从 CSV 文件中加载主题列表"""
    topics = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            topic = row.get(topic_column, "").strip()
            status = row.get("发布状态", "").strip()
            if topic and not status.startswith("已"):
                topics.append(topic)
    return topics


def load_topics_from_yaml(yaml_path: str) -> list[str]:
    """从 YAML 文件中加载主题列表"""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if isinstance(data, list):
        return [str(t) for t in data]
    if isinstance(data, dict):
        return [str(t) for t in data.get("topics", data.get("主题", []))]
    return []


def load_topics_from_env(env_key: str = "AI_ARTICLE_TOPICS") -> list[str]:
    """从环境变量中加载 JSON 格式的主题列表"""
    raw = os.environ.get(env_key, "")
    if not raw:
        return []
    try:
        topics = json.loads(raw)
        if isinstance(topics, list):
            return [str(t) for t in topics]
    except json.JSONDecodeError:
        # 尝试逗号分隔
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def load_topics(work_dir: str = None) -> list[str]:
    """
    智能加载主题列表。优先级：
    1. 环境变量 AI_ARTICLE_TOPICS
    2. work_dir 下的 topics.yaml
    3. work_dir 下的 topics.csv
    4. work_dir 下所有 CSV 中 "主题" 列
    """
    # 1. 环境变量
    topics = load_topics_from_env()
    if topics:
        logger.info(f"  📋 从环境变量加载了 {len(topics)} 个主题")
        return topics

    if not work_dir:
        work_dir = os.path.join(project_root, "sucai")

    # 2. topics.yaml
    yaml_path = os.path.join(work_dir, "topics.yaml")
    if os.path.exists(yaml_path):
        topics = load_topics_from_yaml(yaml_path)
        if topics:
            logger.info(f"  📋 从 {yaml_path} 加载了 {len(topics)} 个主题")
            return topics

    # 3. topics.csv
    csv_path = os.path.join(work_dir, "topics.csv")
    if os.path.exists(csv_path):
        topics = load_topics_from_csv(csv_path)
        if topics:
            logger.info(f"  📋 从 {csv_path} 加载了 {len(topics)} 个主题")
            return topics

    # 4. 所有 CSV 文件中的 "主题" 列
    import glob
    for csv_file in glob.glob(os.path.join(work_dir, "*.csv")):
        topics.extend(load_topics_from_csv(csv_file))
    if topics:
        logger.info(f"  📋 从 CSV 文件扫描到 {len(topics)} 个待创作主题")

    return topics


# ─── 命令行入口 ────────────────────────────────────────────

async def _main():
    """命令行测试入口"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    base_url = os.environ.get("CPA_BASE_URL", "http://127.0.0.1:8317/v1")
    api_key = os.environ.get("CPA_API_KEY", "wechat-auto-publish-key")
    model = os.environ.get("CPA_MODEL", "gemini-2.5-flash")
    author = os.environ.get("WECHAT_DEFAULT_AUTHOR", "AI Studio")

    gen = ArticleGenerator(
        base_url=base_url,
        api_key=api_key,
        model=model,
        author=author,
    )

    topic = sys.argv[1] if len(sys.argv) > 1 else "人工智能如何改变日常生活"
    article = await gen.generate_article(topic)

    print("\n" + "=" * 60)
    if article["success"]:
        print(f"标题: {article['title']}")
        print(f"摘要: {article['digest']}")
        print(f"正文长度: {len(article['content_markdown'])} 字符")
        print(f"模型: {article['model']}")
        print("-" * 60)
        print(article["content_markdown"][:500] + "...")
    else:
        print(f"❌ 生成失败: {article['error']}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(_main())
