"""
小红书帖子监控工程 - 全局配置

所有路径、常量、选择器集中管理，便于统一调整。
"""

import os
import sys
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "db" / "monitor.db"
COOKIE_PATH = PROJECT_ROOT / "cookies" / "xhs_cookies.json"
OUTPUT_DIR = PROJECT_ROOT / "output"

# NAS 存储路径（复用现有 skill 的 SMB 配置）
NAS_PATH = r"\\100.114.94.119\sese\精神食粮"

# ── 小红书配置 ────────────────────────────────────────────
XHS_BASE_URL = "https://www.xiaohongshu.com"
XHS_SEARCH_URL = XHS_BASE_URL + "/search_result?keyword={keyword}&source=web_search_result_notes"

# 请求头（复用 xiaohongshu-image-downloader skill 的配置）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.xiaohongshu.com/",
}

# ── 浏览器配置 ────────────────────────────────────────────
# 跨平台浏览器配置：
#   Windows → channel="chrome"（复用系统 Chrome）
#   Linux   → executable_path="/snap/bin/chromium"（Ubuntu snap chromium）
#             或 channel="chromium"（如果装了 Playwright chromium）
BROWSER_CHANNEL = None
BROWSER_EXECUTABLE_PATH = None
if sys.platform == "win32":
    BROWSER_CHANNEL = "chrome"
else:
    # Linux: 优先用 snap chromium，其次 google-chrome
    for _candidate in ("/snap/bin/chromium", "/usr/bin/google-chrome", "/usr/bin/chromium-browser"):
        if Path(_candidate).exists():
            BROWSER_EXECUTABLE_PATH = _candidate
            break
    # 如果系统没有 chromium，用 Playwright 自带的（需 playwright install chromium）
    if not BROWSER_EXECUTABLE_PATH:
        BROWSER_CHANNEL = "chromium"

# ── 搜索配置 ──────────────────────────────────────────────
SCROLL_TIMES = 3          # 搜索结果页滚动次数（每次加载更多瀑布流）
SCROLL_WAIT_MS = 2000     # 每次滚动后等待渲染的毫秒数
PAGE_LOAD_WAIT_MS = 3000  # 搜索页首次加载后等待时间
PAGE_GOTO_TIMEOUT_MS = 30000  # 页面 goto 超时（毫秒），networkidle 可能因图片/视频加载慢而超时
MAX_RESULTS_DEFAULT = 10  # 每个关键词默认最大抓取数

# ── 搜索结果页 DOM 选择器（如小红书改版需调整此处）─────────
SELECTORS = {
    "note_card": "section.note-item",
    "link": "a.cover.ld",
    "title": ".footer .title",
    "author": ".author .name",
    "likes": ".like-wrapper .count",
}

# ── 去重配置 ──────────────────────────────────────────────
DEDUP_CONTENT_HASH = True  # 是否启用内容指纹去重

# ── LLM 配置（OpenAI 兼容端点）────────────────────────────
# 默认 DeepSeek（国内访问稳定、价格低）；任何 OpenAI 兼容端点可改 base_url + model
# 优先级: XHS_LLM_API_KEY > OPENAI_API_KEY
LLM_BASE_URL = os.getenv("XHS_LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("XHS_LLM_MODEL", "deepseek-chat")
LLM_API_KEY = os.getenv("XHS_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
LLM_ENABLED = bool(LLM_API_KEY)  # 未配置 API key 时跳过 LLM 过滤（降级）
