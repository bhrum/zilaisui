#!/usr/bin/env python3
"""Capture and smoke-test Playwright storage_state for web-console logins.

Supported services: WeChat Official Account (mp.weixin.qq.com) and ChatGPT web
(chatgpt.com). This script does not bypass captcha, QR-code login, Cloudflare,
or any other access control. It only saves or reuses a normal user session after
manual login.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from playwright.async_api import Page, async_playwright

DEFAULTS = {
    "wechat": {
        "login_url": "https://mp.weixin.qq.com/",
        "test_url": "https://mp.weixin.qq.com/cgi-bin/home",
        "auth": "wechat_auth/state.json",
        "profile": "wechat_auth/profile",
        "locale": "zh-CN",
        "timezone": "Asia/Shanghai",
    },
    "chatgpt": {
        "login_url": "https://chatgpt.com/",
        "test_url": "https://chatgpt.com/",
        "auth": "auth_profiles/saved/chatgpt_state.json",
        "profile": "camoufox_profile/chatgpt_profile",
        "locale": "en-US",
        "timezone": "UTC",
    },
}

CHATGPT_INPUTS = ["textarea[data-testid='prompt-textarea']", "#prompt-textarea", "textarea", "[contenteditable='true']"]
CHATGPT_SEND = ["button[data-testid='send-button']", "button[aria-label*='Send']"]
CHATGPT_RESPONSES = ["[data-message-author-role='assistant']", "div.markdown", "article"]
WECHAT_ACCOUNT = [".weui-desktop-account__nickname", ".nickname", "#nickname"]


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def default_auth(service: str) -> str:
    return os.environ.get("WEB_AUTH_STATE_PATH") or DEFAULTS[service]["auth"]


def default_profile(service: str) -> str:
    return os.environ.get("WEB_AUTH_PROFILE_DIR") or DEFAULTS[service]["profile"]


def browser_options(args: argparse.Namespace, service: str) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "headless": args.headless,
        "locale": DEFAULTS[service]["locale"],
        "timezone_id": DEFAULTS[service]["timezone"],
        "viewport": {"width": 1440, "height": 900},
    }
    if args.channel and args.browser == "chromium":
        opts["channel"] = args.channel
    if args.executable_path:
        opts["executable_path"] = args.executable_path
    proxy = args.proxy or os.environ.get("UNIFIED_PROXY_CONFIG") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        opts["proxy"] = {"server": proxy}
    return opts


async def has_selector(page: Page, selectors: list[str], timeout: int = 800) -> bool:
    for selector in selectors:
        try:
            await page.locator(selector).last.wait_for(timeout=timeout)
            return True
        except Exception:
            pass
    return False


async def visible_text(page: Page, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            text = (await page.locator(selector).first.inner_text(timeout=1200)).strip()
            if text:
                return text
        except Exception:
            pass
    return None


async def is_logged_in(page: Page, service: str) -> bool:
    url = page.url or ""
    if service == "wechat":
        return ("mp.weixin.qq.com" in url and "cgi-bin" in url and "token=" in url and "login" not in url) or await has_selector(page, WECHAT_ACCOUNT)
    return "chatgpt.com" in url and "auth/login" not in url and await has_selector(page, CHATGPT_INPUTS, 1500)


async def write_diagnostics(page: Page, diag_dir: Path, name: str, result: dict[str, Any]) -> None:
    diag_dir.mkdir(parents=True, exist_ok=True)
    (diag_dir / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        await page.screenshot(path=str(diag_dir / f"{name}.png"), full_page=True)
    except Exception as exc:
        result["screenshot_error"] = str(exc)
        (diag_dir / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


async def save_auth(args: argparse.Namespace) -> int:
    service = args.service
    auth_path = Path(args.auth or default_auth(service)).expanduser()
    profile_dir = Path(args.profile or default_profile(service)).expanduser()
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    url = args.url or DEFAULTS[service]["login_url"]

    print(f"Opening {url}")
    print(f"Auth state will be saved to: {auth_path}")
    print("Complete the normal login flow in the opened browser. Do not share the saved auth JSON.")

    async with async_playwright() as p:
        btype = getattr(p, args.browser)
        context = await btype.launch_persistent_context(str(profile_dir), **browser_options(args, service))
        page = context.pages[0] if context.pages else await context.new_page()
        result = {"ok": False, "service": service, "auth_path": str(auth_path), "profile_dir": str(profile_dir), "started_at": int(time.time())}
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            end = time.time() + args.timeout
            while args.no_wait or time.time() < end:
                result["ok"] = await is_logged_in(page, service)
                if args.no_wait or result["ok"]:
                    break
                await asyncio.sleep(2)
            storage = await context.storage_state(path=str(auth_path))
            result.update({"url": page.url, "cookies": len(storage.get("cookies", [])), "origins": len(storage.get("origins", [])), "saved": True})
            account = await visible_text(page, WECHAT_ACCOUNT) if service == "wechat" else None
            if account:
                result["account_name"] = account
            await write_diagnostics(page, Path(args.diagnostic_dir), f"{service}_auth", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] and result["cookies"] else 2
        except Exception as exc:
            result.update({"ok": False, "error": str(exc), "url": page.url})
            await write_diagnostics(page, Path(args.diagnostic_dir), f"{service}_auth", result)
            print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        finally:
            await context.close()


async def fill_chatgpt(page: Page, prompt: str) -> bool:
    for selector in CHATGPT_INPUTS:
        try:
            loc = page.locator(selector).last
            await loc.wait_for(timeout=5000)
            await loc.click(timeout=3000)
            tag = await loc.evaluate("el => el.tagName.toLowerCase()")
            if tag == "textarea":
                await loc.fill(prompt)
            else:
                await loc.evaluate("(el, text) => { el.textContent = text; el.dispatchEvent(new InputEvent('input', {bubbles: true, data: text, inputType: 'insertText'})); }", prompt)
            return True
        except Exception:
            pass
    return False


async def click_send(page: Page) -> bool:
    for selector in CHATGPT_SEND:
        try:
            loc = page.locator(selector).last
            await loc.wait_for(timeout=2000)
            if await loc.is_enabled(timeout=1000):
                await loc.click(timeout=2000)
                return True
        except Exception:
            pass
    try:
        await page.keyboard.press("Enter")
        return True
    except Exception:
        return False


async def read_chatgpt_response(page: Page, before: int, timeout: int) -> str:
    end = time.time() + timeout
    last = ""
    stable = 0
    while time.time() < end:
        texts: list[str] = []
        for selector in CHATGPT_RESPONSES:
            try:
                loc = page.locator(selector)
                for i in range(before, await loc.count()):
                    text = (await loc.nth(i).inner_text(timeout=1000)).strip()
                    if text:
                        texts.append(text)
            except Exception:
                pass
        current = texts[-1] if texts else ""
        if current and current == last:
            stable += 1
            if stable >= 3:
                return current
        else:
            stable = 0
            last = current
        await asyncio.sleep(1)
    return last


async def test_auth(args: argparse.Namespace) -> int:
    service = args.service
    auth_path = Path(args.auth or default_auth(service)).expanduser()
    if not auth_path.exists():
        print(json.dumps({"ok": False, "error": f"auth file not found: {auth_path}"}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    url = args.url or DEFAULTS[service]["test_url"]
    async with async_playwright() as p:
        btype = getattr(p, args.browser)
        launch_opts = browser_options(args, service)
        launch_opts.pop("locale", None)
        launch_opts.pop("timezone_id", None)
        launch_opts.pop("viewport", None)
        browser = await btype.launch(**launch_opts)
        context = await browser.new_context(storage_state=str(auth_path), locale=DEFAULTS[service]["locale"], timezone_id=DEFAULTS[service]["timezone"], viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        result: dict[str, Any] = {"ok": False, "service": service, "auth_path": str(auth_path), "checked_at": int(time.time())}
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
            await page.wait_for_timeout(1500)
            cookies = len(await context.cookies())
            if service == "wechat":
                account = await visible_text(page, WECHAT_ACCOUNT)
                result.update({"ok": await is_logged_in(page, service), "url": page.url, "cookies": cookies})
                if account:
                    result["account_name"] = account
            else:
                composer = await has_selector(page, CHATGPT_INPUTS, 2500)
                result.update({"ok": composer and "auth/login" not in page.url, "url": page.url, "cookies": cookies, "composer_found": composer})
                if result["ok"] and not args.chatgpt_no_send:
                    before = 0
                    try:
                        before = await page.locator(CHATGPT_RESPONSES[0]).count()
                    except Exception:
                        pass
                    prompt = args.prompt or "只回复 pong"
                    if await fill_chatgpt(page, prompt) and await click_send(page):
                        response = await read_chatgpt_response(page, before, args.timeout)
                        result.update({"ok": bool(response), "response_preview": response[:1000]})
                    else:
                        result.update({"ok": False, "message": "could not submit ChatGPT prompt"})
            if not result["ok"] and "message" not in result:
                result["message"] = "saved auth did not reach a logged-in usable page"
            await write_diagnostics(page, Path(args.diagnostic_dir), f"{service}_smoke", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 2
        except Exception as exc:
            result.update({"ok": False, "error": str(exc), "url": page.url})
            await write_diagnostics(page, Path(args.diagnostic_dir), f"{service}_smoke", result)
            print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        finally:
            await context.close()
            await browser.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Save or smoke-test web browser auth state.", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ["save", "test"]:
        p = sub.add_parser(name)
        p.add_argument("--service", choices=sorted(DEFAULTS), required=True)
        p.add_argument("--auth")
        p.add_argument("--url")
        p.add_argument("--timeout", type=int, default=300 if name == "save" else 90)
        p.add_argument("--headless", action="store_true", default=env_bool("WEB_AUTH_HEADLESS", name == "test"))
        p.add_argument("--browser", choices=["chromium", "firefox", "webkit"], default=os.environ.get("WEB_AUTH_BROWSER", "chromium"))
        p.add_argument("--channel", default=os.environ.get("WEB_AUTH_CHANNEL"))
        p.add_argument("--executable-path", default=os.environ.get("WEB_AUTH_EXECUTABLE_PATH"))
        p.add_argument("--proxy")
        p.add_argument("--diagnostic-dir", default=os.environ.get("WEB_AUTH_DIAGNOSTIC_DIR", "auth_diagnostics"))
        if name == "save":
            p.add_argument("--profile")
            p.add_argument("--no-wait", action="store_true")
        else:
            p.add_argument("--prompt")
            p.add_argument("--chatgpt-no-send", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(save_auth(args) if args.cmd == "save" else test_auth(args))


if __name__ == "__main__":
    raise SystemExit(main())
