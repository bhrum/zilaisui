import os
import sys
import asyncio
import json
import subprocess
import hashlib
import re

# 确保项目根目录在 sys.path 中，以便加载核心模块和配置
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv
load_dotenv()

# 强制开启微信模块并关闭无头模式，以便人工扫码
os.environ["WECHAT_ENABLED"] = "true"
os.environ["WECHAT_HEADLESS"] = "false"

from wechat_publisher.browser import get_wechat_browser
from config.settings import WECHAT_AUTH_STATE_PATH

MAP_FILE_PATH = os.path.join(os.path.dirname(__file__), "wechat_accounts_map.json")


def check_gh_cli():
    """检测本地系统是否安装并配置了 GitHub CLI (gh)"""
    try:
        # 使用 powershell / bash 跨平台执行指令校验
        result = subprocess.run(
            ["gh", "auth", "status"], 
            capture_output=True, 
            text=True, 
            timeout=10
        )
        if result.returncode != 0:
            print("⚠️ 警告: GitHub CLI (gh) 未检测到登录状态。自动同步 Secret 将会失败。")
            print("请先在终端执行 'gh auth login' 完成验证。")
            return False
        return True
    except FileNotFoundError:
        print("❌ 错误: 系统环境未安装 GitHub CLI (gh)。无法自动同步 Secret。")
        print("请访问 https://cli.github.com/ 安装并添加到环境变量。")
        return False

def load_account_map():
    if os.path.exists(MAP_FILE_PATH):
        with open(MAP_FILE_PATH, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_account_map(account_map):
    with open(MAP_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(account_map, f, ensure_ascii=False, indent=2)
    print(f"📄 同步成功：已将核心映射表更新持久化至 {MAP_FILE_PATH}")


def sync_secret(account_id: str, state_path: str):
    """自动将捕获到的微信 Auth State (JSON) 同步到 GitHub 仓库 Secrets 中"""
    if not os.path.exists(state_path):
        print(f"❌ 找不到状态文件: {state_path}")
        return False
        
    secret_name = f"WECHAT_AUTH_STATE_JSON_{account_id}"
    print(f"\n🔄 正在同步认证态至 GitHub Secret: {secret_name} ...")
    
    with open(state_path, 'r', encoding='utf-8') as f:
        auth_data = f.read()

    try:
        subprocess.run(
            ["gh", "secret", "set", secret_name],
            input=auth_data,
            text=True,
            capture_output=True,
            check=True
        )
        print(f"✅ 成功! Secret [{secret_name}] 已经安全推送到您的 GitHub 远程仓库。")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ 自动同步失败! `gh secret set` 错误信息:\n{e.stderr.strip()}")
        return False

async def add_or_update_account():
    # 先清理本地的旧状态，强制触发重新扫码登录
    if os.path.exists(WECHAT_AUTH_STATE_PATH):
        try:
            os.remove(WECHAT_AUTH_STATE_PATH)
            print("🧹 已清理本机历史缓存，准备开启独立扫码环境。")
        except OSError:
            pass
            
    browser = get_wechat_browser()
    launched = await browser.launch(headless=False)
    if not launched:
        print("❌ 浏览器引擎启动失败，请检查 Camoufox 运行状态。")
        return
        
    print(f"\n📱 启动扫码流程：在弹出的浏览器中，使用【微信App】扫码登录您想要新增加或刷新凭证的公众号。")
    print("工具将自动提取公众号实际名称并为您创建哈希防卫。")
    success = await browser.login(timeout_seconds=180)  # 给予充足扫码时间
    
    if success:
        account_name = browser.account_name
        if not account_name:
            print("⚠️ 无法自动获取您的公众号名称，可能网络较慢。工具强行停止。")
            await browser.close()
            return
            
        print(f"\n🎉 登录成功! 自动提取当前公众号名称为: [{account_name}]")
        
        # 将中文账号名自动生成合规标识符 (MD5取前6位组合)
        # 确保完全唯一且不含非常规字符，适合做 GitHub Secret 名称
        acc_hash = hashlib.md5(account_name.encode('utf-8')).hexdigest()[:6].upper()
        account_id = f"ACC_{acc_hash}"
        
        # 加载与更新账号图谱
        acc_map = load_account_map()
        acc_map[account_name] = account_id
        save_account_map(acc_map)
        
        # [NEW] 创建该账号的专属素材文件夹
        target_dir = os.path.join(project_root, "sucai", account_name)
        os.makedirs(target_dir, exist_ok=True)
        print(f"📁 已自动为您创建该账号专属素材存放区: {target_dir}")
        print("💡 以后请务必将属于该账号的 CSV 素材单独保存在该目录下。")
        
        # 强制保存最新态势到磁盘
        await browser.save_auth()
        await asyncio.sleep(2)  # 给进程足够的落盘和缓存同步时间
        await browser.close()
        
        # 将落地的 Token 推送到 Github Secret
        sync_secret(account_id, WECHAT_AUTH_STATE_PATH)
    else:
        print("\n⚠️ 登录失败或未在规定时间完成扫码，凭证抓取取消，未执行同步。")
        await browser.close()

def main():
    print("=========================================================")
    print("矩阵账号全自动抓取引擎：智能识别名称并动态编织并发密钥网络")
    print("=========================================================")
    
    has_cli = check_gh_cli()
    if not has_cli:
        choice = input("是否忽略 GitHub 同步功能，仅抓取 Token 并构建映射表？(y/N): ")
        if choice.lower() != 'y':
            sys.exit(1)

    print("\n请选择操作：")
    print("  1. 新增/刷新微信公众号凭证")
    print("  2. 配置 CLIProxyAPI 凭证 (AI 文章生成)")
    print("  3. 全部设置 (微信 + CLIProxyAPI)")

    choice = input("\n请输入选项 (1/2/3) [默认: 1]: ").strip() or "1"

    try:
        if choice in ("1", "3"):
            asyncio.run(add_or_update_account())
        if choice in ("2", "3"):
            setup_cpa_secrets(has_cli)
    except KeyboardInterrupt:
        print("\n中止操作。")


def setup_cpa_secrets(has_cli: bool = True):
    """
    交互式配置 CLIProxyAPI 凭证并同步到 GitHub Secrets。
    需要配置的 Secret：
    - CPA_API_KEY：CLIProxyAPI 的访问密钥（自定义，用于保护 API 访问）
    - GEMINI_API_KEY：Google Gemini API Key（用于 AI 文章生成）
    """
    print("\n" + "=" * 60)
    print("  🤖 CLIProxyAPI 凭证配置 (AI 文章生成引擎)")
    print("=" * 60)

    print("\n📌 CLIProxyAPI 是一个 AI 代理服务，将 Gemini/Claude/GPT 等")
    print("   模型暴露为标准 OpenAI 兼容 API，用于自动生成文章内容。\n")

    # 1. CPA_API_KEY — CLIProxyAPI 访问密钥
    print("─── 第 1 步: 设置 CLIProxyAPI 访问密钥 ───")
    print("这是一个自定义密码，用于保护您的 CLIProxyAPI 端点。")
    cpa_key = input("请输入 CPA_API_KEY (或回车使用默认值 'wechat-auto-key'): ").strip()
    if not cpa_key:
        cpa_key = "wechat-auto-key"

    # 2. GEMINI_API_KEY — Google Gemini API Key
    print("\n─── 第 2 步: 设置 Gemini API Key ───")
    print("前往 https://aistudio.google.com/apikey 获取免费的 Gemini API Key")
    gemini_key = input("请输入 GEMINI_API_KEY: ").strip()

    if not gemini_key:
        print("⚠️ 未输入 Gemini API Key，AI 文章生成将无法工作。")
        print("   您也可以配置第三方 OpenAI 兼容 API 作为替代。")
        
        # 可选：第三方 OpenAI 兼容 API
        print("\n─── (可选) 第三方 OpenAI 兼容 API ───")
        compat_url = input("OpenAI 兼容 API Base URL (留空跳过): ").strip()
        if compat_url:
            compat_key = input("API Key: ").strip()
            if has_cli and compat_key:
                _set_github_secret("OPENAI_COMPAT_BASE_URL", compat_url)
                _set_github_secret("OPENAI_COMPAT_API_KEY", compat_key)

    # 3. 同步到 GitHub Secrets
    if has_cli:
        print("\n🔄 正在同步凭证到 GitHub Secrets...")
        _set_github_secret("CPA_API_KEY", cpa_key)
        if gemini_key:
            _set_github_secret("GEMINI_API_KEY", gemini_key)

    # 4. (可选) 配置 AI 创作主题
    print("\n─── (可选) 配置 AI 文章主题 ───")
    print("您可以现在提供创作主题，也可以后续通过 topics.yaml 文件配置。")
    topics_input = input("请输入主题 (逗号分隔，留空跳过): ").strip()
    if topics_input and has_cli:
        topics_list = [t.strip() for t in topics_input.split(",") if t.strip()]
        topics_json = json.dumps(topics_list, ensure_ascii=False)
        _set_github_secret("AI_ARTICLE_TOPICS", topics_json)
        print(f"  ✅ 已设置 {len(topics_list)} 个创作主题")

    print("\n" + "=" * 60)
    print("  ✅ CLIProxyAPI 凭证配置完成!")
    print("=" * 60)
    print("\n💡 后续使用：")
    print("   • 手动触发 GitHub Action: 'WeChat AI Article Publish'")
    print("   • 本地测试: python scripts/wechat/publish_ai_articles.py")
    print("   • 本地只测 AI: python scripts/wechat/article_generator.py '你的主题'")


def _set_github_secret(name: str, value: str):
    """设置单个 GitHub Secret"""
    try:
        result = subprocess.run(
            ["gh", "secret", "set", name],
            input=value,
            text=True,
            capture_output=True,
            check=True,
        )
        print(f"  ✅ Secret [{name}] 已同步")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Secret [{name}] 同步失败: {e.stderr.strip()}")
        return False


if __name__ == "__main__":
    main()

