# -*- coding: utf-8 -*-
"""真实浏览器渠道的抓取/下载助手（应对 Akamai 对自动化指纹的拦截）。

背景（富达站点实测）：
  - Playwright 自带 Chromium（headless shell）→ 403 Access Denied；
  - 真实浏览器渠道（channel=chrome/msedge）+ Chrome UA → 200 正常；
  - 纯 requests（即使带浏览器 cookie）→ 403。

因此本模块统一提供：
  launch_browser()       优先使用本机 Chrome/Edge，兜底自带 Chromium；
  new_context()          统一 Chrome UA / locale；
  is_access_denied()     识别拦截页并快速失败，避免长时间空等；
  fetch_json()           在页面内 fetch（同源），拿接口 JSON；
  download_via_browser() 先切到目标域再页面内 fetch，把 PDF 字节落盘。
"""

from __future__ import annotations

import base64
import json
import os
from urllib.parse import urlparse


CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# 优先真实浏览器渠道；Edge 默认 UA 会被富达站点拦截，因此统一覆盖 Chrome UA
BROWSER_CHANNELS = ("chrome", "msedge")


def launch_browser(playwright, *, headless: bool = True, channels=BROWSER_CHANNELS):
    """启动浏览器：优先 Chrome/Edge，找不到时兜底 Playwright 自带 Chromium。"""
    last_error = None
    for channel in channels:
        try:
            return playwright.chromium.launch(channel=channel, headless=headless)
        except Exception as exc:  # noqa: BLE001 - 逐个渠道尝试
            last_error = exc
    if last_error is not None:
        print(f"[!] 未找到 Chrome/Edge（{last_error}），改用自带 Chromium（可能被站点拦截）")
    return playwright.chromium.launch(headless=headless)


def new_context(browser, *, locale: str = "zh-HK"):
    """创建统一 UA/locale 的浏览器上下文。"""
    return browser.new_context(
        user_agent=CHROME_UA,
        locale=locale,
        ignore_https_errors=True,
        accept_downloads=True,
    )


def is_access_denied(page=None, response=None) -> bool:
    """判断是否命中站点拦截页（403 / Access Denied）。"""
    if response is not None and getattr(response, "status", None) == 403:
        return True
    if page is None:
        return False
    try:
        title = (page.title() or "").strip()
    except Exception:  # noqa: BLE001
        return False
    if title.lower().startswith("access denied"):
        return True
    try:
        content = page.content()
    except Exception:  # noqa: BLE001
        return False
    return "Access Denied" in content and len(content) < 3000


def fetch_json(page, url: str, *, timeout_ms: int = 30000) -> dict:
    """在页面内 fetch 指定接口并解析 JSON（页面需与接口同源或接口允许跨域）。"""
    result = page.evaluate(
        """async ({url, timeout}) => {
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), timeout);
            try {
                const resp = await fetch(url, {headers: {Accept: 'application/json'}, signal: ctrl.signal});
                return {status: resp.status, text: await resp.text()};
            } catch (err) {
                return {status: -1, text: String(err)};
            } finally {
                clearTimeout(timer);
            }
        }""",
        {"url": url, "timeout": timeout_ms},
    )
    if int(result.get("status") or 0) != 200:
        raise RuntimeError(f"接口请求失败：HTTP {result.get('status')} ({url}) {result.get('text', '')[:120]}")
    return json.loads(result["text"])


def download_via_browser(page, url: str, dest: str, *, timeout_ms: int = 60000) -> int:
    """用浏览器把文件下载到 dest，返回字节数。

    实现方式：先切到目标域（同源），再在页面内 fetch 取字节（base64 传回），
    避免 requests / APIRequestContext 被 Akamai 403 拦截。
    """
    target = urlparse(url)
    origin = f"{target.scheme}://{target.netloc}"
    if urlparse(page.url).netloc != target.netloc:
        for warmup in (f"{origin}/robots.txt", f"{origin}/"):
            try:
                page.goto(warmup, wait_until="domcontentloaded", timeout=timeout_ms)
                break
            except Exception:  # noqa: BLE001 - 预热失败不阻断后续 fetch
                continue
    payload = page.evaluate(
        """async ({url, timeout}) => {
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), timeout);
            try {
                const resp = await fetch(url, {credentials: 'include', signal: ctrl.signal});
                if (!resp.ok) return {status: resp.status, b64: '', error: ''};
                const buf = new Uint8Array(await resp.arrayBuffer());
                let binary = '';
                const chunk = 0x8000;
                for (let i = 0; i < buf.length; i += chunk) {
                    binary += String.fromCharCode.apply(null, buf.subarray(i, i + chunk));
                }
                return {status: resp.status, b64: btoa(binary), len: buf.length};
            } catch (err) {
                return {status: -1, b64: '', error: String(err)};
            } finally {
                clearTimeout(timer);
            }
        }""",
        {"url": url, "timeout": timeout_ms},
    )
    status = int(payload.get("status") or 0)
    if status != 200:
        raise RuntimeError(
            f"浏览器下载失败：HTTP {status} {payload.get('error') or ''} ({url})"
        )
    data = base64.b64decode(payload.get("b64") or "")
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"下载内容不是 PDF：{url}")
    temporary = dest + ".part"
    with open(temporary, "wb") as handle:
        handle.write(data)
    os.replace(temporary, dest)
    return len(data)
