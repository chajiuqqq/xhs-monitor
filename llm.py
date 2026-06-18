"""
LLM 能力封装 - OpenAI 兼容端点

提供两个核心函数：
  - decompose_task(description)  : 自然语言需求 → {keywords, filter}
  - filter_posts(posts, criteria) : 批量帖子筛选，返回命中的帖子

默认端点: DeepSeek (https://api.deepseek.com/v1)
可改 base_url + model 切换到任何 OpenAI 兼容服务（OpenAI/月之暗面/智谱/Qwen 等）
"""

import json
import re
import sys
from typing import Any

from config import LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL


# ── Prompt 模板 ────────────────────────────────────────────

_DECOMPOSE_SYSTEM = """你是任务拆解助手。用户给出自然语言监控需求描述，返回严格 JSON：
{
  "name": "用于标识任务组的英文/拼音 slug（≤24 字符，snake_case，仅含字母/数字/下划线）",
  "keywords": [3-5 个适合在小红书搜索的关键词（中文为主，简洁 2-4 字/词）],
  "filter": "用于二次过滤搜索结果的自然语言条件；可为空字符串"
}

要求：
1. name 要简洁、概括需求主题（例: agent_jd_30w, sh_blind_date, sale_recruitment）
2. 关键词要互补，覆盖需求的不同侧面（例：主题词 + 同义词 + 细分场景）
3. 关键词适合小红书用户搜索习惯，避免过学术的术语
4. filter 要具体可执行，例: "年薪>30w 的 agent 开发工程师招聘信息；不含培训/课程/广告"
5. 若需求无隐含过滤条件，filter 返回空字符串
6. 只返回 JSON 对象，不要其他内容、不要 markdown 代码块包装"""

_FILTER_SYSTEM = """你是小红书帖子筛选助手。给定一个用户筛选条件和一组帖子，判断每个帖子是否符合。
返回严格 JSON 数组（不要 markdown 代码块包装），每个元素对应一个帖子：
[
  {"note_id": "...", "match": true/false, "reason": "≤20字理由"}
]

要求：
- 严格按条件判断，不要宽松匹配
- 信息不足（无 desc）时保守返回 match=false
- 只返回 JSON 数组"""

_SUMMARY_SYSTEM = """你是小红书帖子摘要助手。给定一组帖子（每条含 title 和 desc，其中 desc 可能含 [图片文字] 段落——是 OCR 从图片中提取的文本），
为每条生成 1-2 句中文摘要（≤60 字）。
突出关键信息：主题、地点、价格、要求、时间、联系方式。
返回严格 JSON 数组：[{"note_id": "...", "summary": "..."}]
只返回 JSON 数组，不要 markdown 包装。"""


# ── 客户端 ────────────────────────────────────────────────

_client: Any = None


def _get_client():
    """懒加载 openai 客户端。LLM_ENABLED=False 时调用方会先检查。"""
    global _client
    if _client is None:
        from openai import OpenAI  # 延迟导入，避免未配置时启动报错
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return _client


def _chat_json(messages: list[dict], max_retries: int = 1) -> dict | list | None:
    """调 chat completion 并尝试解析 JSON 返回。

    失败时重试 max_retries 次；都失败返回 None。
    """
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            client = _get_client()
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=0.2,  # 低温度，更稳定的结构化输出
                timeout=60,
            )
            content = resp.choices[0].message.content or ""
            # 兼容 LLM 把 JSON 包在 ```json ... ``` 里的情况
            content = content.strip()
            if content.startswith("```"):
                content = content.strip("`").removeprefix("json").strip()
            return json.loads(content)
        except Exception as e:
            last_err = e
            print(f"  [LLM 失败] 第 {attempt + 1} 次: {e}", file=sys.stderr)
    if last_err:
        print(f"  [LLM 失败] 重试耗尽: {last_err}", file=sys.stderr)
    return None


# ── 公开 API ──────────────────────────────────────────────

def decompose_task(description: str) -> dict:
    """
    自然语言需求 → {name, keywords, filter}。

    Returns:
        {"name": "...", "keywords": ["..."], "filter": "..."}
        失败时返回 {"keywords": [], "filter": "", "_error": "msg", "name": ""}
    """
    if not LLM_ENABLED:
        return {
            "name": "",
            "keywords": [],
            "filter": "",
            "_error": "LLM 未启用（缺少 API key）",
        }

    messages = [
        {"role": "system", "content": _DECOMPOSE_SYSTEM},
        {"role": "user", "content": f"用户需求: {description}"},
    ]
    result = _chat_json(messages, max_retries=1)
    if not isinstance(result, dict):
        return {
            "name": "",
            "keywords": [],
            "filter": "",
            "_error": "LLM 返回非 dict",
        }

    # 字段兜底
    keywords = result.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(k).strip() for k in keywords if str(k).strip()][:5]

    flt = result.get("filter") or ""
    if not isinstance(flt, str):
        flt = ""

    name = _normalize_name(result.get("name"), description)

    return {"name": name, "keywords": keywords, "filter": flt}


def _normalize_name(raw: Any, description: str) -> str:
    """把 LLM 返回的 name 规整成 snake_case slug；无 name 时从 description 派生。

    始终返回 ≤24 字符的 slug（含字母/数字/下划线）；空则返回空串。
    """
    slug = ""
    if isinstance(raw, str):
        slug = raw.strip().lower()
        slug = re.sub(r"[^a-z0-9_]+", "_", slug)
        slug = re.sub(r"_+", "_", slug).strip("_")[:24]
    if slug:
        return slug
    # fallback: 用 description 第一行派生
    seed = description.strip().splitlines()[0] if description else ""
    slug = re.sub(r"[^a-z0-9]+", "_", seed.lower())
    slug = re.sub(r"_+", "_", slug).strip("_")[:24]
    return slug


def filter_posts(posts: list[dict], criteria: str, *, use_llm: bool = True) -> list[dict]:
    """
    批量筛选帖子，1 次 API 调用处理整批。

    Args:
        posts: 帖子列表，每项至少含 note_id, title, desc
        criteria: 筛选条件（自然语言）
        use_llm: False 时跳过 LLM（降级用），直接返回原列表

    Returns:
        命中的帖子列表，每项附加 {llm_match_reason: "..."}。
        若 LLM 不可用 / 调用失败：返回原 posts（无 reason 字段），让 pipeline 降级推送。
    """
    if not posts:
        return []

    if not use_llm or not LLM_ENABLED or not criteria:
        # 降级：直接返回全部（不附加 llm_match_reason）
        return list(posts)

    # 精简输入，控制 token
    # 优先用 combined_desc（含 OCR 文本），fallback 到 desc
    slim = [
        {
            "note_id": p.get("note_id", ""),
            "title": (p.get("title") or "")[:80],
            "desc": (p.get("combined_desc") or p.get("desc") or "")[:600],
            "likes": p.get("likes", 0),
        }
        for p in posts
    ]

    messages = [
        {"role": "system", "content": _FILTER_SYSTEM},
        {
            "role": "user",
            "content": (
                f"筛选条件: {criteria}\n\n"
                f"帖子列表（共 {len(slim)} 条）:\n"
                f"{json.dumps(slim, ensure_ascii=False)}"
            ),
        },
    ]
    result = _chat_json(messages, max_retries=1)
    if not isinstance(result, list):
        print("  [LLM 过滤] 降级：返回全部帖子", file=sys.stderr)
        return list(posts)

    # 按 note_id 建索引，匹配成功的带回原 post
    by_id = {p.get("note_id"): p for p in posts}
    matched: list[dict] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        if not item.get("match"):
            continue
        nid = item.get("note_id", "")
        post = by_id.get(nid)
        if not post:
            continue
        # 复制避免污染原对象
        post_out = dict(post)
        reason = item.get("reason") or ""
        if isinstance(reason, str):
            post_out["llm_match_reason"] = reason[:80]
        matched.append(post_out)

    return matched


def summarize_posts(notes: list[dict], *, use_llm: bool = True, chunk_size: int = 20) -> dict[str, str]:
    """
    批量生成帖子摘要（1 次 API 调用处理一批，最多 chunk_size 条）。

    Args:
        notes: 帖子列表，每项至少含 note_id, title, desc（或 combined_desc）
        use_llm: False 时跳过（降级用）
        chunk_size: 每批最多多少条（>chunk_size 自动分多次调用）

    Returns:
        {note_id: summary}  命中的摘要。失败/未启用 → {}
    """
    if not notes or not use_llm or not LLM_ENABLED:
        return {}

    out: dict[str, str] = {}

    def _one_batch(batch: list[dict]) -> dict[str, str]:
        slim = [
            {
                "note_id": n.get("note_id", ""),
                "title": (n.get("title") or "")[:80],
                "desc": (n.get("combined_desc") or n.get("desc") or "")[:600],
            }
            for n in batch
        ]
        messages = [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": f"帖子列表（共 {len(slim)} 条）:\n{json.dumps(slim, ensure_ascii=False)}"},
        ]
        result = _chat_json(messages, max_retries=1)
        if not isinstance(result, list):
            print("  [LLM 摘要] 降级：返回空（push 用 desc 截断）", file=sys.stderr)
            return {}
        return {
            r.get("note_id", ""): (r.get("summary") or "")[:80]
            for r in result
            if isinstance(r, dict) and r.get("note_id")
        }

    for i in range(0, len(notes), chunk_size):
        out.update(_one_batch(notes[i : i + chunk_size]))
    return out
