"""
Cookie 获取辅助脚本

打开有头浏览器，手动登录小红书，登录成功后自动保存 Cookie。
后续 search 模块会加载此 Cookie 进行搜索。

用法: python save_cookies.py
"""

import asyncio
import json
import sys
from pathlib import Path

from config import BROWSER_CHANNEL, BROWSER_EXECUTABLE_PATH, COOKIE_PATH, XHS_BASE_URL

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        launch_kwargs = {"headless": False}
        if BROWSER_EXECUTABLE_PATH:
            launch_kwargs["executable_path"] = BROWSER_EXECUTABLE_PATH
        elif BROWSER_CHANNEL:
            launch_kwargs["channel"] = BROWSER_CHANNEL
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = await context.new_page()

        print("[提示] 浏览器将打开小红书登录页")
        print("[提示] 请手动扫码或输入账号登录")
        print("[提示] 登录成功后，回到终端按回车保存 Cookie")

        await page.goto(f"{XHS_BASE_URL}/explore", wait_until="networkidle")

        # 等待用户登录
        input(">>> 登录成功后按回车继续...")

        # 保存 Cookie
        cookies = await context.cookies()
        COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
        COOKIE_PATH.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[完成] Cookie 已保存到: {COOKIE_PATH}")
        print(f"[完成] 共 {len(cookies)} 条 Cookie")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
