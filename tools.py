#!/usr/bin/env python3
"""工具函数：WebFetch / WebSearch / ReadLocal，供圆桌会议 AI 调用查证信息。"""
import asyncio
import json
import os
from pathlib import Path

import html2text
import httpx
from ddgs import DDGS

HOME = Path.home()
ENABLE_READ_LOCAL = os.getenv("YUANZHUO_ENABLE_READ_LOCAL", "").lower() in {"1", "true", "yes", "on"}
_h2t = html2text.HTML2Text()
_h2t.ignore_links = True
_h2t.ignore_images = True
_h2t.body_width = 0


async def web_fetch(url: str) -> str:
    """抓取单个网页，返回纯文本（去 HTML 标签）。超时 15 秒，截断 8000 字符。"""
    if not url.startswith(("http://", "https://")):
        return "❌ 抓取失败：URL 必须以 http:// 或 https:// 开头"
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        text = _h2t.handle(html)
        # 清理多余空行
        lines = [l for l in text.splitlines() if l.strip()]
        text = "\n".join(lines)
        if len(text) > 8000:
            text = text[:8000] + "\n\n... (内容已截断)"
        return text if text.strip() else "❌ 抓取失败：页面无文本内容"
    except httpx.TimeoutException:
        return f"❌ 抓取失败：超时（15s）"
    except httpx.HTTPStatusError as e:
        return f"❌ 抓取失败：HTTP {e.response.status_code}"
    except Exception as e:
        return f"❌ 抓取失败：{type(e).__name__}: {e}"


async def web_search(query: str, num: int = 5) -> str:
    """关键词搜索，返回前 N 条结果（标题 + URL + 摘要）。"""
    try:
        def _search():
            with DDGS() as d:
                return list(d.text(query, max_results=num))

        results = await asyncio.get_event_loop().run_in_executor(None, _search)
        if not results:
            return f"❌ 搜索失败：无结果（query={query!r}）"
        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "（无标题）")
            href = r.get("href", "")
            body = r.get("body", "").strip()
            lines.append(f"{i}. **{title}**\n   {href}\n   {body}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"❌ 搜索失败：{type(e).__name__}: {e}"


def read_local(path: str) -> str:
    """读本地文件，限制在 ~/（用户家目录）内。截断 8000 字符。"""
    if not ENABLE_READ_LOCAL:
        return "❌ read_local is disabled by default. Set YUANZHUO_ENABLE_READ_LOCAL=1 only in a trusted local environment."
    try:
        p = Path(path).expanduser().resolve()
    except Exception as e:
        return f"❌ 路径解析失败：{e}"
    if not p.is_relative_to(HOME):
        return f"❌ 路径不允许：只能读取 ~/ 目录下的文件（请求路径：{p}）"
    if not p.exists():
        return f"❌ 文件不存在：{p}"
    if not p.is_file():
        return f"❌ 路径不是文件：{p}"
    try:
        content = p.read_text(errors="ignore")
    except UnicodeDecodeError:
        return "❌ 不支持二进制文件，请用图片附件机制"
    except Exception as e:
        return f"❌ 读取失败：{e}"
    if len(content) > 8000:
        content = content[:8000] + "\n\n... (内容已截断)"
    return content


# ── OpenAI tool_calls 格式定义 ──────────────────────────────────────────────

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取单个网页内容，返回去标签后的纯文本。用于查证具体网页信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "网页 URL，必须以 http:// 或 https:// 开头",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "关键词搜索，返回前 N 条结果（标题 + URL + 摘要）。用于查找信息或验证观点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "num": {
                        "type": "integer",
                        "description": "返回结果数量，默认 5，最多 10",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_local",
            "description": "读取本地文件内容，只允许读取 ~/ 用户家目录下的文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，支持 ~ 展开，必须在用户家目录内",
                    }
                },
                "required": ["path"],
            },
        },
    },
]

TOOL_HANDLERS = {
    "web_fetch": web_fetch,
    "web_search": web_search,
}

if ENABLE_READ_LOCAL:
    TOOL_HANDLERS["read_local"] = read_local
else:
    TOOL_DEFS = [item for item in TOOL_DEFS if item["function"]["name"] != "read_local"]


def make_tool_summary(name: str, args: dict, result: str) -> str:
    """生成工具调用的 UI 摘要行。"""
    char_count = len(result)
    if name == "web_fetch":
        url = args.get("url", "")
        domain = url.split("/")[2] if url.count("/") >= 2 else url
        return f"📡 抓取 {domain}（{char_count} 字）"
    elif name == "web_search":
        query = args.get("query", "")
        lines = [l for l in result.splitlines() if l.strip().startswith(("1.", "2.", "3.", "4.", "5."))]
        count = len(lines)
        return f"🔍 搜索\"{query}\"（{count} 条结果）"
    elif name == "read_local":
        path = args.get("path", "")
        fname = Path(path).name
        return f"📄 读取文件 {fname}（{char_count} 字）"
    else:
        return f"🔧 调用工具 {name}"
