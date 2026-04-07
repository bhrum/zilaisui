"""
WeChat Publisher Data Models
Pydantic models for WeChat article publishing requests and responses.
"""

from typing import Optional

from pydantic import BaseModel, Field


class WeChatArticleRequest(BaseModel):
    """Request model for publishing an article to WeChat Official Account."""

    title: str = Field(..., description="文章标题", max_length=64)
    content_markdown: str = Field(..., description="文章正文 (Markdown 格式)")
    author: Optional[str] = Field(None, description="作者名称，留空使用默认配置")
    digest: Optional[str] = Field(
        None, description="文章摘要，留空则自动截取正文前54字"
    )
    cover_image_path: Optional[str] = Field(
        None, description="封面图片本地路径，留空使用默认封面"
    )
    content_source_url: Optional[str] = Field(
        None, description="原文链接 URL"
    )


class WeChatPublishResponse(BaseModel):
    """Response model for article publish results."""

    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="结果描述")
    mode: str = Field("draft", description="发布模式: draft / publish")
    screenshot_path: Optional[str] = Field(
        None, description="操作完成后的截图路径（用于调试）"
    )


class WeChatStatusResponse(BaseModel):
    """Response model for WeChat login status."""

    enabled: bool = Field(..., description="微信发布模块是否启用")
    logged_in: bool = Field(False, description="是否已登录公众号后台")
    account_name: Optional[str] = Field(None, description="公众号名称")
    message: str = Field("", description="状态描述")


class WeChatLoginResponse(BaseModel):
    """Response when triggering login flow."""

    success: bool = Field(..., description="登录是否成功")
    message: str = Field(..., description="结果描述")
    needs_scan: bool = Field(False, description="是否需要扫码")
    qr_screenshot_path: Optional[str] = Field(
        None, description="二维码截图路径（需要扫码时）"
    )
