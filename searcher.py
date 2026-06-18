"""
小红书搜索模块 - Playwright 浏览器自动化

职责：
  1. 用 headless 浏览器模拟真实搜索，从搜索结果页提取帖子列表
  2. 在同一浏览器会话中逐个打开详情页，提取完整 HTML（含 SSR 数据）
  3. 将 HTML 传给 content_parser 做正则解析（标题/正文/图片/视频）

关键依赖：
  - 需要预先保存登录 Cookie（运行 save_cookies.py 获取）
  - pip install playwright（复用系统 Chrome，无需下载 Chromium）
"""

import asyncio
import json
import sys
from urllib.parse import quote, parse_qs, urlparse

from config import (
    BROWSER_CHANNEL,
    BROWSER_EXECUTABLE_PATH,
    COOKIE_PATH,
    HEADERS,
    MAX_RESULTS_DEFAULT,
    PAGE_LOAD_WAIT_MS,
    SCROLL_TIMES,
    SCROLL_WAIT_MS,
    SELECTORS,
    XHS_BASE_URL,
    XHS_SEARCH_URL,
)

# Windows asyncio 兼容
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def _parse_likes(text: str) -> int:
    """解析点赞数文本，如 '1.2万' -> 12000。"""
    text = text.strip()
    if "万" in text:
        return int(float(text.replace("万", "")) * 10000)
    if "千" in text:
        return int(float(text.replace("千", "")) * 1000)
    try:
        return int(text)
    except ValueError:
        return 0


async def _launch_browser():
    """启动浏览器并加载 Cookie，返回 (browser, context)。"""
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    launch_kwargs = {"headless": True}
    if BROWSER_EXECUTABLE_PATH:
        launch_kwargs["executable_path"] = BROWSER_EXECUTABLE_PATH
    elif BROWSER_CHANNEL:
        launch_kwargs["channel"] = BROWSER_CHANNEL
    browser = await pw.chromium.launch(**launch_kwargs)
    context = await browser.new_context(
        user_agent=HEADERS["User-Agent"],
        viewport={"width": 1280, "height": 800},
        locale="zh-CN",
    )

    if COOKIE_PATH.exists():
        cookies = json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
        await context.add_cookies(cookies)
    else:
        print(f"[警告] 未找到 Cookie 文件: {COOKIE_PATH}")

    return pw, browser, context


async def _search_page(context, keyword: str, max_results: int) -> list[dict]:
    """在搜索结果页提取帖子列表。"""
    results: list[dict] = []
    search_url = XHS_SEARCH_URL.format(keyword=quote(keyword))
    page = await context.new_page()

    await page.goto(search_url, wait_until="networkidle")
    await page.wait_for_timeout(PAGE_LOAD_WAIT_MS)

    # 滚动加载更多瀑布流
    for _ in range(SCROLL_TIMES):
        await page.evaluate("window.scrollBy(0, 800)")
        await page.wait_for_timeout(SCROLL_WAIT_MS)

    cards = await page.query_selector_all(SELECTORS["note_card"])
    for card in cards:
        try:
            link = await card.query_selector(SELECTORS["link"])
            href = await link.get_attribute("href") if link else ""
            note_id = ""
            xsec_token = ""
            if href:
                parsed = urlparse(href)
                note_id = parsed.path.rstrip("/").split("/")[-1]
                qs = parse_qs(parsed.query)
                xsec_token = qs.get("xsec_token", [""])[0]
            if not note_id:
                continue

            url = f"{XHS_BASE_URL}/explore/{note_id}"
            if xsec_token:
                url += f"?xsec_token={xsec_token}&xsec_source="

            title_el = await card.query_selector(SELECTORS["title"])
            title = (await title_el.inner_text()).strip() if title_el else ""

            author_el = await card.query_selector(SELECTORS["author"])
            author = (await author_el.inner_text()).strip() if author_el else ""

            likes_el = await card.query_selector(SELECTORS["likes"])
            likes_text = (await likes_el.inner_text()).strip() if likes_el else "0"

            results.append({
                "note_id": note_id,
                "xsec_token": xsec_token,
                "title": title,
                "author": author,
                "likes": _parse_likes(likes_text),
                "url": url,
            })

            if len(results) >= max_results:
                break
        except Exception:
            continue

    await page.close()
    return results


async def _fetch_detail(context, url: str, note_id: str) -> dict:
    """用同一浏览器 context 打开详情页，从 SSR 数据中提取结构化内容。

    直接解析 window.__INITIAL_STATE__ 对象，精确定位 noteDetailMap 中的
    note 节点，避免全局正则匹配到页面底部备案信息。
    """
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(2000)

        # 直接在浏览器中解析 SSR 数据，提取结构化字段
        detail = await page.evaluate("""(noteId) => {
            const state = window.__INITIAL_STATE__;
            if (!state || !state.note || !state.note.noteDetailMap) return null;
            const entry = state.note.noteDetailMap[noteId];
            if (!entry || !entry.note) return null;
            const note = entry.note;
            return {
                title: note.title || '',
                desc: note.desc || '',
                type: note.type || 'image',
                user: note.user ? {
                    nickname: note.user.nickname || '',
                    userId: note.user.userId || '',
                } : null,
                imageList: (note.imageList || []).map(img => img.urlDefault || img.url || ''),
                video: note.video ? {
                    media: note.video.media ? {
                        stream: note.video.media.stream || {},
                    } : null,
                } : null,
                interactInfo: note.interactInfo ? {
                    likedCount: note.interactInfo.likedCount || '0',
                } : null,
            };
        }""", note_id)

        return detail or {}
    finally:
        await page.close()


async def _search_and_parse_async(
    keywords: list[str], max_results: int
) -> dict[str, list[dict]]:
    """
    搜索 + 详情解析一体化（单个浏览器实例，避免反复启动）。

    流程: 搜索关键词A → 逐个打开详情页提取HTML → 搜索关键词B → ...
    返回: {keyword: [{note_id, title, author, ..., html}, ...]}
    """
    pw, browser, context = await _launch_browser()
    all_results: dict[str, list[dict]] = {}

    try:
        for kw in keywords:
            print(f"  [搜索] {kw}")
            notes = await _search_page(context, kw, max_results)
            print(f"         命中 {len(notes)} 条")

            # 逐个解析详情（从 SSR 数据中直接提取结构化内容）
            for note in notes:
                try:
                    print(f"  [解析] {note['note_id']}  {note['title'][:30]}")
                    detail = await _fetch_detail(context, note["url"], note["note_id"])
                    note["detail"] = detail
                except Exception as e:
                    print(f"  [详情失败] {note['note_id']}: {e}")
                    note["detail"] = {}

            all_results[kw] = notes
    finally:
        await browser.close()
        await pw.stop()

    return all_results


def search_and_parse(
    keywords: list[str], max_results: int = MAX_RESULTS_DEFAULT
) -> dict[str, list[dict]]:
    """
    同步接口：搜索 + 解析详情（单浏览器实例）。

    Returns:
        {keyword: [{note_id, title, author, likes, url, xsec_token, html}, ...]}
    """
    return asyncio.run(_search_and_parse_async(keywords, max_results))
