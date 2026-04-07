"""
WeChat Publisher Module
Browser-based automation for publishing articles to WeChat Official Account.

Simulates real user behavior on mp.weixin.qq.com using Playwright,
following the same architecture pattern as the AI Studio browser automation.
"""

from .browser import WeChatBrowser, get_wechat_browser, shutdown_wechat_browser
from .content_formatter import extract_digest, markdown_to_wechat_html
from .models import (
    WeChatArticleRequest,
    WeChatLoginResponse,
    WeChatPublishResponse,
    WeChatStatusResponse,
)
from .publisher import WeChatPublisher

__all__ = [
    # Core classes
    "WeChatBrowser",
    "WeChatPublisher",
    # Singleton management
    "get_wechat_browser",
    "shutdown_wechat_browser",
    # Content formatting
    "markdown_to_wechat_html",
    "extract_digest",
    # Data models
    "WeChatArticleRequest",
    "WeChatPublishResponse",
    "WeChatStatusResponse",
    "WeChatLoginResponse",
]
