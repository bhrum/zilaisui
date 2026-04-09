"""
WeChat Browser Session Manager
Manages the Playwright browser context for mp.weixin.qq.com automation.

Handles:
- Launching a separate browser context (independent of the AI Studio browser)
- Login via QR code scan with cookie persistence
- Auth state save/load
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

from config.settings import (
    WECHAT_AUTH_STATE_PATH,
    WECHAT_HEADLESS,
    WECHAT_MAX_DELAY,
    WECHAT_MIN_DELAY,
)

from . import selectors as sel

logger = logging.getLogger("WeChatBrowser")


class WeChatBrowser:
    """
    Manages a Playwright browser context for WeChat Official Account backend.

    This runs independently from the AI Studio Playwright context to avoid
    interfering with the main proxy functionality.
    """

    def __init__(self):
        self._playwright = None
        self._camoufox = None
        self._browser = None
        self._context = None
        self._page = None
        self._token: Optional[str] = None
        self._is_logged_in = False
        self._account_name: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def page(self):
        return self._page

    @property
    def is_logged_in(self) -> bool:
        return self._is_logged_in

    @property
    def account_name(self) -> Optional[str]:
        return self._account_name

    @property
    def token(self) -> Optional[str]:
        return self._token

    async def launch(self, headless: Optional[bool] = None) -> bool:
        """
        Launch browser context for WeChat backend.

        Args:
            headless: Override headless setting. None uses config default.

        Returns:
            True if browser launched successfully.
        """
        if headless is None:
            headless = WECHAT_HEADLESS

        try:
            # 共用 AI Studio 底层操作逻辑：支持直连统一的 Camoufox CDP 端口 以及 统一网络代理
            ws_endpoint = os.environ.get("CAMOUFOX_WS_ENDPOINT")
            
            if ws_endpoint:
                from playwright.async_api import async_playwright
                logger.info(f"🔗 共用 AI Studio 底层逻辑: 通过 CDP 连接到共享 Camoufox 节点 ({ws_endpoint})...")
                # 必须先启动 playwright 引擎来承载 CDP 连接
                if not getattr(self, '_playwright', None):
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.firefox.connect(ws_endpoint, timeout=30000)
            else:
                from camoufox.async_api import AsyncCamoufox
                logger.info(f"🚀 启动独立的本地 Camoufox 实例运行微信自动化 (headless={headless})...")
                self._camoufox = AsyncCamoufox(
                    headless=headless,
                    enable_cache=True,
                    window_size=(1440, 900),
                    humanize=True,
                    geoip=True
                )
                self._browser = await self._camoufox.start()

            # Try loading existing auth state
            storage_state = None
            if os.path.exists(WECHAT_AUTH_STATE_PATH):
                try:
                    with open(WECHAT_AUTH_STATE_PATH, "r", encoding="utf-8") as f:
                        state_data = json.load(f)
                    if state_data.get("cookies"):
                        storage_state = WECHAT_AUTH_STATE_PATH
                        logger.info(
                            f"📂 Loaded existing auth state from {WECHAT_AUTH_STATE_PATH}"
                        )
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"Failed to load auth state: {e}")

            # Create context with optional saved state
            context_kwargs = {
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
            }
            if storage_state:
                context_kwargs["storage_state"] = storage_state

            # 共用 AI Studio 统一代理逻辑
            unified_proxy = os.environ.get("UNIFIED_PROXY_CONFIG") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            if unified_proxy:
                context_kwargs["proxy"] = {"server": unified_proxy}
                logger.info(f"🌐 共用 AI Studio 代理配置: {unified_proxy}")

            self._context = await self._browser.new_context(**context_kwargs)

            self._page = await self._context.new_page()
            logger.info("✅ WeChat browser launched successfully")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to launch WeChat browser: {e}", exc_info=True)
            return False

    async def login(self, timeout_seconds: int = 120) -> bool:
        """
        Perform login flow. If auth state exists, validate it.
        Otherwise, navigate to login page and wait for QR scan.

        Args:
            timeout_seconds: Max time to wait for QR code scan.

        Returns:
            True if login successful.
        """
        async with self._lock:
            if not self._page:
                logger.error("Browser not launched. Call launch() first.")
                return False

            try:
                # Navigate to WeChat backend
                logger.info("🔑 Navigating to mp.weixin.qq.com...")
                await self._page.goto(sel.MP_LOGIN_URL, wait_until="domcontentloaded")
                await self._random_delay()

                # Check if already logged in (auth state loaded)
                if await self._check_logged_in():
                    logger.info("✅ Already logged in via saved auth state")
                    return True

                # Need to scan QR code
                logger.info("📱 Please scan the QR code to login...")
                logger.info(f"⏳ Waiting up to {timeout_seconds}s for scan...")
                
                print("\n" + "=" * 60)
                print("⚠️  等待扫码登录 ⚠️")
                print("程序正在等待您扫码... 如果浏览器已显示二维码，请使用微信APP扫码确认。")
                print(f"最大等待时间: {timeout_seconds} 秒。扫码成功后，程序会自动继续...")
                print("=" * 60 + "\n")

                # Save QR code screenshot
                qr_screenshot_path = os.path.join(
                    os.path.dirname(WECHAT_AUTH_STATE_PATH), "qr_code.png"
                )
                os.makedirs(os.path.dirname(qr_screenshot_path), exist_ok=True)
                await self._page.screenshot(path=qr_screenshot_path)
                logger.info(f"📸 QR code screenshot saved to: {qr_screenshot_path}")

                # Wait for login success
                start_time = time.time()
                while time.time() - start_time < timeout_seconds:
                    if await self._check_logged_in():
                        logger.info("✅ Login successful!")
                        await self.save_auth()
                        return True
                    await asyncio.sleep(2)

                logger.error("❌ Login timed out")
                return False

            except Exception as e:
                logger.error(f"❌ Login failed: {e}", exc_info=True)
                return False

    async def _check_logged_in(self) -> bool:
        """Check if we're currently logged into the WeChat backend."""
        try:
            current_url = self._page.url

            # If URL contains "cgi-bin" or "home", we're likely logged in
            if "cgi-bin" in current_url and "token=" in current_url:
                self._is_logged_in = True
                self._extract_token(current_url)
                await self._extract_account_name()
                return True

            # Try to find the login success indicator
            try:
                indicator = await self._page.wait_for_selector(
                    sel.LOGIN_SUCCESS_INDICATOR, timeout=3000
                )
                if indicator:
                    self._is_logged_in = True
                    self._extract_token(self._page.url)
                    await self._extract_account_name()
                    return True
            except Exception:
                pass

            # Try navigating to home to see if we get redirected to login
            if "mp.weixin.qq.com" in current_url and "login" not in current_url:
                try:
                    await self._page.goto(
                        sel.MP_HOME_URL, wait_until="domcontentloaded", timeout=10000
                    )
                    await asyncio.sleep(1)
                    new_url = self._page.url
                    if "cgi-bin" in new_url and "login" not in new_url:
                        self._is_logged_in = True
                        self._extract_token(new_url)
                        await self._extract_account_name()
                        return True
                except Exception:
                    pass

            return False

        except Exception as e:
            logger.debug(f"Login check error: {e}")
            return False

    def _extract_token(self, url: str):
        """Extract the token parameter from a URL."""
        match = re.search(sel.TOKEN_URL_PATTERN, url)
        if match:
            self._token = match.group(1)
            logger.debug(f"Extracted token: {self._token}")

    async def _extract_account_name(self):
        """Try to extract the account name from the page."""
        try:
            elem = await self._page.query_selector(sel.ACCOUNT_NAME_SELECTOR)
            if elem:
                self._account_name = await elem.inner_text()
                if self._account_name:
                    self._account_name = self._account_name.strip()
                    logger.info(f"📝 Account: {self._account_name}")
        except Exception:
            pass

    async def save_auth(self) -> bool:
        """Save current browser auth state to file."""
        try:
            if not self._context:
                return False

            os.makedirs(os.path.dirname(WECHAT_AUTH_STATE_PATH), exist_ok=True)
            storage = await self._context.storage_state(path=WECHAT_AUTH_STATE_PATH)
            cookie_count = len(storage.get("cookies", []))
            logger.info(
                f"💾 Auth state saved ({cookie_count} cookies) → {WECHAT_AUTH_STATE_PATH}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to save auth state: {e}")
            return False

    async def load_auth(self) -> bool:
        """Check if saved auth state file exists and is valid."""
        if not os.path.exists(WECHAT_AUTH_STATE_PATH):
            return False
        try:
            with open(WECHAT_AUTH_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return bool(data.get("cookies"))
        except (json.JSONDecodeError, OSError):
            return False

    async def close(self):
        """Close the WeChat browser session."""
        try:
            if self._page and not self._page.is_closed():
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if hasattr(self, '_playwright') and self._playwright:
                await self._playwright.stop()
            logger.info("🔒 WeChat browser closed")
        except Exception as e:
            logger.error(f"Error closing WeChat browser: {e}")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._camoufox = None
            self._playwright = None
            self._is_logged_in = False
            self._token = None

    async def _random_delay(self, min_s: float = None, max_s: float = None):
        """Add a random delay to simulate human behavior."""
        import random

        min_delay = min_s if min_s is not None else WECHAT_MIN_DELAY
        max_delay = max_s if max_s is not None else WECHAT_MAX_DELAY
        delay = random.uniform(min_delay, max_delay)
        await asyncio.sleep(delay)

    async def ensure_ready(self) -> bool:
        """Ensure the browser is launched and logged in."""
        if not self._page or self._page.is_closed():
            if not await self.launch():
                return False
        if not self._is_logged_in:
            if not await self.login():
                return False
        return True


# Module-level singleton instance
_wechat_browser: Optional[WeChatBrowser] = None


def get_wechat_browser() -> WeChatBrowser:
    """Get or create the singleton WeChatBrowser instance."""
    global _wechat_browser
    if _wechat_browser is None:
        _wechat_browser = WeChatBrowser()
    return _wechat_browser


async def shutdown_wechat_browser():
    """Shutdown the singleton WeChatBrowser instance."""
    global _wechat_browser
    if _wechat_browser is not None:
        await _wechat_browser.close()
        _wechat_browser = None
