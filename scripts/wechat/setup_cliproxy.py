"""
CLIProxyAPI 服务管理器
——————————————————————
• 从 GitHub Releases 下载预编译二进制（或使用已缓存的本地副本）
• 根据环境变量动态生成 config.yaml
• 后台启动 CLIProxyAPI 服务
• 健康检查确认 API 可用
• 优雅关闭
"""

import asyncio
import json
import os
import platform
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

sys.stdout.reconfigure(encoding='utf-8')

# ─── 配置常量 ───────────────────────────────────────────────

CLIPROXY_PORT = int(os.environ.get("CPA_PORT", "8317"))
CLIPROXY_DIR = os.path.join(project_root, "CLIProxyAPI")
CLIPROXY_BIN_DIR = os.path.join(project_root, ".cliproxy_bin")
CLIPROXY_CONFIG_PATH = os.path.join(CLIPROXY_BIN_DIR, "config.yaml")
GITHUB_RELEASES_API = "https://api.github.com/repos/router-for-me/CLIProxyAPI/releases/latest"

# 全局进程句柄
_cliproxy_process: subprocess.Popen | None = None


def _detect_platform() -> tuple[str, str]:
    """检测当前操作系统和架构，返回 (goos, goarch)"""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        goos = "windows"
    elif system == "darwin":
        goos = "darwin"
    else:
        goos = "linux"

    if machine in ("x86_64", "amd64"):
        goarch = "amd64"
    elif machine in ("aarch64", "arm64"):
        goarch = "arm64"
    else:
        goarch = "amd64"  # fallback

    return goos, goarch


def _get_binary_name(goos: str) -> str:
    """根据操作系统返回二进制文件名"""
    if goos == "windows":
        return "cli-proxy-api.exe"
    return "cli-proxy-api"


def download_cliproxy_binary() -> str:
    """
    从 GitHub Releases 下载最新的预编译二进制文件。
    返回二进制文件的绝对路径。
    """
    goos, goarch = _detect_platform()
    binary_name = _get_binary_name(goos)
    binary_path = os.path.join(CLIPROXY_BIN_DIR, binary_name)

    # 如果已有缓存的二进制文件，直接使用
    if os.path.exists(binary_path):
        print(f"✅ 已缓存的 CLIProxyAPI 二进制: {binary_path}")
        return binary_path

    os.makedirs(CLIPROXY_BIN_DIR, exist_ok=True)

    print(f"📥 正在从 GitHub Releases 下载 CLIProxyAPI ({goos}/{goarch})...")

    # 获取最新 release 信息
    try:
        req = urllib.request.Request(GITHUB_RELEASES_API, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            release_info = json.loads(resp.read().decode())
    except Exception as e:
        print(f"⚠️ 无法获取 release 信息: {e}")
        print("  降级: 尝试从本地源码编译...")
        return _build_from_source()

    # 查找匹配的 asset
    target_suffix = f"_{goos}_{goarch}"
    archive_ext = ".zip" if goos == "windows" else ".tar.gz"
    download_url = None
    asset_name = None

    for asset in release_info.get("assets", []):
        name = asset["name"]
        if target_suffix in name and name.endswith(archive_ext):
            download_url = asset["browser_download_url"]
            asset_name = name
            break

    if not download_url:
        print(f"⚠️ 未找到匹配 {goos}/{goarch} 的 release asset")
        return _build_from_source()

    # 下载压缩包
    print(f"  下载: {asset_name}")
    archive_path = os.path.join(CLIPROXY_BIN_DIR, asset_name)
    try:
        urllib.request.urlretrieve(download_url, archive_path)
    except Exception as e:
        print(f"⚠️ 下载失败: {e}")
        return _build_from_source()

    # 解压
    print(f"  解压: {asset_name}")
    try:
        if archive_ext == ".zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                for member in zf.namelist():
                    if member.endswith(binary_name):
                        # 提取到 bin 目录
                        source = zf.open(member)
                        with open(binary_path, "wb") as target:
                            target.write(source.read())
                        break
        else:
            with tarfile.open(archive_path, "r:gz") as tf:
                for member in tf.getmembers():
                    if member.name.endswith(binary_name):
                        f = tf.extractfile(member)
                        if f:
                            with open(binary_path, "wb") as target:
                                target.write(f.read())
                        break
    except Exception as e:
        print(f"⚠️ 解压失败: {e}")
        return _build_from_source()

    # 设置执行权限 (Linux/macOS)
    if goos != "windows":
        os.chmod(binary_path, 0o755)

    # 清理压缩包
    try:
        os.remove(archive_path)
    except OSError:
        pass

    print(f"✅ CLIProxyAPI 二进制就绪: {binary_path}")
    return binary_path


def _build_from_source() -> str:
    """从本地源码编译 CLIProxyAPI（降级方案）"""
    goos, goarch = _detect_platform()
    binary_name = _get_binary_name(goos)
    binary_path = os.path.join(CLIPROXY_BIN_DIR, binary_name)

    if not os.path.exists(os.path.join(CLIPROXY_DIR, "go.mod")):
        raise FileNotFoundError(
            f"CLIProxyAPI 源码不存在于 {CLIPROXY_DIR}，且无法从 GitHub 下载二进制。"
            f"请先执行: git clone https://github.com/router-for-me/CLIProxyAPI {CLIPROXY_DIR}"
        )

    print(f"🔨 从源码编译 CLIProxyAPI...")
    os.makedirs(CLIPROXY_BIN_DIR, exist_ok=True)

    env = os.environ.copy()
    env["CGO_ENABLED"] = "0"

    result = subprocess.run(
        ["go", "build", "-ldflags=-s -w", "-o", binary_path, "./cmd/server/"],
        cwd=CLIPROXY_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(f"编译失败:\n{result.stderr}")

    print(f"✅ 编译完成: {binary_path}")
    return binary_path


def generate_config() -> str:
    """
    根据环境变量动态生成 CLIProxyAPI config.yaml。
    返回配置文件路径。
    """
    os.makedirs(CLIPROXY_BIN_DIR, exist_ok=True)

    api_key = os.environ.get("CPA_API_KEY", "wechat-auto-publish-key")
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    
    # 构建 openai-compatibility 配置（如果提供了第三方 API）
    openai_compat_section = ""
    openai_base_url = os.environ.get("OPENAI_COMPAT_BASE_URL", "")
    openai_api_key = os.environ.get("OPENAI_COMPAT_API_KEY", "")
    if openai_base_url and openai_api_key:
        openai_compat_section = f"""
openai-compatibility:
  - name: "custom-provider"
    base-url: "{openai_base_url}"
    api-key-entries:
      - api-key: "{openai_api_key}"
"""

    config_yaml = f"""# Auto-generated CLIProxyAPI config for WeChat AI Publishing
# Generated by setup_cliproxy.py — DO NOT EDIT MANUALLY

host: "127.0.0.1"
port: {CLIPROXY_PORT}

api-keys:
  - "{api_key}"

debug: false
request-retry: 2
"""

    # 添加 Gemini API Key 配置
    if gemini_api_key:
        config_yaml += f"""
gemini-api-key:
  - api-key: "{gemini_api_key}"
"""

    # 添加 OpenAI 兼容配置
    if openai_compat_section:
        config_yaml += openai_compat_section

    with open(CLIPROXY_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(config_yaml)

    print(f"✅ CLIProxyAPI 配置文件已生成: {CLIPROXY_CONFIG_PATH}")
    return CLIPROXY_CONFIG_PATH


def start_service() -> str:
    """
    启动 CLIProxyAPI 后台服务。
    返回 API base URL (e.g. http://127.0.0.1:8317/v1)
    """
    global _cliproxy_process

    if _cliproxy_process and _cliproxy_process.poll() is None:
        print("⚠️ CLIProxyAPI 服务已在运行中")
        return f"http://127.0.0.1:{CLIPROXY_PORT}/v1"

    # 1. 确保二进制就绪
    binary_path = download_cliproxy_binary()

    # 2. 确保配置文件就绪
    config_path = generate_config()

    # 3. 启动后台进程
    print(f"🚀 启动 CLIProxyAPI 服务 (端口 {CLIPROXY_PORT})...")

    log_file = os.path.join(CLIPROXY_BIN_DIR, "cliproxy.log")
    log_fh = open(log_file, "w", encoding="utf-8")

    _cliproxy_process = subprocess.Popen(
        [binary_path, "--config", config_path],
        cwd=CLIPROXY_BIN_DIR,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    print(f"  PID: {_cliproxy_process.pid}")
    print(f"  日志: {log_file}")

    # 4. 健康检查 — 等待 API 就绪
    base_url = f"http://127.0.0.1:{CLIPROXY_PORT}"
    api_url = f"{base_url}/v1"
    health_url = f"{base_url}/v1/models"

    print("  等待 API 就绪...", end="", flush=True)
    for attempt in range(30):
        time.sleep(1)
        try:
            req = urllib.request.Request(
                health_url,
                headers={
                    "Authorization": f"Bearer {os.environ.get('CPA_API_KEY', 'wechat-auto-publish-key')}",
                    "User-Agent": "Mozilla/5.0",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    models_data = json.loads(resp.read().decode())
                    model_count = len(models_data.get("data", []))
                    print(f"\n✅ CLIProxyAPI 就绪! 可用模型: {model_count} 个")
                    # 打印前5个模型名
                    for m in models_data.get("data", [])[:5]:
                        print(f"   • {m.get('id', 'unknown')}")
                    if model_count > 5:
                        print(f"   ... 及其他 {model_count - 5} 个模型")
                    return api_url
        except Exception:
            print(".", end="", flush=True)

        # 检查进程是否意外退出
        if _cliproxy_process.poll() is not None:
            print(f"\n❌ CLIProxyAPI 进程意外退出 (code={_cliproxy_process.returncode})")
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    print(f"  最后日志:\n{f.read()[-2000:]}")
            except Exception:
                pass
            raise RuntimeError("CLIProxyAPI 启动失败")

    print("\n❌ 健康检查超时 (30s)")
    stop_service()
    raise RuntimeError("CLIProxyAPI 启动超时")


def stop_service():
    """优雅关闭 CLIProxyAPI 服务"""
    global _cliproxy_process

    if _cliproxy_process is None:
        return

    if _cliproxy_process.poll() is not None:
        print(f"  CLIProxyAPI 已退出 (code={_cliproxy_process.returncode})")
        _cliproxy_process = None
        return

    print("🛑 关闭 CLIProxyAPI 服务...")
    try:
        if sys.platform == "win32":
            _cliproxy_process.terminate()
        else:
            _cliproxy_process.send_signal(signal.SIGTERM)

        _cliproxy_process.wait(timeout=10)
        print("  ✅ 服务已停止")
    except subprocess.TimeoutExpired:
        print("  ⚠️ 优雅关闭超时，强制终止...")
        _cliproxy_process.kill()
        _cliproxy_process.wait(timeout=5)
    except Exception as e:
        print(f"  ⚠️ 关闭时出错: {e}")
    finally:
        _cliproxy_process = None


def get_api_base_url() -> str:
    """返回 CLIProxyAPI 的 API base URL"""
    return f"http://127.0.0.1:{CLIPROXY_PORT}/v1"


def get_api_key() -> str:
    """返回配置的 API Key"""
    return os.environ.get("CPA_API_KEY", "wechat-auto-publish-key")


# ─── 命令行入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CLIProxyAPI 服务管理器")
    parser.add_argument("action", choices=["start", "download", "config"],
                        help="start=启动服务, download=仅下载二进制, config=仅生成配置")
    args = parser.parse_args()

    if args.action == "download":
        download_cliproxy_binary()
    elif args.action == "config":
        generate_config()
    elif args.action == "start":
        try:
            url = start_service()
            print(f"\n服务地址: {url}")
            print("按 Ctrl+C 停止...")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_service()
