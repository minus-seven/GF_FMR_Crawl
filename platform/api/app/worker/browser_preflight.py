"""Native browser preflight for the formal data Worker.

The crawler adapters that use Playwright must run in a native user/service
process. This module performs a local browser launch check only; it does not
open a remote website or require database access.
"""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class BrowserPreflightResult:
    """成功的浏览器启动结果；参数 browser 表示实际使用的渠道。"""

    browser: str
    version: str


def _channels() -> list[str]:
    """读取浏览器尝试顺序；参数为空，默认 Chrome、Edge、自带 Chromium。"""

    configured = os.environ.get("PLATFORM_BROWSER_CHANNELS", "")
    values = [item.strip().lower() for item in configured.split(",") if item.strip()]
    return values or ["chrome", "msedge", "bundled"]


def check_browser() -> BrowserPreflightResult:
    """启动并关闭一个本地浏览器页面；失败时抛出带逐渠道原因的 RuntimeError。"""

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - 取决于运行时依赖
        raise RuntimeError(f"Playwright 未安装：{type(exc).__name__}: {exc}") from exc

    failures: list[str] = []
    with sync_playwright() as playwright:
        for channel in _channels():
            browser = None
            try:
                launch_kwargs = {"headless": True}
                if channel != "bundled":
                    launch_kwargs["channel"] = channel
                browser = playwright.chromium.launch(**launch_kwargs)
                page = browser.new_page()
                page.close()
                return BrowserPreflightResult(
                    browser=channel,
                    version=str(browser.version),
                )
            except Exception as exc:  # noqa: BLE001 - 继续尝试下一渠道
                message = str(exc).replace("\n", " ")
                failures.append(f"{channel}: {type(exc).__name__}: {message[-500:]}")
            finally:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass

    raise RuntimeError(
        "原生浏览器预检失败；Worker 不会启动。\n" + "\n".join(failures)
    )

