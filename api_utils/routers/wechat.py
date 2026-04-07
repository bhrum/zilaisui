"""
WeChat Publisher API Router
REST endpoints for WeChat Official Account article publishing.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from config.settings import WECHAT_ENABLED

from wechat_publisher import (
    WeChatArticleRequest,
    WeChatPublisher,
    get_wechat_browser,
    shutdown_wechat_browser,
)

logger = logging.getLogger("WeChatRouter")

router = APIRouter(prefix="/api/wechat", tags=["WeChat Publisher"])


def _check_enabled():
    """Check if WeChat publisher module is enabled."""
    if not WECHAT_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="微信发布模块未启用。请在 .env 中设置 WECHAT_ENABLED=true",
        )


@router.get("/status")
async def wechat_status():
    """
    Get the current status of the WeChat publisher module.
    Returns login status, account name, and configuration.
    """
    browser = get_wechat_browser()
    return JSONResponse(
        content={
            "enabled": WECHAT_ENABLED,
            "logged_in": browser.is_logged_in,
            "account_name": browser.account_name,
            "message": (
                "已登录" if browser.is_logged_in
                else "未启用" if not WECHAT_ENABLED
                else "未登录"
            ),
        }
    )


@router.post("/login")
async def wechat_login(headless: Optional[bool] = None):
    """
    Trigger the WeChat login flow.

    For first-time login, set headless=false so you can scan the QR code.
    After successful login, the auth state will be saved and reused.

    Args:
        headless: Whether to run browser in headless mode.
                  Set to false for QR code scanning.
    """
    _check_enabled()
    browser = get_wechat_browser()

    try:
        # Launch browser if not running
        if not browser.page or browser.page.is_closed():
            launched = await browser.launch(headless=headless)
            if not launched:
                return JSONResponse(
                    status_code=500,
                    content={
                        "success": False,
                        "message": "浏览器启动失败",
                        "needs_scan": False,
                    },
                )

        # Attempt login
        success = await browser.login(timeout_seconds=120)

        if success:
            return JSONResponse(
                content={
                    "success": True,
                    "message": f"登录成功！账号: {browser.account_name or '未知'}",
                    "needs_scan": False,
                }
            )
        else:
            return JSONResponse(
                content={
                    "success": False,
                    "message": "登录超时，请在120秒内完成扫码",
                    "needs_scan": True,
                    "qr_screenshot_path": "wechat_auth/qr_code.png",
                }
            )

    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": f"登录异常: {str(e)}",
                "needs_scan": False,
            },
        )


@router.post("/logout")
async def wechat_logout():
    """
    Logout and clear saved auth state.
    """
    _check_enabled()

    try:
        await shutdown_wechat_browser()
        import os
        from config.settings import WECHAT_AUTH_STATE_PATH
        if os.path.exists(WECHAT_AUTH_STATE_PATH):
            os.remove(WECHAT_AUTH_STATE_PATH)
            logger.info(f"Removed auth state: {WECHAT_AUTH_STATE_PATH}")

        return JSONResponse(
            content={
                "success": True,
                "message": "已登出并清除登录态",
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": f"登出失败: {str(e)}",
            },
        )


@router.post("/publish")
async def wechat_publish(request: WeChatArticleRequest):
    """
    Publish an article to WeChat Official Account.

    The article content should be in Markdown format and will be automatically
    converted to WeChat-compatible HTML with inline styles.

    By default, articles are saved as drafts (WECHAT_PUBLISH_MODE=draft).
    """
    _check_enabled()

    publisher = WeChatPublisher()

    result = await publisher.publish_article(
        title=request.title,
        content_markdown=request.content_markdown,
        author=request.author,
        digest=request.digest,
        cover_image_path=request.cover_image_path,
        content_source_url=request.content_source_url,
    )

    status_code = 200 if result.get("success") else 500
    return JSONResponse(status_code=status_code, content=result)


@router.post("/preview")
async def wechat_preview(request: WeChatArticleRequest):
    """
    Preview the converted HTML content without publishing.
    Useful for checking formatting before actually posting.
    """
    from wechat_publisher import extract_digest, markdown_to_wechat_html

    html_content = markdown_to_wechat_html(request.content_markdown)
    digest = request.digest or extract_digest(request.content_markdown)

    return JSONResponse(
        content={
            "title": request.title,
            "author": request.author,
            "digest": digest,
            "content_html": html_content,
            "content_length": len(html_content),
        }
    )
