"""
WeChat Article Publisher
Handles the browser-based page operations for creating and saving articles
on the WeChat Official Account backend (mp.weixin.qq.com).
"""

import asyncio
import logging
import os
from typing import Optional

from config.settings import (
    WECHAT_DEFAULT_AUTHOR,
    WECHAT_MAX_DELAY,
    WECHAT_MIN_DELAY,
    WECHAT_PUBLISH_MODE,
)

from . import selectors as sel
from .browser import WeChatBrowser, get_wechat_browser
from .content_formatter import extract_digest, markdown_to_wechat_html

logger = logging.getLogger("WeChatPublisher")


class WeChatPublisher:
    """
    Orchestrates article creation on WeChat Official Account backend
    through Playwright browser automation.
    """

    def __init__(self, browser: Optional[WeChatBrowser] = None):
        self._browser = browser or get_wechat_browser()

    async def publish_article(
        self,
        title: str,
        content_markdown: str,
        author: Optional[str] = None,
        digest: Optional[str] = None,
        cover_image_path: Optional[str] = None,
        content_source_url: Optional[str] = None,
        mode: Optional[str] = None,
        publish_time = None,
    ) -> dict:
        """
        Full article publishing workflow:
        1. Ensure browser is ready and logged in
        2. Navigate to article editor
        3. Fill in title, content, author, digest
        4. Upload cover image (if provided)
        5. Save as draft or publish

        Args:
            title: Article title (max 64 chars).
            content_markdown: Article body in Markdown format.
            author: Author name. Defaults to config.
            digest: Article digest/summary. Auto-generated if not provided.
            cover_image_path: Path to cover image file.
            content_source_url: Original source URL.
            mode: "draft" or "publish". Defaults to config.

        Returns:
            Dict with success status, message, and optional screenshot path.
        """
        if mode is None:
            mode = WECHAT_PUBLISH_MODE
        if author is None:
            author = WECHAT_DEFAULT_AUTHOR
        if digest is None:
            digest = extract_digest(content_markdown)

        # Step 1: Convert Markdown to WeChat HTML
        logger.info(f"📝 Publishing article: {title}")
        content_html = markdown_to_wechat_html(content_markdown)
        logger.info(
            f"  Content converted: {len(content_markdown)} chars MD → "
            f"{len(content_html)} chars HTML"
        )

        # Step 2: Ensure browser is ready
        if not await self._browser.ensure_ready():
            return {
                "success": False,
                "message": "浏览器未就绪或未登录，请先登录公众号后台",
                "mode": mode,
            }

        page = self._browser.page
        if not page:
            return {
                "success": False,
                "message": "浏览器页面不可用",
                "mode": mode,
            }

        try:
            # Step 3: Navigate to new article editor
            await self._navigate_to_editor(page)

            # Step 4: Fill in article content
            await self._fill_title(page, title)
            await self._fill_content(page, content_html)
            await self._fill_author(page, author)
            await self._fill_digest(page, digest)

            # Step 5: Set original URL if provided
            if content_source_url:
                await self._fill_source_url(page, content_source_url)

            # Step 6: Upload cover image if provided
            if cover_image_path and os.path.exists(cover_image_path):
                await self._upload_cover_image(page, cover_image_path)

            # Step 6.5: Declare originality and enable appreciation
            await self._declare_original_and_appreciation(page, appreciation_author=author or "Bhrum")

            # Step 7: Save draft or publish
            if mode == "publish":
                result = await self._do_publish(page)
            elif mode == "schedule":
                from datetime import datetime
                if not publish_time:
                    publish_time = datetime.now()
                for retry_idx in range(9999):
                    result = await self._do_schedule_publish(page, publish_time)
                    if not result.get("success") and result.get("retry_full_flow"):
                        logger.warning(f"  [Auto-Retry] 捕获到弹窗异常消失标志(系统繁忙)，等待 15 分钟后进行第 {retry_idx + 1} 次完整重试流程...")
                        await asyncio.sleep(900)
                        if "appmsg_edit" not in page.url:
                            logger.info("  ⭐ [静默成功] 发现页面在 15 分钟休眠期间已成功跳转，确认为延迟发表成功，免去重试！")
                            result = {"success": True, "message": "休眠期间跳转成功", "screenshot_path": ""}
                            break
                        continue
                    break
            else:
                result = await self._do_save_draft(page)

            # Save auth state after successful operation
            await self._browser.save_auth()

            # Take a screenshot of the result
            screenshot_path = await self._take_result_screenshot(page, title)
            result["screenshot_path"] = screenshot_path

            return result

        except Exception as e:
            logger.error(f"❌ Article publish failed: {e}", exc_info=True)
            # Try to take an error screenshot
            try:
                err_path = os.path.join(
                    os.path.dirname(
                        os.path.dirname(os.path.abspath(__file__))
                    ),
                    "wechat_auth",
                    "error_screenshot.png",
                )
                await page.screenshot(path=err_path)
            except Exception:
                pass
            return {
                "success": False,
                "message": f"发布失败: {str(e)}",
                "mode": mode,
            }

    async def _navigate_to_editor(self, page):
        """Navigate to the article editor page."""
        logger.info("📄 Navigating to article editor...")

        # Build URL with token if available
        token = self._browser.token
        if token:
            editor_url = sel.MP_NEW_ARTICLE_URL.format(token=token)
        else:
            editor_url = sel.MP_DRAFT_URL

        await page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
        await self._random_delay(2, 4)

        # 检查是否因为登录过期被重定向到了登录页/首页
        current_url = page.url
        if "appmsg" not in current_url and ("login" in current_url or current_url.endswith("mp.weixin.qq.com/") or "home" in current_url):
            logger.warning("⚠️ 登录状态可能已过期，被重定向到了非编辑器页面。")
            print("\n" + "=" * 60)
            print("⚠️ 登录状态可能已过期，需要重新扫码认证！")
            print("程序正在尝试调起扫码流程，请稍候并在浏览器中操作...")
            print("=" * 60 + "\n")
            
            # 将登录状态重置并触发重新登录
            self._browser._is_logged_in = False
            success = await self._browser.login(timeout_seconds=120)
            if not success:
                raise Exception("重新扫码登录失败或超时，流程中止。")
            
            print("\n✅ 重新扫码认证成功！继续自动发起页面导航...")
            logger.info("✅ Re-login successful, resuming navigation to editor...")
            
            # 使用新获取的 token 重新构建编辑器 URL
            token = self._browser.token
            editor_url = sel.MP_NEW_ARTICLE_URL.format(token=token) if token else sel.MP_DRAFT_URL
            
            await page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
            await self._random_delay(2, 4)

        # Wait for the title input to appear (indicates editor loaded)
        try:
            await page.wait_for_selector(
                sel.ARTICLE_TITLE_SELECTOR, timeout=15000
            )
            logger.info("✅ Article editor loaded")
        except Exception:
            # Maybe we got redirected, try to extract token from current URL
            current_url = page.url
            if "token=" in current_url:
                self._browser._extract_token(current_url)
            logger.warning("⚠️ Editor load uncertain, attempting to continue...")
            
            # Dump HTML for debugging exactly what the editor page looks like now
            try:
                html_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "wechat_auth",
                    "error_editor.html",
                )
                html_content = await page.content()
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
            except Exception:
                pass
                
        return page

    async def _fill_title(self, page, title: str):
        """Fill in the article title."""
        logger.info(f"  📌 Setting title: {title}")
        try:
            title_input = await page.wait_for_selector(
                sel.ARTICLE_TITLE_SELECTOR, timeout=10000
            )
            if title_input:
                await title_input.click()
                await self._random_delay(0.3, 0.8)
                await title_input.fill("")
                await title_input.type(title, delay=50)
                await self._random_delay()
        except Exception as e:
            logger.warning(f"Failed to set title via selector, trying JS: {e}")
            await page.evaluate(
                """(title) => {
                    const input = document.querySelector('#title')
                        || document.querySelector('input[name="title"]');
                    if (input) {
                        input.value = title;
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                }""",
                title,
            )

    async def _fill_content(self, page, content_html: str):
        """Inject HTML content into the editor iframe."""
        logger.info("  📝 Injecting article content...")
        try:
            # Try to find and interact with the editor iframe
            editor_frame = None

            # Method 1: direct iframe by selector
            iframe_element = await page.query_selector(sel.EDITOR_IFRAME_SELECTOR)
            if iframe_element:
                editor_frame = await iframe_element.content_frame()

            # Method 2: find iframe by iterating frames
            if not editor_frame:
                for frame in page.frames:
                    if "ueditor" in frame.url or frame.name == "ueditor_0":
                        editor_frame = frame
                        break

            if editor_frame:
                # Inject HTML content into the editor iframe body
                await editor_frame.evaluate(
                    """(html) => {
                        document.body.innerHTML = html;
                        // Trigger change events
                        document.body.dispatchEvent(
                            new Event('input', {bubbles: true})
                        );
                    }""",
                    content_html,
                )
                logger.info("  ✅ Content injected via iframe")
            else:
                # Fallback: try to find editable div directly
                logger.warning(
                    "  ⚠️ Editor iframe not found, trying direct content injection..."
                )
                await page.evaluate(
                    """(html) => {
                        // Try various editor selectors (ProseMirror is the new default)
                        const editors = [
                            document.querySelector('.ProseMirror[contenteditable="true"]'),
                            document.querySelector('.edui-body-container'),
                            document.querySelector('[contenteditable="true"]'),
                            document.querySelector('.rich_media_content'),
                        ];
                        for (const editor of editors) {
                            if (editor) {
                                editor.innerHTML = html;
                                editor.dispatchEvent(
                                    new Event('input', {bubbles: true})
                                );
                                break;
                            }
                        }
                    }""",
                    content_html,
                )

            await self._random_delay()

        except Exception as e:
            logger.error(f"  ❌ Failed to inject content: {e}")
            raise

    async def _fill_author(self, page, author: str):
        """Fill in the author field."""
        logger.info(f"  ✍️  Setting author: {author}")
        try:
            author_input = await page.query_selector(sel.ARTICLE_AUTHOR_SELECTOR)
            if author_input:
                await author_input.click()
                await author_input.fill("")
                await author_input.type(author, delay=30)
                await self._random_delay(0.5, 1.0)
        except Exception as e:
            logger.debug(f"Author field not found or not fillable: {e}")

    async def _fill_digest(self, page, digest: str):
        """Fill in the digest/summary field."""
        logger.info(f"  📋 Setting digest: {digest[:30]}...")
        try:
            digest_input = await page.query_selector(sel.ARTICLE_DIGEST_SELECTOR)
            if digest_input:
                await digest_input.click()
                await digest_input.fill("")
                await digest_input.type(digest, delay=30)
                await self._random_delay(0.5, 1.0)
        except Exception as e:
            logger.debug(f"Digest field not found or not fillable: {e}")

    async def _fill_source_url(self, page, url: str):
        """Fill in the original source URL field."""
        logger.info(f"  🔗 Setting source URL: {url}")
        try:
            url_input = await page.query_selector(sel.ORIGINAL_URL_SELECTOR)
            if url_input:
                await url_input.click()
                await url_input.fill(url)
                await self._random_delay(0.5, 1.0)
        except Exception as e:
            logger.debug(f"Source URL field not found: {e}")

    async def _upload_cover_image(self, page, image_path: str):
        """
        Upload a cover image for the article using the verified 8-phase flow.
        
        Phase 0: Scroll cover area into viewport
        Phase 1: Click cover area to open popup menu
        Phase 2: Click "从图片库选择" via JS (bypasses CSS visibility)
        Phase 3: Wait for image library modal + grid to render
        Phase 4: (Optional) Upload image file if not already in library
        Phase 5: Click a grid thumbnail to select it
        Phase 6: Click "下一步" (Next)
        Phase 7: Click "完成" in crop dialog
        """
        logger.info(f"  🖼️  Uploading cover image: {image_path}")
        debug_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "wechat_auth",
        )
        os.makedirs(debug_dir, exist_ok=True)
        
        try:
            # ── Phase 0: Scroll cover area into viewport ──
            logger.info("  [Phase 0] Scrolling cover area into viewport...")
            cover_locator = page.locator(sel.COVER_IMAGE_AREA_SELECTOR).first
            try:
                await cover_locator.scroll_into_view_if_needed(timeout=5000)
                await self._random_delay(1, 1.5)
            except Exception as e:
                logger.warning(f"  [Phase 0] scroll failed: {e}")

            # ── Phase 1: Click cover area to open popup ──
            logger.info("  [Phase 1] Clicking cover area...")
            await cover_locator.click(timeout=5000)
            await self._random_delay(1.5, 2.5)
            
            # ── Phase 2: Click "从图片库选择" via JS ──
            logger.info("  [Phase 2] Clicking '从图片库选择' via JS...")
            clicked = await page.evaluate("""() => {
                const el = document.querySelector('a.js_imagedialog');
                if (el) { el.click(); return true; }
                // Fallback: find by text
                const all = document.querySelectorAll('a, li');
                for (const a of all) {
                    if ((a.textContent || '').trim() === '从图片库选择') {
                        a.click(); return true;
                    }
                }
                return false;
            }""")
            if not clicked:
                logger.error("  [Phase 2] FAILED: Could not find '从图片库选择'")
                await page.screenshot(path=os.path.join(debug_dir, "cover_phase2_fail.png"))
                return
            await self._random_delay(3, 5)
            
            # ── Phase 3: Wait for image library modal + grid ──
            logger.info("  [Phase 3] Waiting for image library modal...")
            try:
                # Wait for the grid items to appear (they render async)
                await page.wait_for_selector(
                    sel.IMAGE_LIB_ITEM, state="visible", timeout=10000
                )
                logger.info("  [Phase 3] ✅ Image grid loaded")
            except Exception as e:
                logger.error(f"  [Phase 3] Grid did not appear: {e}")
                await page.screenshot(path=os.path.join(debug_dir, "cover_phase3_fail.png"))
                return
            
            # ── Phase 4: Upload image if needed ──
            # Check if we should upload or just select an existing image
            should_upload = image_path and os.path.exists(image_path)
            if should_upload:
                logger.info(f"  [Phase 4] Uploading image file: {image_path}")
                # Find the modal's file input (NOT the toolbar one)
                modal_file_input = await page.query_selector(sel.IMAGE_LIB_FILE_INPUT)
                if modal_file_input:
                    await modal_file_input.set_input_files(os.path.abspath(image_path))
                    logger.info("  [Phase 4] File set, waiting for upload...")
                    await self._random_delay(4, 6)
                    # Wait for the uploaded image to appear in the grid
                    try:
                        await page.wait_for_selector(
                            sel.IMAGE_LIB_ITEM, state="visible", timeout=10000
                        )
                    except Exception:
                        pass
                else:
                    logger.warning("  [Phase 4] Modal file input not found, selecting existing image")
            
            await page.screenshot(path=os.path.join(debug_dir, "cover_phase4_grid.png"))
            
            # ── Phase 5: Click first grid thumbnail to select it ──
            logger.info("  [Phase 5] Selecting first image in grid...")
            grid_items = page.locator(sel.IMAGE_LIB_ITEM)
            item_count = await grid_items.count()
            if item_count == 0:
                logger.error("  [Phase 5] No grid items found!")
                await page.screenshot(path=os.path.join(debug_dir, "cover_phase5_fail.png"))
                return
            
            logger.info(f"  [Phase 5] Found {item_count} grid items, clicking first...")
            await grid_items.first.click()
            await self._random_delay(1, 2)
            
            await page.screenshot(path=os.path.join(debug_dir, "cover_phase5_selected.png"))
            
            # ── Phase 6: Click "下一步" ──
            logger.info("  [Phase 6] Clicking '下一步'...")
            # Verify the button is not disabled
            next_btn_enabled = await page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const text = (btn.textContent || '').trim();
                    if (text === '下一步') {
                        if (btn.classList.contains('weui-desktop-btn_disabled')) {
                            return {found: true, disabled: true};
                        }
                        btn.click();
                        return {found: true, disabled: false, clicked: true};
                    }
                }
                return {found: false};
            }""")
            logger.info(f"  [Phase 6] 下一步 result: {next_btn_enabled}")
            
            if not next_btn_enabled.get("clicked"):
                if next_btn_enabled.get("disabled"):
                    logger.warning("  [Phase 6] 下一步 is disabled — image may not be truly selected")
                    # Retry: try clicking the grid item again with JS
                    await page.evaluate("""() => {
                        const items = document.querySelectorAll('.weui-desktop-img-picker__item');
                        if (items.length > 0) items[0].click();
                    }""")
                    await self._random_delay(1, 2)
                    # Try next again
                    await page.evaluate("""() => {
                        const btns = document.querySelectorAll('button');
                        for (const btn of btns) {
                            if ((btn.textContent || '').trim() === '下一步' && 
                                !btn.classList.contains('weui-desktop-btn_disabled')) {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                else:
                    logger.error("  [Phase 6] 下一步 button not found!")
                    await page.screenshot(path=os.path.join(debug_dir, "cover_phase6_fail.png"))
                    return
            
            await self._random_delay(2, 4)
            await page.screenshot(path=os.path.join(debug_dir, "cover_phase6_crop.png"))
            
            # ── Phase 7: Click "完成" / "确定" in crop dialog ──
            # ⚠️ 裁剪图片需要加载时间！必须等按钮出现后再点
            logger.info("  [Phase 7] 等待裁剪图片加载并点击确认...")
            finish_clicked = False
            for attempt in range(15):  # 最多等 15 秒
                btn_result = await page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    const found = [];
                    for (const btn of btns) {
                        const text = (btn.textContent || '').trim();
                        const rect = btn.getBoundingClientRect();
                        if ((text === '确认' || text === '完成' || text === '确定') && rect.width > 0) {
                            found.push({text: text, disabled: btn.disabled, w: rect.width});
                            if (!btn.disabled) {
                                btn.click();
                                return {clicked: true, text: text};
                            }
                        }
                    }
                    return {clicked: false, found: found};
                }""")
                if btn_result.get('clicked'):
                    finish_clicked = True
                    logger.info(f"  [Phase 7] ✅ 裁剪确认已点击 (尝试 {attempt+1}次): {btn_result}")
                    break
                logger.debug(f"  [Phase 7] 等待裁剪确认按钮... ({attempt+1}/15) found={btn_result.get('found', [])}")
                await asyncio.sleep(1)
            
            if not finish_clicked:
                # 降级: 用 Playwright locator 尝试点击
                logger.warning("  [Phase 7] JS点击失败, 尝试 Playwright locator...")
                try:
                    crop_confirm = page.locator('button:has-text("确定"), button:has-text("完成"), button:has-text("确认")').first
                    await crop_confirm.click(timeout=5000)
                    finish_clicked = True
                    logger.info("  [Phase 7] ✅ 通过 Playwright locator 点击了裁剪确认")
                except Exception as e:
                    logger.warning(f"  [Phase 7] ⚠️ 裁剪确认按钮始终未出现或不可点击: {e}")
                    await page.screenshot(path=os.path.join(debug_dir, "cover_phase7_fail.png"))
            
            # 等待裁剪弹窗关闭
            if finish_clicked:
                logger.info("  [Phase 7] 等待裁剪弹窗关闭...")
                for i in range(10):
                    has_crop_dialog = await page.evaluate("""() => {
                        const dlgs = document.querySelectorAll('.weui-desktop-dialog');
                        for (const d of dlgs) {
                            if (d.getBoundingClientRect().width > 100) return true;
                        }
                        return false;
                    }""")
                    if not has_crop_dialog:
                        logger.info(f"  [Phase 7] ✅ 裁剪弹窗已关闭 (耗时 {i+1}s)")
                        break
                    await asyncio.sleep(1)
            
            await self._random_delay(1, 2)
            
            # ── Phase 8: Verify cover is set ──
            logger.info("  [Phase 8] Verifying cover image...")
            cover_text = await page.evaluate("""() => {
                const el = document.querySelector('.js_cover_btn_area, .select-cover__btn');
                return el ? el.textContent.trim() : '';
            }""")
            if '拖拽或选择封面' in cover_text:
                logger.warning("  [Phase 8] ⚠️ Cover area still shows placeholder — cover may not be set")
                await page.screenshot(path=os.path.join(debug_dir, "cover_phase8_fail.png"))
            else:
                logger.info("  [Phase 8] ✅ Cover image appears to be set!")
            
            await page.screenshot(path=os.path.join(debug_dir, "cover_final.png"))
            logger.info("  ✅ Cover image flow completed")
            
        except Exception as e:
            logger.warning(f"  ⚠️ Cover image upload failed: {e}")
            try:
                await page.screenshot(path=os.path.join(debug_dir, "cover_error.png"))
            except Exception:
                pass

    async def _declare_original_and_appreciation(self, page, appreciation_author: str = "Bhrum"):
        """
        声明原创 + 开启赞赏 的完整自动化流程。
        
        正确的UI流程：
        1. 点击"未声明 >" 打开原创声明弹窗
        2. 在弹窗中配置原创选项 → 勾选同意 → 点击"确定"
        3. **等待弹窗完全消失**（这是关键！弹窗不消失则赞赏按钮不可交互）
        4. 弹窗消失后，在主页面上点击"赞赏"开关/按钮
        
        Phase A: 滚动到原创区域 → 点击 "未声明 >" 打开原创声明弹窗
        Phase B: 在弹窗中：选择"文字原创" → 勾选同意 → 确定
        Phase C: 轮询等待弹窗完全消失（最多15秒）
        Phase D: 弹窗消失后，开启赞赏
        """
        logger.info("  🏷️ [原创+赞赏] 开始声明原创和开启赞赏...")
        debug_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "wechat_auth",
        )
        os.makedirs(debug_dir, exist_ok=True)

        async def _ss(name: str):
            try:
                path = os.path.join(debug_dir, f"original_{name}.png")
                await page.screenshot(path=path)
                return path
            except Exception:
                return None

        async def _html(name: str):
            try:
                path = os.path.join(debug_dir, f"original_{name}.html")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(await page.content())
                return path
            except Exception:
                return None

        async def _close_all_dialogs():
            """安全关闭所有弹窗，防止阻塞后续操作"""
            await page.evaluate('''() => {
                const dlgs = document.querySelectorAll('.weui-desktop-dialog');
                for (const dlg of dlgs) {
                    if (dlg.getBoundingClientRect().width < 100) continue;
                    const closeBtn = dlg.querySelector('.weui-desktop-dialog__close-btn');
                    if (closeBtn) { closeBtn.click(); continue; }
                    const btns = dlg.querySelectorAll('button');
                    for (const btn of btns) {
                        if ((btn.innerText || '').trim() === '取消') { btn.click(); break; }
                    }
                }
            }''')
            await asyncio.sleep(1)

        async def _wait_for_dialog_close(max_wait: int = 15) -> bool:
            """轮询等待所有原创相关弹窗消失，返回是否成功关闭"""
            for i in range(max_wait):
                has_dialog = await page.evaluate('''() => {
                    const dlgs = document.querySelectorAll('.weui-desktop-dialog');
                    for (const d of dlgs) {
                        const r = d.getBoundingClientRect();
                        if (r.width > 100) return true;
                    }
                    return false;
                }''')
                if not has_dialog:
                    logger.info(f"  [等待弹窗关闭] ✅ 弹窗已消失 (耗时 {i+1}s)")
                    return True
                logger.debug(f"  [等待弹窗关闭] 弹窗仍在... ({i+1}/{max_wait}s)")
                await asyncio.sleep(1)
            logger.warning(f"  [等待弹窗关闭] ⚠️ 等待 {max_wait}s 后弹窗仍未关闭")
            return False

        try:
            # ═══════════════════════════════════════════════════════════
            # 预检查: 关闭任何残留弹窗（如封面裁剪弹窗未关闭的情况）
            # ═══════════════════════════════════════════════════════════
            pre_dialog = await page.evaluate('''() => {
                const dlgs = document.querySelectorAll('.weui-desktop-dialog');
                for (const d of dlgs) {
                    if (d.getBoundingClientRect().width > 100)
                        return {open: true, text: d.innerText.substring(0, 100)};
                }
                return {open: false};
            }''')
            if pre_dialog.get('open'):
                logger.warning(f"  [预检查] ⚠️ 发现残留弹窗，先关闭: {pre_dialog.get('text', '')[:60]}")
                await _close_all_dialogs()
                await asyncio.sleep(2)

            # ═══════════════════════════════════════════════════════════
            # Phase A: 点击 "未声明 >" 打开原创声明弹窗
            # ═══════════════════════════════════════════════════════════
            logger.info("  [PhaseA] 滚动并点击 '原创' → '未声明 >'...")
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await asyncio.sleep(1)

            clicked = await page.evaluate('''() => {
                const allEls = document.querySelectorAll('a, span, div, button, label');
                for (const el of allEls) {
                    const t = (el.innerText || '').trim();
                    if (t.includes('未声明') && !t.includes('赞赏') && el.getBoundingClientRect().width > 5) {
                        el.click();
                        return {clicked: true, text: t.substring(0, 40)};
                    }
                }
                return {clicked: false};
            }''')
            logger.info(f"  [PhaseA] 点击结果: {clicked}")

            if not clicked.get('clicked'):
                await _ss("phaseA_fail")
                await _html("phaseA_fail")
                logger.warning("  [PhaseA] ⚠️ 未找到'未声明'链接，跳过原创声明")
                return

            await asyncio.sleep(3)
            await _ss("phaseA_after_click")

            # 检查弹窗是否弹出
            has_dialog = await page.evaluate('''() => {
                const dlgs = document.querySelectorAll('.weui-desktop-dialog');
                for (const d of dlgs) {
                    if (d.getBoundingClientRect().width > 100 && d.innerText.includes('原创'))
                        return true;
                }
                return false;
            }''')
            if not has_dialog:
                logger.warning("  [PhaseA] 点击后没有弹窗出现")
                await _html("phaseA_no_dialog")
                return

            # ═══════════════════════════════════════════════════════════
            # Phase B: 在原创弹窗内配置选项（仅原创，不含赞赏）
            # ═══════════════════════════════════════════════════════════
            logger.info("  [PhaseB] 配置原创弹窗...")

            # B1: "文字原创" 通常已默认选中，确认一下
            await page.evaluate('''() => {
                const dlg = [...document.querySelectorAll('.weui-desktop-dialog')]
                    .find(d => d.getBoundingClientRect().width > 100 && d.innerText.includes('原创'));
                if (!dlg) return;
                const els = dlg.querySelectorAll('label, span');
                for (const el of els) {
                    if ((el.innerText || '').trim() === '文字原创') {
                        el.click(); break;
                    }
                }
            }''')
            await asyncio.sleep(0.5)

            # B2: 勾选 "我已阅读并同意" — 用 JS 精确定位，避免误触
            # ⚠️ 弹窗内有多个 checkbox：赞赏开关(js_reward_switch)是隐藏的，会被 locator 优先匹配到！
            # ⚠️ label 内包含《协议》超链接，点击 label 会误触链接打开新页面！
            # 所以必须用 JS 精准找到"我已阅读"旁边的 checkbox 并直接 click
            logger.info("  [PhaseB] 勾选同意协议（JS精确定位）...")
            
            # 记录当前页面数量，用于检测是否误开了新标签页
            pages_before = len(page.context.pages)
            
            checkbox_result = await page.evaluate('''() => {
                const dlg = [...document.querySelectorAll('.weui-desktop-dialog')]
                    .find(d => d.getBoundingClientRect().width > 100 && d.innerText.includes('原创'));
                if (!dlg) return {error: 'no_dialog'};
                
                // 找到所有 checkbox，排除赞赏开关
                const allCbs = dlg.querySelectorAll('input[type="checkbox"]');
                const result = {total: allCbs.length, details: []};
                
                for (const cb of allCbs) {
                    const classes = cb.className || '';
                    const parent = cb.closest('label, div, span');
                    const parentText = parent ? (parent.innerText || '').trim().substring(0, 50) : '';
                    const isRewardSwitch = classes.includes('reward') || classes.includes('switch');
                    
                    result.details.push({
                        classes: classes.substring(0, 50),
                        parentText: parentText,
                        isRewardSwitch: isRewardSwitch,
                        checked: cb.checked,
                        visible: cb.getBoundingClientRect().height > 0
                    });
                    
                    // 跳过赞赏开关 checkbox
                    if (isRewardSwitch) continue;
                    
                    // 这个就是"我已阅读"的 checkbox
                    if (!cb.checked) {
                        cb.click();
                        result.clicked = true;
                        result.target = 'agree_checkbox';
                    } else {
                        result.alreadyChecked = true;
                    }
                }
                
                // 如果上面没找到（都是 switch 类型），降级：点击所有非 switch 的 checkbox
                if (!result.clicked && !result.alreadyChecked) {
                    for (const cb of allCbs) {
                        if (!(cb.className || '').includes('switch') && !(cb.className || '').includes('reward')) {
                            cb.checked = true;
                            cb.dispatchEvent(new Event('change', {bubbles: true}));
                            result.clicked = true;
                            result.target = 'fallback_set_checked';
                            break;
                        }
                    }
                }
                
                return result;
            }''')
            logger.info(f"  [PhaseB] checkbox操作结果: {checkbox_result}")
            
            await asyncio.sleep(1)
            
            # 检查是否误开了新标签页（点到了协议链接），如果有就关掉
            pages_after = len(page.context.pages)
            if pages_after > pages_before:
                logger.warning(f"  [PhaseB] ⚠️ 检测到新标签页被打开（{pages_before} → {pages_after}），关闭多余标签页...")
                for p in page.context.pages:
                    if p != page:
                        try:
                            await p.close()
                        except Exception:
                            pass
                await asyncio.sleep(0.5)
            
            await _ss("phaseB_after_agree")

            # B3: 点击 "确定" — 用 Playwright locator 点击（比 JS evaluate 更可靠）
            logger.info("  [PhaseB] 点击确定...")
            
            # 先诊断按钮状态
            btn_info = await page.evaluate('''() => {
                const dlg = [...document.querySelectorAll('.weui-desktop-dialog')]
                    .find(d => d.getBoundingClientRect().width > 100 && d.innerText.includes('原创'));
                if (!dlg) return {error: 'no_dialog'};
                const btns = dlg.querySelectorAll('button');
                const info = [];
                for (const btn of btns) {
                    const t = (btn.innerText || '').trim();
                    const r = btn.getBoundingClientRect();
                    info.push({text: t, disabled: btn.disabled, w: r.width, h: r.height, x: r.x, y: r.y, classes: btn.className});
                }
                // 同时检查 checkbox 状态
                const cbs = dlg.querySelectorAll('input[type="checkbox"]');
                const cbInfo = [];
                for (const cb of cbs) { cbInfo.push({checked: cb.checked}); }
                return {buttons: info, checkboxes: cbInfo};
            }''')
            logger.info(f"  [PhaseB] 弹窗内按钮/checkbox状态: {btn_info}")
            
            confirm_clicked = False
            try:
                # 策略1: Playwright locator 点击可见弹窗内的"确定"按钮
                confirm_btn = page.locator('.weui-desktop-dialog:visible').locator('button:has-text("确定")').first
                if await confirm_btn.count() > 0:
                    is_disabled = await confirm_btn.is_disabled()
                    logger.info(f"  [PhaseB] 找到确定按钮, disabled={is_disabled}")
                    if not is_disabled:
                        await confirm_btn.click(timeout=5000)
                        confirm_clicked = True
                        logger.info("  [PhaseB] ✅ 已通过 Playwright locator 点击确定")
                    else:
                        logger.warning("  [PhaseB] ⚠️ 确定按钮是禁用状态!")
            except Exception as e:
                logger.warning(f"  [PhaseB] Playwright locator 点击确定失败: {e}")
            
            if not confirm_clicked:
                # 策略2: 降级用 JS dispatchEvent 模拟点击
                logger.info("  [PhaseB] 降级: 用 JS dispatchEvent 点击确定...")
                await page.evaluate('''() => {
                    const dlg = [...document.querySelectorAll('.weui-desktop-dialog')]
                        .find(d => d.getBoundingClientRect().width > 100 && d.innerText.includes('原创'));
                    if (!dlg) return;
                    const btns = dlg.querySelectorAll('button');
                    for (const btn of btns) {
                        const t = (btn.innerText || '').trim();
                        if ((t === '确定' || t === '确认') && !btn.disabled) {
                            btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            return;
                        }
                    }
                }''')


            # ═══════════════════════════════════════════════════════════
            # Phase C: 轮询等待弹窗完全消失（关键步骤！）
            # ═══════════════════════════════════════════════════════════
            logger.info("  [PhaseC] 等待原创确认弹窗消失...")
            dialog_closed = await _wait_for_dialog_close(max_wait=15)
            await _ss("phaseC_result")

            if not dialog_closed:
                # 弹窗未关闭，导出诊断信息
                await _html("phaseC_dialog_still_open")
                remaining_text = await page.evaluate('''() => {
                    const dlgs = document.querySelectorAll('.weui-desktop-dialog');
                    for (const d of dlgs) {
                        if (d.getBoundingClientRect().width > 100)
                            return d.innerText.substring(0, 200);
                    }
                    return '';
                }''')
                logger.warning(f"  [PhaseC] ⚠️ 弹窗仍然打开: {remaining_text[:100]}")
                
                # 最终兜底：强制关闭弹窗
                logger.info("  [PhaseC] 强制关闭残留弹窗...")
                await _close_all_dialogs()
                # 关闭后再等一下确保UI稳定
                await asyncio.sleep(2)

            # 验证原创是否声明成功
            status = await page.evaluate('''() => {
                const allEls = document.querySelectorAll('a, span, div');
                for (const el of allEls) {
                    const t = (el.innerText || '').trim();
                    // 页面实际显示: "文字原创 · 作者: xxx · 已开启快捷转载 >"
                    if (t.includes('文字原创') || t.includes('已声明') || t.includes('修改声明'))
                        return {declared: true, text: t.substring(0, 60)};
                }
                return {declared: false};
            }''')
            if status.get('declared'):
                logger.info(f"  [PhaseC] ✅ 原创声明成功! {status}")
            else:
                logger.warning(f"  [PhaseC] ⚠️ 未确认原创声明状态: {status}")

            # ═══════════════════════════════════════════════════════════
            # Phase D: 弹窗消失后，开启赞赏（赞赏在主页面上操作）
            # ═══════════════════════════════════════════════════════════
            logger.info("  [PhaseD] 弹窗已关闭，现在开启赞赏...")
            await asyncio.sleep(1)  # 等待UI完全稳定
            await _ss("phaseD_before_reward")

            # D1: 在主页面上查找并点击赞赏行的"不开启 >"链接
            # 根据截图，页面布局是: 左边"赞赏"标签 | 右边"不开启 >"链接
            reward_clicked = await page.evaluate('''() => {
                const allEls = document.querySelectorAll('a, span, div, button, label');
                const candidates = [];
                
                for (const el of allEls) {
                    const t = (el.innerText || '').trim();
                    const r = el.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) continue;
                    const inDialog = el.closest('.weui-desktop-dialog');
                    if (inDialog && inDialog.getBoundingClientRect().width > 100) continue;
                    
                    if (t.includes('赞赏') || t.includes('不开启')) {
                        candidates.push({text: t.substring(0, 50), tag: el.tagName, w: r.width, h: r.height});
                    }
                }
                
                // 策略1: 直接找"不开启"链接（赞赏行右侧的操作入口）
                for (const el of document.querySelectorAll('a, span')) {
                    const t = (el.innerText || '').trim();
                    const r = el.getBoundingClientRect();
                    if (r.width < 5) continue;
                    const inDialog = el.closest('.weui-desktop-dialog');
                    if (inDialog && inDialog.getBoundingClientRect().width > 100) continue;
                    
                    // 匹配赞赏行: "不开启" 或 "不开启 >" 或包含赞赏的可点击元素
                    if (t === '不开启' || t === '不开启 >' || t === '不开启 \u003e') {
                        // 确认这个"不开启"是赞赏行的（检查相邻元素或父容器）
                        const parent = el.closest('div, li, tr, [class]');
                        if (parent && (parent.innerText || '').includes('赞赏')) {
                            el.click();
                            return {clicked: true, text: t, strategy: '不开启_in_赞赏_row', candidates: candidates};
                        }
                    }
                }
                
                // 策略2: 找赞赏行整体并点击右侧操作区
                // ⚠️ 注意："已开启快捷转载 >" 在原创行中也包含"开启"，不能用 includes('开启') 匹配！
                for (const el of document.querySelectorAll('a, span, div')) {
                    const t = (el.innerText || '').trim();
                    const r = el.getBoundingClientRect();
                    if (r.width < 5) continue;
                    const inDialog = el.closest('.weui-desktop-dialog');
                    if (inDialog && inDialog.getBoundingClientRect().width > 100) continue;
                    
                    // 严格匹配：必须包含"赞赏"且包含"不开启"（不能只匹配"开启"，会误触原创行的"已开启快捷转载"）
                    if (t.includes('赞赏') && t.includes('不开启')) {
                        // 尝试点击右侧的"不开启"链接
                        const links = el.querySelectorAll('a, span');
                        for (const link of links) {
                            const lt = (link.innerText || '').trim();
                            // 只匹配"不开启"，严格排除"已开启快捷转载"等
                            if (lt.includes('不开启') && !lt.includes('转载') && !lt.includes('原创')) {
                                link.click();
                                return {clicked: true, text: lt, strategy: 'inner_link', candidates: candidates};
                            }
                        }
                        // 降级: 点击整行
                        el.click();
                        return {clicked: true, text: t.substring(0, 50), strategy: 'row_click', candidates: candidates};
                    }
                }
                
                // 策略3: 点击赞赏区域的 toggle/switch
                const switches = document.querySelectorAll('.weui-desktop-switch, [class*="switch"], [class*="toggle"]');
                for (const sw of switches) {
                    const parent = sw.closest('div, label, span');
                    if (parent && (parent.innerText || '').includes('赞赏')) {
                        sw.click();
                        return {clicked: true, text: '赞赏 switch', strategy: 'switch', candidates: candidates};
                    }
                }
                
                return {clicked: false, candidates: candidates};
            }''')
            logger.info(f"  [PhaseD] 赞赏点击结果: {reward_clicked}")
            await asyncio.sleep(2)
            await _ss("phaseD_after_reward_click")

            if reward_clicked.get('clicked'):
                # D2: 如果弹出了赞赏配置弹窗，选择赞赏账户并确认
                await asyncio.sleep(2)
                reward_dialog = await page.evaluate('''() => {
                    const dlgs = document.querySelectorAll('.weui-desktop-dialog');
                    for (const d of dlgs) {
                        const r = d.getBoundingClientRect();
                        if (r.width > 100 && d.innerText.includes('赞赏'))
                            return {open: true, text: d.innerText.substring(0, 200)};
                    }
                    return {open: false};
                }''')
                
                if reward_dialog.get('open'):
                    logger.info(f"  [PhaseD] 赞赏配置弹窗已弹出: {reward_dialog.get('text', '')[:80]}")
                    await _ss("phaseD_reward_dialog")
                    
                    # 选择赞赏账户
                    logger.info("  [PhaseD] 选择赞赏账户...")
                    await page.evaluate('''() => {
                        const dlg = [...document.querySelectorAll('.weui-desktop-dialog')]
                            .find(d => d.getBoundingClientRect().width > 100 && d.innerText.includes('赞赏'));
                        if (!dlg) return;
                        
                        // 策略1: 点击"请选择赞赏账户"链接
                        const links = dlg.querySelectorAll('a, span, div');
                        for (const el of links) {
                            const t = (el.innerText || '').trim();
                            if ((t.includes('选择赞赏') || t.includes('赞赏账户')) && 
                                el.getBoundingClientRect().width > 5) {
                                el.click();
                                break;
                            }
                        }
                    }''')
                    await asyncio.sleep(2)
                    
                    # 选择第一个可用的赞赏账户
                    await page.evaluate('''() => {
                        // 查找下拉菜单/列表项
                        const items = document.querySelectorAll(
                            '.weui-desktop-dropdown__item, .weui-desktop-popover__item, ' +
                            '.weui-desktop-menu__link, li[class*="item"], ' +
                            '.reward-account-item, [class*="account"] li'
                        );
                        for (const item of items) {
                            const r = item.getBoundingClientRect();
                            const t = (item.innerText || '').trim();
                            if (r.width > 10 && r.height > 10 && t && !t.includes('取消')) {
                                item.click();
                                return;
                            }
                        }
                        
                        // 策略2: radio buttons
                        const radios = document.querySelectorAll('input[type="radio"]');
                        for (const r of radios) {
                            const p = r.closest('label, div, li');
                            if (p && p.getBoundingClientRect().width > 10) {
                                r.click();
                                return;
                            }
                        }
                    }''')
                    await asyncio.sleep(2)
                    await _ss("phaseD_after_account_select")
                    
                    # 勾选同意并确认
                    await page.evaluate('''() => {
                        const dlg = [...document.querySelectorAll('.weui-desktop-dialog')]
                            .find(d => d.getBoundingClientRect().width > 100 && d.innerText.includes('赞赏'));
                        if (!dlg) return;
                        
                        // 勾选 checkbox
                        const cbs = dlg.querySelectorAll('input[type="checkbox"]');
                        for (const cb of cbs) {
                            if (!cb.checked) cb.click();
                        }
                    }''')
                    await asyncio.sleep(0.5)
                    
                    # 点击确定
                    await page.evaluate('''() => {
                        const dlg = [...document.querySelectorAll('.weui-desktop-dialog')]
                            .find(d => d.getBoundingClientRect().width > 100 && d.innerText.includes('赞赏'));
                        if (!dlg) return;
                        const btns = dlg.querySelectorAll('button');
                        for (const btn of btns) {
                            const t = (btn.innerText || '').trim();
                            if ((t === '确定' || t === '确认') && !btn.disabled) {
                                btn.click(); return;
                            }
                        }
                    }''')
                    
                    # 等待赞赏弹窗也关闭
                    logger.info("  [PhaseD] 等待赞赏弹窗关闭...")
                    await _wait_for_dialog_close(max_wait=10)
                    
                logger.info("  [PhaseD] ✅ 赞赏开启流程完成")
            else:
                logger.warning(f"  [PhaseD] ⚠️ 未找到赞赏按钮/开关, candidates: {reward_clicked.get('candidates', [])}")
                await _html("phaseD_no_reward_btn")
            
            await _ss("final")

        except Exception as e:
            logger.warning(f"  [原创+赞赏] ⚠️ 异常 (不中断主流程): {e}")
            try:
                await _ss("error")
                await _html("error")
                await _close_all_dialogs()
            except Exception:
                pass

    async def _do_save_draft(self, page) -> dict:
        """Click the save-as-draft button."""
        logger.info("  💾 Saving as draft...")
        try:
            # Look for the save draft button
            save_btn = await page.query_selector(sel.SAVE_DRAFT_BUTTON_SELECTOR)
            if save_btn:
                await save_btn.click()
                await self._random_delay(2, 4)

                # Check for confirmation dialog
                try:
                    confirm_btn = await page.wait_for_selector(
                        sel.CONFIRM_DIALOG_OK_SELECTOR, timeout=5000
                    )
                    if confirm_btn:
                        await confirm_btn.click()
                        await self._random_delay(2, 3)
                except Exception:
                    pass  # No confirmation dialog

                # Check for success
                try:
                    await page.wait_for_selector(
                        sel.SUCCESS_TOAST_SELECTOR, timeout=10000
                    )
                    logger.info("  ✅ Draft saved successfully!")
                    return {
                        "success": True,
                        "message": "文章已保存为草稿",
                        "mode": "draft",
                    }
                except Exception:
                    # No explicit success toast, check if URL changed indicating success
                    pass

                logger.info("  ✅ Draft save operation completed")
                return {
                    "success": True,
                    "message": "草稿保存操作已完成（请在公众号后台确认）",
                    "mode": "draft",
                }
            else:
                # Try JavaScript click as fallback
                clicked = await page.evaluate(
                    """() => {
                        const btns = document.querySelectorAll('a, button');
                        for (const btn of btns) {
                            const text = btn.textContent || btn.innerText || '';
                            if (text.includes('保存') || text.includes('草稿')) {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    }"""
                )
                if clicked:
                    await self._random_delay(3, 5)
                    return {
                        "success": True,
                        "message": "草稿保存操作已完成（通过JS点击）",
                        "mode": "draft",
                    }

                return {
                    "success": False,
                    "message": "未找到保存草稿按钮",
                    "mode": "draft",
                }

        except Exception as e:
            return {
                "success": False,
                "message": f"保存草稿失败: {str(e)}",
                "mode": "draft",
            }

    async def _do_schedule_publish(self, page, publish_time) -> dict:
        """Execute the scheduled publish flow with full diagnostics."""
        debug_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "wechat_auth",
        )
        os.makedirs(debug_dir, exist_ok=True)

        async def _dump_page_state(step_name: str) -> dict:
            """截图 + 分析当前页面所有可见弹窗和交互元素，返回诊断数据。"""
            screenshot_path = os.path.join(debug_dir, f"sched_{step_name}.png")
            try:
                await page.screenshot(path=screenshot_path)
            except Exception:
                pass

            info = await page.evaluate('''() => {
                const result = { dialogs: [], buttons: [], checkboxes: [], inputs: [], url: location.href, bodyText: '' };
                // 可见弹窗
                for (const el of document.querySelectorAll('.weui-desktop-dialog, [role="dialog"], .dialog, .modal')) {
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    if (r.width > 100 && r.height > 50 && s.display !== 'none') {
                        result.dialogs.push({
                            cls: el.className.substring(0, 100),
                            text: el.innerText.substring(0, 500),
                            rect: {w: Math.round(r.width), h: Math.round(r.height)},
                        });
                    }
                }
                // 可见按钮
                for (const btn of document.querySelectorAll('button, a.weui-desktop-btn')) {
                    const r = btn.getBoundingClientRect();
                    if (r.width > 30 && r.height > 15 && r.y > 0 && r.y < window.innerHeight + 200) {
                        result.buttons.push({
                            text: (btn.innerText || '').trim().substring(0, 40),
                            cls: (btn.className || '').substring(0, 80),
                            disabled: btn.disabled || btn.classList.contains('weui-desktop-btn_disabled'),
                        });
                    }
                }
                // checkbox / switch
                for (const cb of document.querySelectorAll('input[type="checkbox"], .weui-desktop-switch__input')) {
                    const r = cb.getBoundingClientRect();
                    const p = cb.closest('div, label, li');
                    result.checkboxes.push({
                        cls: (cb.className || '').substring(0, 80),
                        checked: cb.checked,
                        parentText: p ? p.innerText.substring(0, 60).replace(/\\n/g, ' ') : '',
                        visible: r.width > 0,
                    });
                }
                // inputs
                for (const inp of document.querySelectorAll('input[type="text"], input:not([type])')) {
                    const r = inp.getBoundingClientRect();
                    if (r.width > 30) {
                        result.inputs.push({
                            name: inp.name || inp.id || '',
                            value: inp.value,
                            placeholder: inp.placeholder,
                        });
                    }
                }
                result.bodyText = document.body.innerText.substring(0, 300);
                return result;
            }''')
            info['screenshot'] = screenshot_path
            try:
                import json
                state_path = os.path.join(debug_dir, f"state_{step_name}.json")
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump(info, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"Failed to dump state json: {e}")
            return info

        try:
            logger.info(f"⏳ Schedule publish at {publish_time.strftime('%Y-%m-%d %H:%M')}")

            # ── Step 1: Click 发表 ──
            logger.info("  [Step1] 尝试点击发表并等待配置弹窗出现...")
            # 考虑到配置了原创/赞赏后，微信后台通常会自动保存(页面会有短暂的"保存成功"toast，此时发表按钮会短暂变灰)。
            # 直接检查状态容易遇到 false-positive（在变灰前检查到了 enabled），因此采用轮询点击并验证弹窗是否出现。
            
            publish_opened = False
            for attempt in range(12):  # 最多尝试约 24 秒
                # 尝试用 JS 点击发表按钮 (跳过 disabled 状态)
                await page.evaluate('''() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const btn = btns.reverse().find(b => (b.innerText || '').trim() === '发表' && !b.closest('.weui-desktop-dialog'));
                    if (btn && !btn.disabled && !btn.className.includes('disabled')) {
                        btn.click();
                    }
                }''')
                
                await asyncio.sleep(2)  # 等待弹窗弹出和动画
                
                # 检查发表配置弹窗是否出现（群发确认、定时确认等弹窗）
                has_dialog = await page.evaluate('''() => {
                    for (const el of document.querySelectorAll('.weui-desktop-dialog')) {
                        if (el.getBoundingClientRect().width > 100) return true;
                    }
                    return false;
                }''')
                
                if has_dialog:
                    publish_opened = True
                    logger.info(f"  [Step1] ✅ 成功弹出发表配置弹窗 (尝试 {attempt+1} 次)")
                    break
                else:
                    logger.debug(f"  [Step1] 尚未出现弹窗，等待/重试... ({attempt+1}/12)")
            
            if not publish_opened:
                logger.warning("  [Step1] ⚠️ 尝试多次点击发表后仍未出现弹窗，导出状态...")

            state1 = await _dump_page_state("step1_after_publish_click")
            logger.info(f"  [Step1] 页面状态: {len(state1['dialogs'])} 个弹窗, {len(state1['buttons'])} 个按钮")
            
            # ALWAYS dump step1 HTML heavily requested for DOM analysis
            try:
                html_path = os.path.join(debug_dir, "sched_step1_diagnostic.html")
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(await page.content())
            except Exception:
                pass

            if not state1['dialogs']:
                # 没有弹窗弹出 → 可能发表条件不满足
                err_msg = f"点击发表后没有弹窗出现。截图: {state1['screenshot']}"
                logger.error(f"  ❌ {err_msg}")
                # 导出完整 HTML
                html_path = os.path.join(debug_dir, "sched_step1_no_dialog.html")
                html = await page.content()
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html)
                return {"success": False, "message": err_msg,
                        "debug": {"screenshot": state1['screenshot'], "html": html_path,
                                  "buttons": state1['buttons'][:10]}}

            # ── Step 2: 分析第一个弹窗 ──
            first_dialog = state1['dialogs'][0]
            dialog_text = first_dialog['text']
            logger.info(f"  [Step2] 弹窗内容关键词: {dialog_text[:100]}...")

            # ── Step 2a: 如果是"原创声明"弹窗 ──
            if "原创" in dialog_text and ("声明类型" in dialog_text or "我已阅读" in dialog_text):
                logger.info("  [Step2a] 检测到原创声明弹窗 → 自动处理")
                
                # 选择"无需声明"
                await page.evaluate('''() => {
                    const dialogs = document.querySelectorAll('.weui-desktop-dialog');
                    for (const dlg of dialogs) {
                        if (dlg.getBoundingClientRect().width < 100) continue;
                        const t = dlg.innerText;
                        if (!t.includes('原创')) continue;
                        // 找"无需声明"单选
                        const labels = dlg.querySelectorAll('label, span, div');
                        for (const lbl of labels) {
                            if (lbl.innerText && lbl.innerText.trim() === '无需声明') {
                                lbl.click();
                                break;
                            }
                        }
                        // 勾选"我已阅读并同意"
                        const allEls = dlg.querySelectorAll('label, span, div, input');
                        for (const el of allEls) {
                            if (el.innerText && el.innerText.includes('我已阅读')) {
                                el.click(); break;
                            }
                        }
                        const cbs = dlg.querySelectorAll('input[type="checkbox"]');
                        for (const cb of cbs) { if (!cb.checked) cb.click(); }
                    }
                }''')
                await asyncio.sleep(1)

                state2a = await _dump_page_state("step2a_after_original_fill")

                # 点击"确定"
                try:
                    confirm = page.locator(".weui-desktop-dialog:visible").locator("button:has-text('确定')").first
                    await confirm.click()
                    await asyncio.sleep(3)
                    logger.info("  [Step2a] ✅ 原创声明弹窗已确认")
                except Exception as e:
                    logger.warning(f"  [Step2a] 点击确定失败: {e}")

                # 确认后应该弹出第二个弹窗（定时发表弹窗）
                state2b = await _dump_page_state("step2b_after_original_confirm")
                if not state2b['dialogs']:
                    err_msg = f"原创声明确认后没有新弹窗。截图: {state2b['screenshot']}"
                    logger.error(f"  ❌ {err_msg}")
                    return {"success": False, "message": err_msg,
                            "debug": {"screenshot": state2b['screenshot']}}
                dialog_text = state2b['dialogs'][0]['text']

            # ── Step 3: 此时应该是"发表"弹窗（含群发通知/定时发表） ──
            if "群发通知" not in dialog_text and "定时发表" not in dialog_text and "发表" not in dialog_text:
                # 不是预期的发表弹窗
                err_msg = f"当前弹窗非发表弹窗，内容: {dialog_text[:200]}"
                logger.error(f"  ❌ {err_msg}")
                state3 = await _dump_page_state("step3_unexpected_dialog")
                html_path = os.path.join(debug_dir, "sched_step3_unexpected.html")
                html = await page.content()
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html)
                return {"success": False, "message": err_msg,
                        "debug": {"screenshot": state3['screenshot'], "html": html_path,
                                  "dialog_text": dialog_text[:500]}}

            logger.info("  [Step3] ✅ 进入发表弹窗，开始设置定时发表...")

            # ── Step 4: 开启定时发表、关闭群发 ──
            await page.evaluate('''() => {
                function selectOption(dlg, keyword) {
                    // Strategy 1: radio buttons (New UI)
                    const radios = dlg.querySelectorAll('input[type="radio"]');
                    for (const r of radios) {
                        const p = r.closest('label, div, p, span');
                        if (p && p.innerText && p.innerText.includes(keyword)) {
                            if (!r.checked) r.click();
                            return true;
                        }
                    }
                
                    // Strategy 2: find label containing keyword, then find switch in same row
                    const allEls = dlg.querySelectorAll('label, span, div, p');
                    for (const el of allEls) {
                        const txt = (el.innerText || '').trim();
                        if (!txt.includes(keyword)) continue;
                        let row = el.closest('.weui-desktop-form__control-group, .publish_access_item, div[class*="form"], li, section');
                        if (!row) row = el.parentElement;
                        if (!row) continue;
                        const cb = row.querySelector('.weui-desktop-switch__input, input[type="checkbox"]');
                        if (cb && !cb.checked) {
                            cb.click();
                            return true;
                        }
                        const switchLabel = row.querySelector('label.weui-desktop-switch, .weui-desktop-switch');
                        if (switchLabel) {
                            const innerCb = switchLabel.querySelector('input');
                            if (innerCb && !innerCb.checked) {
                                switchLabel.click();
                                return true;
                            }
                        }
                    }
                    
                    // Strategy 3: Click the exact text label if it acts as a tab/button
                    const clickables = dlg.querySelectorAll('label, div, span, p, li, a');
                    for (const el of clickables) {
                        const txt = (el.innerText || '').trim();
                        if (txt === keyword) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 5 && r.width < 300 && r.height > 5) {
                                el.click();
                                return true;
                            }
                        }
                    }
                    
                    return false;
                }

                const dialogs = document.querySelectorAll('.weui-desktop-dialog');
                for (const dlg of dialogs) {
                    if (dlg.getBoundingClientRect().width < 100) continue;
                    const t = dlg.innerText;
                    if (!t.includes('发表') && !t.includes('发送')) continue;
                    
                    selectOption(dlg, '定时发表');
                    
                    const allEls = dlg.querySelectorAll('label, span, div, p');
                    for (const el of allEls) {
                        const txt = (el.innerText || '').trim();
                        if (txt.includes('群发通知')) {
                            let row = el.closest('.weui-desktop-form__control-group') || el.parentElement;
                            if (row) {
                                const cb = row.querySelector('.weui-desktop-switch__input, input[type="checkbox"]');
                                if (cb && cb.checked) {
                                    cb.click();
                                }
                            }
                        }
                    }
                    break;
                }
            }''')
            await asyncio.sleep(2)

            state4 = await _dump_page_state("step4_after_switches")
            logger.info(f"  [Step4] 开关设置后: checkboxes={[(c['parentText'][:20], c['checked']) for c in state4['checkboxes'] if c['visible']][:6]}")

            # ── Step 5: 设置时间 ──
            target_hour = publish_time.strftime('%H')
            target_minute = publish_time.strftime('%M')
            target_time = publish_time.strftime('%H:%M')
            logger.info(f"  [Step5] 设置定时时间: {target_time}")

            await page.evaluate('''() => {
                const inputs = document.querySelectorAll('input');
                for (const inp of inputs) {
                    if (inp.value && /^\\d{2}:\\d{2}$/.test(inp.value)) {
                        inp.click();
                        break;
                    }
                }
            }''')
            await asyncio.sleep(1)

            await page.evaluate('''([hour, minute]) => {
                const allItems = document.querySelectorAll('.weui-desktop-picker__list li');
                let hourClicked = false;
                for (const li of allItems) {
                    const text = li.innerText ? li.innerText.trim() : '';
                    if (!hourClicked && text === hour) {
                        li.click();
                        hourClicked = true;
                    } else if (hourClicked && text === minute) {
                        li.click();
                        break;
                    }
                }
            }''', [target_hour, target_minute])
            await asyncio.sleep(1)

            await page.evaluate('''() => {
                const title = document.querySelector('.weui-desktop-dialog__title');
                if (title) title.click();
                document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
            }''')
            await asyncio.sleep(1)

            state5 = await _dump_page_state("step5_after_time_set")

            # ═══════════════════════════════════════════════════════════
            # 全量 Session 录制系统：录制所有网络请求、Toast 事件、连续截图
            # ═══════════════════════════════════════════════════════════
            import time as _time
            import json as _json
            session_id = publish_time.strftime('%H%M%S')
            session_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "debug_logs", f"session_{session_id}"
            )
            os.makedirs(session_dir, exist_ok=True)
            logger.info(f"  [Session] 📹 全量录制已启动 → {session_dir}")

            session_timeline = {
                "session_id": session_id,
                "publish_time": publish_time.strftime('%Y-%m-%d %H:%M'),
                "started_at": _time.time(),
                "network_events": [],
                "toast_events": [],
                "screenshots": [],
                "submit_attempts": [],
            }

            def _save_session_log():
                try:
                    log_path = os.path.join(session_dir, "session_events.json")
                    with open(log_path, "w", encoding="utf-8") as f:
                        _json.dump(session_timeline, f, ensure_ascii=False, indent=2, default=str)
                except Exception as e:
                    logger.warning(f"  [Session] 保存 session log 失败: {e}")

            async def _take_session_screenshot(label: str) -> str:
                fname = f"{int(_time.time())}_{label}.png"
                fpath = os.path.join(session_dir, fname)
                try:
                    await page.screenshot(path=fpath)
                    session_timeline["screenshots"].append({"file": fname, "label": label, "time": _time.time()})
                except Exception as e:
                    logger.debug(f"  [Session] 截图失败 ({label}): {e}")
                return fpath

            # ── 提前注入网络监听器（增强版：捕获所有 cgi-bin POST + 完整响应） ──
            async def handle_response(response):
                try:
                    url = response.url
                    if 'cgi-bin/' in url and response.request.method == 'POST':
                        entry = {
                            "url": url,
                            "status": response.status,
                            "time": _time.time(),
                            "method": "POST",
                        }
                        try:
                            json_data = await response.json()
                            entry["response"] = json_data
                        except Exception:
                            try:
                                text_data = await response.text()
                                entry["response_text"] = text_data[:2000]
                            except Exception:
                                entry["response_text"] = "(unreadable)"
                        
                        session_timeline["network_events"].append(entry)
                        
                        short_url = url.split('cgi-bin/')[-1].split('?')[0] if 'cgi-bin/' in url else url[-60:]
                        resp_data = entry.get("response", {})
                        base_resp = resp_data.get("base_resp", {}) if isinstance(resp_data, dict) else {}
                        ret_code = base_resp.get("ret", "?")
                        err_msg = base_resp.get("err_msg", resp_data.get("err_msg", ""))
                        logger.info(f"  [NET] {short_url} -> status={response.status} ret={ret_code} err={str(err_msg)[:80]}")
                except Exception:
                    pass
            page.on("response", handle_response)

            # ── 提前注入增强版 Toast 监听器 ──
            await page.evaluate('''() => {
                window.__wechat_toasts = [];
                window.__wechat_dom_snapshots = [];
                
                const KEYWORDS = ['失败','成功','繁忙','频繁','错误','异常','重试','超时',
                                   '请稍后','操作太快','系统','busy','error','fail','success',
                                   '提交','发表成功','定时'];
                
                function matchesKeywords(text) {
                    if (!text || text.length > 500) return false;
                    const lower = text.toLowerCase();
                    return KEYWORDS.some(kw => lower.includes(kw));
                }
                
                function getElementPath(el) {
                    const parts = [];
                    let cur = el;
                    for (let i = 0; i < 4 && cur && cur !== document.body; i++) {
                        const tag = cur.tagName ? cur.tagName.toLowerCase() : '?';
                        const cls = (cur.className || '').toString().substring(0, 60);
                        parts.unshift(tag + (cls ? '.' + cls.split(' ').slice(0,2).join('.') : ''));
                        cur = cur.parentElement;
                    }
                    return parts.join(' > ');
                }
                
                function recordToast(source, el, text) {
                    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {};
                    const s = el.nodeType === 1 ? window.getComputedStyle(el) : {};
                    window.__wechat_toasts.push({
                        time: Date.now(),
                        source: source,
                        text: text.substring(0, 500),
                        path: getElementPath(el),
                        rect: {x: Math.round(r.x||0), y: Math.round(r.y||0), w: Math.round(r.width||0), h: Math.round(r.height||0)},
                        display: s.display || '',
                        visibility: s.visibility || '',
                        opacity: s.opacity || '',
                    });
                }
                
                if (window.__wechat_toast_observer) {
                    window.__wechat_toast_observer.disconnect();
                }
                
                const observer = new MutationObserver((mutations) => {
                    for (const m of mutations) {
                        if (m.addedNodes) {
                            for (const node of m.addedNodes) {
                                if (node.nodeType !== 1) continue;
                                const text = (node.innerText || node.textContent || '').trim();
                                if (matchesKeywords(text)) {
                                    recordToast('addedNode', node, text);
                                }
                                if (node.querySelectorAll) {
                                    for (const child of node.querySelectorAll('*')) {
                                        const ct = (child.innerText || child.textContent || '').trim();
                                        if (ct && ct.length < 200 && matchesKeywords(ct)) {
                                            recordToast('addedChild', child, ct);
                                        }
                                    }
                                }
                            }
                        }
                        
                        if (m.type === 'attributes') {
                            const el = m.target;
                            if (!el || el.nodeType !== 1) continue;
                            const text = (el.innerText || el.textContent || '').trim();
                            if (!text) continue;
                            const s = window.getComputedStyle(el);
                            const isVisible = s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                            if (isVisible && matchesKeywords(text)) {
                                recordToast('attrChange:' + m.attributeName, el, text);
                            }
                        }
                        
                        if (m.type === 'characterData' && m.target.parentElement) {
                            const el = m.target.parentElement;
                            const text = (el.innerText || el.textContent || '').trim();
                            if (matchesKeywords(text)) {
                                recordToast('characterData', el, text);
                            }
                        }
                    }
                });
                observer.observe(document.body, {
                    childList: true,
                    subtree: true,
                    characterData: true,
                    attributes: true,
                    attributeFilter: ['style', 'class', 'hidden']
                });
                window.__wechat_toast_observer = observer;
                
                if (window.__wechat_toast_scanner) clearInterval(window.__wechat_toast_scanner);
                window.__wechat_toast_scanner = setInterval(() => {
                    const selectors = [
                        '.weui-desktop-toast', '.tips_global', '.global_tips',
                        '[class*="toast"]', '[class*="Toast"]', '[class*="tips"]',
                        '[class*="Tips"]', '[class*="notice"]', '[class*="Notice"]',
                        '[class*="alert"]', '[class*="Alert"]', '[class*="msg_"]',
                        '.weui-desktop-msg', '.weui-desktop-notify',
                    ];
                    for (const sel of selectors) {
                        try {
                            for (const el of document.querySelectorAll(sel)) {
                                const r = el.getBoundingClientRect();
                                if (r.width < 10 || r.height < 5) continue;
                                const s = window.getComputedStyle(el);
                                if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') continue;
                                const text = (el.innerText || el.textContent || '').trim();
                                if (!text) continue;
                                if (matchesKeywords(text)) {
                                    const now = Date.now();
                                    const isDup = window.__wechat_toasts.some(t => 
                                        t.text === text.substring(0, 500) && (now - t.time) < 2000
                                    );
                                    if (!isDup) {
                                        recordToast('activeScan:' + sel, el, text);
                                    }
                                }
                            }
                        } catch(e) {}
                    }
                    
                    const dialogs = document.querySelectorAll('.weui-desktop-dialog');
                    for (const dlg of dialogs) {
                        if (dlg.getBoundingClientRect().width > 100) {
                            const snap = {
                                time: Date.now(),
                                text: (dlg.innerText || '').substring(0, 800),
                                cls: (dlg.className || '').substring(0, 100)
                            };
                            const last = window.__wechat_dom_snapshots[window.__wechat_dom_snapshots.length - 1];
                            if (!last || last.text !== snap.text) {
                                window.__wechat_dom_snapshots.push(snap);
                            }
                        }
                    }
                }, 500);
            }''')

            # ── Step 6 ~ 8: 点击发表并验证（处理系统繁忙） ──
            for submit_attempt in range(10):
                attempt_start = _time.time()
                attempt_info = {
                    "attempt": submit_attempt + 1,
                    "start_time": attempt_start,
                    "detected_result": None,
                    "network_signals": [],
                    "toast_signals": [],
                    "active_scan_signals": [],
                }
                
                logger.info(f"  [Step6] 点击弹窗内发表按钮... (提交尝试 {submit_attempt+1})")
                
                net_baseline = len(session_timeline["network_events"])
                toast_baseline_js = await page.evaluate('() => window.__wechat_toasts ? window.__wechat_toasts.length : 0')
                
                await _take_session_screenshot(f"attempt{submit_attempt+1}_before_click")
                
                try:
                    clicked = await page.evaluate('''() => {
                        const dialogs = document.querySelectorAll('.weui-desktop-dialog');
                        for (const dlg of dialogs) {
                            if (dlg.getBoundingClientRect().width < 100) continue;
                            const btns = dlg.querySelectorAll('button');
                            for (const btn of btns) {
                                if (btn.innerText && btn.innerText.trim() === '发表' && !btn.disabled) {
                                    btn.click();
                                    return true;
                                }
                            }
                        }
                        return false;
                    }''')
                    if not clicked:
                        logger.debug("没点到发表按钮，可能弹窗已经消失或正在加载")
                        attempt_info["click_result"] = "not_found"
                    else:
                        attempt_info["click_result"] = "clicked"
                except Exception as e:
                    logger.error(f"  [Step6] 点击发表失败: {e}")
                    attempt_info["click_result"] = f"error: {e}"
                
                detected_result = None
                for poll_idx in range(15):
                    await asyncio.sleep(1)
                    
                    await self._handle_confirm_dialog(page)
                    
                    # ── 检查新的网络事件 ──
                    new_net_events = session_timeline["network_events"][net_baseline:]
                    
                    has_success = False
                    
                    for evt in new_net_events:
                        url = evt.get("url", "")
                        resp = evt.get("response", {})
                        status = evt.get("status", 0)
                        
                        # Check non-JSON text response
                        if not isinstance(resp, dict):
                            resp_text = str(evt.get("response_text", ""))
                            if any(kw in resp_text for kw in ["繁忙", "频繁"]) or "busy" in resp_text.lower():
                                detected_result = "busy"
                                attempt_info["network_signals"].append({"url": url, "signal": "busy_text", "text": resp_text[:200]})
                                break
                            continue
                        
                        base_resp = resp.get("base_resp", {})
                        err_msg = str(resp.get("err_msg", "")).lower()
                        if isinstance(base_resp, dict):
                            err_msg += " " + str(base_resp.get("err_msg", "")).lower()
                            ret_code = base_resp.get("ret", None)
                        else:
                            ret_code = None
                        
                        if status and status != 200:
                            attempt_info["network_signals"].append({"url": url, "signal": f"http_{status}", "err_msg": err_msg[:200]})
                        
                        # Explicit keyword failure
                        if any(kw in err_msg for kw in ["freq", "busy", "繁忙", "频繁", "too many", "rate limit", "系统错误"]):
                            detected_result = "busy"
                            attempt_info["network_signals"].append({"url": url, "signal": "busy", "err_msg": err_msg[:200], "ret": ret_code})
                            break
                        
                        # WeChat specific error codes during publish
                        # 154011 = frequent/busy, 154009 = ?, -1 = general temp error
                        if ret_code is not None and ret_code != 0:
                            if ret_code in [-1, 154011, 154009, 154008]:
                                detected_result = "busy"
                                attempt_info["network_signals"].append({"url": url, "signal": f"ret_{ret_code}", "err_msg": err_msg[:200]})
                                break
                            else:
                                attempt_info["network_signals"].append({"url": url, "signal": f"ret_{ret_code}", "err_msg": err_msg[:200]})
                                # For other non-zero codes, we might also consider them failures if they happen on publish endpoints
                                if any(kw in url for kw in ["appmsg", "masssend", "freepublish", "operate", "timer"]):
                                    detected_result = "busy"
                                    break
                        
                        # Record success but DO NOT BREAK immediately! We must ensure no other failures exist in this batch
                        if ret_code == 0 and any(kw in url for kw in ["appmsg", "masssend", "freepublish", "operate", "timer"]):
                            has_success = True
                            attempt_info["network_signals"].append({"url": url, "signal": "success", "ret": 0})
                    
                    if detected_result:
                        break
                    
                    # Only declare success if we have a valid success signal AND no failures were detected above
                    if has_success and not detected_result:
                        detected_result = "success"
                        break
                    
                    # ── 检查 JS 端的 Toast 事件 ──
                    toasts = await page.evaluate('() => window.__wechat_toasts ? window.__wechat_toasts : []')
                    new_toasts = toasts[toast_baseline_js:]
                    
                    for t in new_toasts:
                        txt = t.get('text', '')
                        source = t.get('source', '')
                        attempt_info["toast_signals"].append({"text": txt[:200], "source": source, "time": t.get('time')})
                        
                        if any(kw in txt for kw in ['繁忙', '频繁', '操作太快', '请稍后', '超时']):
                            detected_result = "busy"
                            logger.warning(f"  [Toast] 捕获到繁忙信号: [{source}] {txt[:100]}")
                            break
                        if '成功' in txt and '失败' not in txt:
                            detected_result = "success"
                            logger.info(f"  [Toast] 捕获到成功信号: [{source}] {txt[:100]}")
                            break
                    
                    if detected_result:
                        break
                    
                    # ── JS 主动扫描页面上所有可能的提示元素 ──
                    active_scan = await page.evaluate('''() => {
                        const results = [];
                        const selectors = [
                            '.weui-desktop-toast', '.tips_global', '.global_tips',
                            '[class*="toast"]', '[class*="Toast"]', '[class*="tips"]',
                            '[class*="notice"]', '[class*="alert"]', '[class*="msg_"]',
                            '.weui-desktop-msg', '.weui-desktop-notify',
                            '.weui-desktop-dialog__tips', '.weui-desktop-popover',
                        ];
                        for (const sel of selectors) {
                            try {
                                for (const el of document.querySelectorAll(sel)) {
                                    const r = el.getBoundingClientRect();
                                    if (r.width < 10 || r.height < 5) continue;
                                    const s = window.getComputedStyle(el);
                                    if (s.display === 'none' || s.visibility === 'hidden') continue;
                                    const text = (el.innerText || el.textContent || '').trim();
                                    if (text) {
                                        results.push({
                                            sel: sel,
                                            text: text.substring(0, 300),
                                            opacity: s.opacity,
                                            display: s.display,
                                        });
                                    }
                                }
                            } catch(e) {}
                        }
                        return results;
                    }''')
                    
                    if active_scan:
                        for item in active_scan:
                            txt = item.get('text', '')
                            attempt_info["active_scan_signals"].append(item)
                            if any(kw in txt for kw in ['繁忙', '频繁', '操作太快', '请稍后']):
                                detected_result = "busy"
                                logger.warning(f"  [ActiveScan] 捕获到繁忙: {txt[:100]}")
                                break
                            if '成功' in txt and '失败' not in txt and '定时' in txt:
                                detected_result = "success"
                                logger.info(f"  [ActiveScan] 捕获到成功: {txt[:100]}")
                                break
                    
                    if detected_result:
                        break
                    
                    if poll_idx % 3 == 2:
                        await _take_session_screenshot(f"attempt{submit_attempt+1}_poll{poll_idx}")
                
                # ── 记录本次尝试结果 ──
                attempt_info["detected_result"] = detected_result
                attempt_info["duration"] = _time.time() - attempt_start
                session_timeline["submit_attempts"].append(attempt_info)
                
                await _take_session_screenshot(f"attempt{submit_attempt+1}_result_{detected_result or 'unknown'}")
                
                try:
                    dom_snapshots = await page.evaluate('() => window.__wechat_dom_snapshots || []')
                    if dom_snapshots:
                        session_timeline.setdefault("dom_snapshots", []).extend(dom_snapshots)
                except Exception:
                    pass
                
                _save_session_log()
                
                if detected_result == "busy":
                    logger.info(f"  本次信号: net={len(attempt_info['network_signals'])} toast={len(attempt_info['toast_signals'])} scan={len(attempt_info['active_scan_signals'])}")
                    
                    # 检查是否其实已经发表成功（页面已经跳转）！
                    current_url = page.url
                    if "appmsg_edit" not in current_url:
                        logger.warning(f"  ⭐ [修正] 虽然捕获了繁忙信号，但页面已跳转成功 (URL: {current_url[-40:]})，覆盖为成功！")
                        detected_result = "success"
                    else:
                        has_dialog = await page.evaluate('''() => {
                            for (const el of document.querySelectorAll('.weui-desktop-dialog')) {
                                if (el.getBoundingClientRect().width > 100) return true;
                            }
                            return false;
                        }''')
                        if not has_dialog:
                            logger.info("  [检查] 捕获报错且弹窗消失，等待 10 秒确认是否为延迟跳转的真成功...")
                            for _ in range(5):
                                await asyncio.sleep(2)
                                if "appmsg_edit" not in page.url:
                                    logger.warning(f"  ⭐ [修正] 页面发生延迟跳转 (URL: {page.url[-40:]})，确认为真实发表成功！")
                                    detected_result = "success"
                                    break
                            
                            if detected_result != "success":
                                logger.warning("  ⚠️ 系统繁忙导致发表弹窗被强制关闭，需从头拉起发表流程...")
                                return {"success": False, "retry_full_flow": True, "message": "busy_dialog_closed"}
                        logger.warning("  ⚠️ 监听到系统繁忙(三重检测确认)，等待 30 秒后重试提交...")
                        await _take_session_screenshot(f"attempt{submit_attempt+1}_busy_waiting")
                        await asyncio.sleep(30)
                        continue
                
                if detected_result == "success":
                    logger.info("  ✅ 监听到发表成功信号（三重检测确认）！")
                    state8 = await _dump_page_state("step8_final_success")
                    try:
                        with open(os.path.join(session_dir, "success_page.html"), "w", encoding="utf-8") as f:
                            f.write(await page.content())
                    except Exception:
                        pass
                    _save_session_log()
                    return {"success": True, "message": "已成功提交定时发表任务(三重检测确认)",
                            "screenshot_path": state8.get('screenshot', ''),
                            "session_dir": session_dir}
                
                # ── 没有捕获到明确信号，走增强版兜底逻辑 ──
                state8 = await _dump_page_state("step8_final_fallback")
                current_url = page.url
                
                try:
                    with open(os.path.join(session_dir, f"attempt{submit_attempt+1}_fallback.html"), "w", encoding="utf-8") as f:
                        f.write(await page.content())
                except Exception:
                    pass
                
                if "appmsg_edit" not in current_url:
                    has_failure_signal = False
                    for evt in session_timeline["network_events"][net_baseline:]:
                        resp = evt.get("response", {})
                        if isinstance(resp, dict):
                            base_resp = resp.get("base_resp", {})
                            if isinstance(base_resp, dict) and base_resp.get("ret", 0) != 0:
                                has_failure_signal = True
                                logger.warning(f"  [兜底检查] 发现失败网络信号: ret={base_resp.get('ret')} url={evt.get('url','')[-40:]}")
                                break
                            resp_text = str(resp.get("err_msg", ""))
                            if any(kw in resp_text for kw in ["繁忙", "频繁", "busy", "freq"]):
                                has_failure_signal = True
                                logger.warning(f"  [兜底检查] 发现繁忙网络信号: {resp_text[:80]}")
                                break
                    
                    if has_failure_signal:
                        logger.warning("  ⚠️ 兜底检查确认页面虽不可识别，但仍有失败信号，等待 30 秒后重试...")
                        await asyncio.sleep(30)
                        continue
                    
                    logger.info("✅ 定时发表成功 (兜底判断：页面跳转 + 无失败/繁忙信号)！")
                    _save_session_log()
                    return {"success": True, "message": "已成功提交定时发表任务",
                            "screenshot_path": state8['screenshot'],
                            "session_dir": session_dir}
                
                # If we are here, we are still on the editor page, and it didn't succeed.
                if not state8['dialogs']:
                    err_msg = "发表弹窗已消失，但页面未能跳转完毕。疑似发表步骤异常中断。"
                    logger.error(f"  ❌ {err_msg}")
                    _save_session_log()
                    return {"success": False, "retry_full_flow": True, "message": err_msg, "screenshot_path": state8['screenshot'], "session_dir": session_dir}
                
                if submit_attempt == 9:
                    remaining_text = state8['dialogs'][0]['text'] if state8['dialogs'] else ''
                    err_msg = f"定时发表后仍有弹窗: {remaining_text[:200]}"
                    logger.warning(f"  ⚠️ {err_msg}")
                    _save_session_log()
                    return {"success": False, "message": err_msg,
                            "debug": {"screenshot": state8['screenshot'],
                                      "dialog_text": remaining_text[:500],
                                      "session_dir": session_dir}}
                else:
                    logger.debug("  [Step8] 页面仍有弹窗，等 3 秒后重试点击发表...")
                    await asyncio.sleep(3)
            
            # 循环结束后的清理
            try:
                await page.evaluate('() => { if (window.__wechat_toast_scanner) clearInterval(window.__wechat_toast_scanner); }')
            except Exception:
                pass

        except Exception as e:
            logger.error(f"❌ Schedule Publish error: {str(e)}")
            try:
                err_state = await _dump_page_state("error_final")
            except Exception:
                err_state = {}
            return {"success": False, "message": str(e),
                    "debug": err_state}


    async def _do_publish(self, page) -> dict:
        """Click the publish/mass-send button. USE WITH CAUTION."""
        logger.warning("  🚀 Publishing article (high risk operation)...")
        try:
            publish_btn = await page.query_selector(sel.PUBLISH_BUTTON_SELECTOR)
            if publish_btn:
                await publish_btn.click()
                await self._random_delay(2, 4)

                # Handle confirmation dialog
                try:
                    confirm_btn = await page.wait_for_selector(
                        sel.CONFIRM_DIALOG_OK_SELECTOR, timeout=10000
                    )
                    if confirm_btn:
                        await confirm_btn.click()
                        await self._random_delay(3, 5)
                except Exception:
                    pass

                return {
                    "success": True,
                    "message": "文章已发布（请在公众号后台确认状态）",
                    "mode": "publish",
                }
            else:
                return {
                    "success": False,
                    "message": "未找到发布按钮",
                    "mode": "publish",
                }
        except Exception as e:
            return {
                "success": False,
                "message": f"发布失败: {str(e)}",
                "mode": "publish",
            }

    async def _take_result_screenshot(self, page, title: str) -> Optional[str]:
        """Take a screenshot after the operation for debugging."""
        try:
            screenshot_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "wechat_auth",
            )
            os.makedirs(screenshot_dir, exist_ok=True)
            # Sanitize title for filename
            safe_title = "".join(c for c in title[:20] if c.isalnum() or c in " _-")
            screenshot_path = os.path.join(
                screenshot_dir, f"result_{safe_title}.png"
            )
            await page.screenshot(path=screenshot_path, full_page=False)
            logger.info(f"  📸 Screenshot saved: {screenshot_path}")
            return screenshot_path
        except Exception as e:
            logger.debug(f"Failed to take screenshot: {e}")
            return None

    async def _handle_confirm_dialog(self, page):
        """Handle secondary confirmation dialogs that may appear after publish click."""
        try:
            # Wait briefly for a second confirmation dialog
            await asyncio.sleep(1)
            clicked = await page.evaluate('''() => {
                const dialogs = document.querySelectorAll('.weui-desktop-dialog');
                for (const dlg of dialogs) {
                    const r = dlg.getBoundingClientRect();
                    if (r.width < 100) continue;
                    const s = window.getComputedStyle(dlg);
                    if (s.display === 'none') continue;
                    const btns = dlg.querySelectorAll('button');
                    for (const btn of btns) {
                        const text = (btn.innerText || '').trim();
                        if ((text === '确定' || text === '确认' || text === '确认发表' || text === '继续发表') && !btn.disabled) {
                            btn.click();
                            return text;
                        }
                    }
                }
                return null;
            }''')
            if clicked:
                logger.info(f"  [Step7] ✅ 二次确认弹窗已点击: {clicked}")
            else:
                logger.info("  [Step7] 无二次确认弹窗")
        except Exception as e:
            logger.debug(f"  [Step7] 处理确认弹窗异常: {e}")

    async def _random_delay(
        self, min_s: float = None, max_s: float = None
    ):
        """Add random delay to simulate human behavior."""
        import random

        min_delay = min_s if min_s is not None else WECHAT_MIN_DELAY
        max_delay = max_s if max_s is not None else WECHAT_MAX_DELAY
        await asyncio.sleep(random.uniform(min_delay, max_delay))

