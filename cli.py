#!/usr/bin/env python3
import argparse
import asyncio
import base64
import inspect
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pypdf
from openai import AsyncOpenAI
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from tools import ENABLE_READ_LOCAL, TOOL_DEFS, TOOL_HANDLERS, make_tool_summary

HOME = Path.home()
BASE = Path(__file__).parent
HISTORY_FILE = HOME / ".yuanzhuo" / "history.json"
EXPORT_DIR = Path(os.getenv("YUANZHUO_EXPORT_DIR", str(HOME / ".yuanzhuo" / "exports")))
HISTORY_MAX = 10  # 最多保留最近 10 个议题


def _load_dotenv() -> None:
    """加载项目根目录 .env；不覆盖已经存在的环境变量。"""
    env_path = BASE / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)) or default))
    except ValueError:
        return default


STOP_MARKER = "[散会]"
USER_MARKER = "[ASK_USER]"
MAX_TURNS = 20  # 兜底上限，每方最多 10 轮
UNIT_SIZE = 4   # 一个小辩论单元 = 双方各 2 轮，跑完暂停问 USER

RELAY_BASE_URL = os.getenv("YUANZHUO_RELAY_BASE_URL", "https://api.openai.com/v1").rstrip("/")
RELAY_API_KEY = os.getenv("YUANZHUO_RELAY_API_KEY", "")
LOCAL_API_KEY_PLACEHOLDER = "yuanzhuo-local-relay-key"
ANALYST_MODEL = os.getenv("YUANZHUO_ANALYST_MODEL", "gpt-4.1")
EXECUTOR_MODEL = os.getenv("YUANZHUO_EXECUTOR_MODEL", "gpt-4.1")
LOCAL_ASSISTANT_CMD = os.getenv("YUANZHUO_LOCAL_ASSISTANT_CMD", "").strip()

ATTACH_TEXT_EXTS = {
    ".md", ".txt", ".py", ".json", ".yaml", ".yml",
    ".csv", ".html", ".js", ".ts", ".sh", ".log", ".sql",
    ".tsx", ".jsx", ".css", ".xml", ".toml", ".ini",
}
ATTACH_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ATTACH_PDF_EXT = ".pdf"
ATTACH_MAX_BYTES = 50_000  # 单个文本文件最大 50KB，超过截断
ATTACH_IMG_MAX_BYTES = 5_000_000  # 单张图片最大 5MB，超过跳过

TOOL_HINT = (
    "【工具能力】必要时你可以调用以下工具查证信息：\n"
    "- web_fetch(url)：抓取单个网页内容\n"
    "- web_search(query)：关键词搜索\n"
    + ("- read_local(path)：读 ~/ 目录下本地文件（仅在可信本机显式启用）\n" if ENABLE_READ_LOCAL else "")
    + "优先用你的判断力和常识，只在确实需要外部信息时才调用工具。"
    "工具调用计入本轮上限（5 次），尽量精简。\n\n"
)

FIRST_RULE = (
    "\n\n【开场规则】不超过 300 字。给出：核心判断（1-2句）、"
    "具体建议（2-3条）、关键风险（1-2条）、一句话忠告。"
    "等待对方回应，不要写 [散会]。\n\n"
)

DEBATE_RULE = (
    "\n\n【本轮规则】不超过 200 字，直接说推理，不列长清单。"
    "如果 USER 在本轮讨论中有发言，先用 1 句话回应 USER，再和对方辩论。"
    "先指出对方推理中至少 1 处你不认同之处，再给出你的反驳或修正判断。"
    "⚠️ 不要写 [散会]，是否结束由 USER 决定。即使你觉得双方接近共识，也继续推进——指出新角度、追问细节、或换一个切入点深挖。"
    "⚠️ 如果本轮出现了你无法判断、必须听 USER 本人意见才能继续的关键信息缺口，在发言末尾加 [ASK_USER]。否则不加。\n\n"
)

SIMA_FIRST_RULE = (
    "\n\n【开场规则】不超过 120 字。直接给核心判断（1句）+ 最关键的理由（2条）。"
    "等对方回应，不要写 [散会]。\n\n"
)

SIMA_DEBATE_RULE = (
    "\n\n【本轮规则】不超过 100 字。"
    "如果 USER 在本轮讨论中有发言，先用 1 句话回应 USER，再和对方辩论。"
    "先点出对方 1 处推理漏洞，再给你的反驳结论。只说最关键的一点，不列清单。"
    "⚠️ 不要写 [散会]，是否结束由 USER 决定。即使你觉得对方说的对，也继续追问执行细节、ROI 假设、或边界条件。"
    "⚠️ 如果本轮出现了你无法判断、必须听 USER 本人意见才能继续的关键信息缺口，在发言末尾加 [ASK_USER]。否则不加。\n\n"
)

# ── 模板 ────────────────────────────────────────────────────────────────────

TEMPLATES_DIR = BASE / "templates"
ROLES_DIR = BASE / "roles"

TEMPLATE_LIST = [
    ("selection",    "选品评估"),
    ("sidehustle",   "副业方向利弃分析"),
    ("negotiation",  "谈判/沟通脚本"),
    ("swot",         "SWOT 框架决策"),
    ("free",         "自由议题（默认）"),
]


def load_template(name: str) -> str:
    """加载模板文件，返回 body（去掉 YAML frontmatter 后的内容）。失败返回空字符串。"""
    path = TEMPLATES_DIR / f"{name}.md"
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8")
    # 去掉 --- ... --- frontmatter
    if raw.startswith("---"):
        end = raw.find("---", 3)
        if end != -1:
            raw = raw[end + 3:].lstrip("\n")
    return raw


def apply_template(template_name: str, topic: str) -> str:
    """把模板 body 里的 {topic} 替换为实际议题。"""
    body = load_template(template_name)
    if not body:
        return topic  # 降级：直接用原始议题
    return body.replace("{topic}", topic)


def pick_template_interactive(console: Console) -> str:
    """交互式选择模板，返回模板 key（如 'free'）。"""
    console.print("\n[bold]请选择议题模式：[/bold]")
    for i, (key, label) in enumerate(TEMPLATE_LIST, 1):
        console.print(f"  {i}. {label}")
    console.print()
    try:
        raw = Prompt.ask("[bold]选择[/bold] [dim](1-5，默认 5)[/dim]", default="5")
        idx = int(raw.strip()) - 1
        if 0 <= idx < len(TEMPLATE_LIST):
            return TEMPLATE_LIST[idx][0]
    except (ValueError, KeyboardInterrupt, EOFError):
        pass
    return "free"


# ── 角色系统 ─────────────────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (meta_dict, body)。"""
    meta = {}
    body = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            fm_block = text[3:end].strip()
            for line in fm_block.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            body = text[end + 3:].lstrip("\n")
    return meta, body


def _model_for_role(file_name: str, meta: dict) -> str:
    if file_name == "zhuge":
        return ANALYST_MODEL
    if file_name == "sima":
        return EXECUTOR_MODEL
    return meta.get("model", ANALYST_MODEL)


def load_roles() -> list[dict]:
    """从 roles/ 目录加载角色列表（跳过 hidden: true 的特殊角色）。失败时降级用 prompts/。"""
    roles = []
    # Fixed order: analyst, executor, investor, user advocate, operator, mentor.
    order = ["zhuge", "sima", "investor", "user", "boss", "mentor"]
    if ROLES_DIR.exists():
        for name in order:
            path = ROLES_DIR / f"{name}.md"
            if path.exists():
                raw = path.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(raw)
                # 跳过 hidden 角色（researcher, moderator 等内部角色）
                if meta.get("hidden", "").lower() == "true":
                    continue
                roles.append({
                    "file": name,
                    "name": meta.get("name", name),
                    "short": meta.get("short", name),
                    "color": meta.get("color", "white"),
                    "icon": meta.get("icon", "🤖"),
                    "model": _model_for_role(name, meta),
                    "body": body,
                })
    # 降级：如果 roles/ 不存在或为空，用 prompts/
    if not roles:
        for fname, display in [("zhuge", "分析型辩手"), ("sima", "执行型辩手")]:
            path = BASE / "prompts" / f"{fname}.md"
            if path.exists():
                body = path.read_text(encoding="utf-8")
                roles.append({
                    "file": fname,
                    "name": display,
                    "short": "analyst" if fname == "zhuge" else "executor",
                    "color": "green" if fname == "zhuge" else "red",
                    "icon": "🧠" if fname == "zhuge" else "💰",
                    "model": ANALYST_MODEL if fname == "zhuge" else EXECUTOR_MODEL,
                    "body": body,
                })
    return roles


def load_role(name: str) -> dict | None:
    """按文件名加载单个角色（含 hidden 角色）。"""
    path = ROLES_DIR / f"{name}.md"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    return {
        "file": name,
        "name": meta.get("name", name),
        "short": meta.get("short", name),
        "color": meta.get("color", "white"),
        "icon": meta.get("icon", "🤖"),
        "model": _model_for_role(name, meta),
        "body": body,
    }


def pick_role_interactive(console: Console, roles: list[dict], slot: str, default_idx: int) -> dict:
    """交互式选择角色，返回角色 dict。"""
    default_num = default_idx + 1
    console.print(f"\n[bold]请选择 {slot} 角色（默认 {default_num}. {roles[default_idx]['name']}）：[/bold]")
    for i, r in enumerate(roles, 1):
        console.print(f"  {i}. {r['name']} {r['icon']} ({r['model']})")
    console.print()
    try:
        raw = Prompt.ask(
            f"[bold]选择[/bold] [dim](1-{len(roles)}，默认 {default_num})[/dim]",
            default=str(default_num),
        )
        idx = int(raw.strip()) - 1
        if 0 <= idx < len(roles):
            return roles[idx]
    except (ValueError, KeyboardInterrupt, EOFError):
        pass
    return roles[default_idx]


# ── PDF 附件 ─────────────────────────────────────────────────────────────────

def _extract_pdf_text(path: Path) -> tuple[str, int]:
    """返回 (text, page_count)。失败抛 Exception。"""
    reader = pypdf.PdfReader(str(path))
    pages = len(reader.pages)
    text = "\n\n".join(p.extract_text() or "" for p in reader.pages)
    return text, pages


# ── 持久化 ───────────────────────────────────────────────────────────────────

def load_chain() -> list:
    """从 ~/.yuanzhuo/history.json 读取历史链，失败返回空列表。"""
    try:
        return json.loads(HISTORY_FILE.read_text())
    except Exception:
        return []


def save_chain(chain: list) -> None:
    """持久化 chain 到 ~/.yuanzhuo/history.json，只保留最近 HISTORY_MAX 个议题。"""
    trimmed = chain[-HISTORY_MAX:]
    data = []
    for e in trimmed:
        entry = {
            "topic": e["topic"],
            "history": [[role, text] for role, text in e["history"]],
        }
        for field in ("scores", "tags", "created_at", "todos"):
            if field in e:
                entry[field] = e[field]
        data.append(entry)
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


# ── 附件解析 ─────────────────────────────────────────────────────────────────

def parse_attachments(text: str) -> tuple[str, str, list[str]]:
    """从用户输入中提取文件路径，返回 (清理后的议题文本, 文本附件块, 图片 data URL 列表)。"""
    pattern = r'(~?/[^\s\\]*(?:\\\s[^\s\\]*)*\.[a-zA-Z]{1,5})'
    paths = re.findall(pattern, text)
    text_blocks: list[str] = []
    image_urls: list[str] = []
    cleaned = text
    pdf_count = 0

    for raw in paths:
        actual = raw.replace('\\ ', ' ')
        p = Path(actual).expanduser()
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in ATTACH_TEXT_EXTS:
            try:
                content = p.read_text(errors='ignore')
            except Exception:
                continue
            if len(content) > ATTACH_MAX_BYTES:
                content = content[:ATTACH_MAX_BYTES] + "\n\n... (内容已截断，原文更长)"
            text_blocks.append(f"## 附件文件：{p.name}\n```\n{content}\n```")
            cleaned = cleaned.replace(raw, f"[已加载附件：{p.name}]")
        elif ext == ATTACH_PDF_EXT:
            try:
                content, pages = _extract_pdf_text(p)
            except Exception:
                cleaned = cleaned.replace(raw, f"[PDF读取失败：{p.name}]")
                continue
            if len(content) > ATTACH_MAX_BYTES:
                content = content[:ATTACH_MAX_BYTES] + "\n\n... (内容已截断)"
            text_blocks.append(f"## PDF 附件：{p.name}（{pages} 页）\n```\n{content}\n```")
            cleaned = cleaned.replace(raw, f"[已加载 PDF：{p.name}（{pages}页）]")
            pdf_count += 1
        elif ext in ATTACH_IMG_EXTS:
            size = p.stat().st_size
            if size > ATTACH_IMG_MAX_BYTES:
                size_mb = size / 1_000_000
                cleaned = cleaned.replace(
                    raw, f"[图片：{p.name}（{size_mb:.1f}MB > 5MB 已跳过）]"
                )
                continue
            try:
                with p.open('rb') as f:
                    b64 = base64.b64encode(f.read()).decode()
            except Exception:
                continue
            mime = "image/jpeg" if ext in {".jpg", ".jpeg"} else f"image/{ext[1:]}"
            image_urls.append(f"data:{mime};base64,{b64}")
            cleaned = cleaned.replace(raw, f"[图片：{p.name}]")

    attach_text = "\n\n" + "\n\n".join(text_blocks) if text_blocks else ""
    return cleaned.strip(), attach_text, image_urls


# ── 导出 ─────────────────────────────────────────────────────────────────────

def _make_slug(topic: str) -> str:
    """生成文件名用的 slug：只保留中文/英文/数字，空格换 -，取前 30 字符。"""
    chars = []
    for ch in topic:
        if '一' <= ch <= '鿿' or ch.isalnum():
            chars.append(ch)
        elif ch in (' ', '\t'):
            chars.append('-')
    slug = ''.join(chars)[:30].strip('-')
    return slug or "yuanzhuo"


def _parse_todos_from_secretary(secretary_text: str) -> list[dict]:
    """从秘书总结中提取待办清单（## 待办清单 段落下的 - [ ] 条目）。"""
    todos = []
    in_todo_section = False
    now_iso = datetime.now().isoformat(timespec="seconds")
    for line in secretary_text.splitlines():
        if re.match(r"^#{1,3}\s*待办清单", line):
            in_todo_section = True
            continue
        if in_todo_section:
            # 遇到下一个 ## 段落则退出
            if re.match(r"^#{1,3}\s", line) and not re.match(r"^#{1,3}\s*待办", line):
                break
            m = re.match(r"^\s*-\s*\[[ x]\]\s*(.+)", line)
            if m:
                todos.append({
                    "text": m.group(1).strip(),
                    "status": "pending",
                    "created_at": now_iso,
                })
    return todos


def export_debate(
    topic: str,
    history: list,
    secretary: str,
    duration: int,
    role_a: dict | None = None,
    role_b: dict | None = None,
    scores: dict | None = None,
    todos: list | None = None,
) -> Path:
    """Write the debate transcript as Markdown outside the repository."""
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    slug = _make_slug(topic)
    filename = f"yuanzhuo-{slug}-{ts}.md"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    dest = EXPORT_DIR / filename

    name_a = role_a["name"] if role_a else "分析型辩手"
    name_b = role_b["name"] if role_b else "执行型辩手"
    model_a = role_a["model"] if role_a else ANALYST_MODEL
    model_b = role_b["model"] if role_b else EXECUTOR_MODEL

    lines = [f"# 议题：{topic}", ""]
    turns = len(history)
    lines.append(
        f"*{datetime.now().strftime('%Y-%m-%d %H:%M')} · 共 {turns} 轮 · "
        f"用时 {duration} 秒 · {name_a}({model_a}) × {name_b}({model_b})*"
    )
    lines += ["", "## 完整辩论", ""]

    for role, text in history:
        if role == "zhuge":
            name = name_a
        elif role == "sima":
            name = name_b
        else:
            name = "USER"
        lines.append(f"### {name}")
        lines.append("")
        lines.append(text)
        lines.append("")

    lines += ["## 秘书总结", "", secretary, ""]

    # 待办清单（checkbox 格式）
    if todos:
        lines += ["## 待办清单", ""]
        for t in todos:
            check = "x" if t.get("status") == "done" else " "
            lines.append(f"- [{check}] {t['text']}")
        lines.append("")

    # 功能 F：附加评分段落
    if scores:
        lines += [
            "## 本场评分",
            "",
            f"- 🧠 深度：{scores.get('depth', '?')}/5",
            f"- 🤝 共识：{scores.get('consensus', '?')}/5",
            f"- ⚡ 执行：{scores.get('execution', '?')}/5",
            f"- 评语：{scores.get('comment', '')}",
            "",
        ]

    lines += ["---", f"*由圆桌会议生成 · ~/bin/yuanzhuo*"]

    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


# ── 本机接力功能（默认关闭）───────────────────────────────────────────────

def launch_local_assistant_with_md(md_path: Path, console: Console) -> None:
    """在新 Terminal 窗口启动本机助手命令执行 md 待办。"""
    if not LOCAL_ASSISTANT_CMD:
        console.print("[dim]本机接力功能未启用。[/dim]")
        return
    path_str = str(md_path)
    cmd = f"{LOCAL_ASSISTANT_CMD} '请读 {path_str} 的待办清单并按顺序协助我执行'"
    # 转义单引号给 osascript
    osa_cmd = cmd.replace("'", "'\\''")
    osa = f'tell application "Terminal" to do script "{osa_cmd}"'
    try:
        subprocess.Popen(["osascript", "-e", osa])
        console.print(f"[dim]已在新 Terminal 窗口启动本机助手。[/dim]")
    except Exception as e:
        # 降级：打印命令让 USER 自己复制
        console.print(f"\n[dim]osascript 启动失败（{e}），请手动在终端运行：[/dim]")
        console.print(f"[bold]{cmd}[/bold]")


async def ask_local_assistant_handoff(md_path: Path, console: Console) -> None:
    """散会后询问是否启动本机助手接力。"""
    if not LOCAL_ASSISTANT_CMD:
        return
    rel = str(md_path).replace(str(HOME), "~")
    console.print(f"\n[dim]是否启动本机助手执行待办清单？(y/N，默认 N):[/dim] ", end="")
    try:
        ans = await asyncio.get_event_loop().run_in_executor(None, input)
        ans = ans.strip().lower()
    except (KeyboardInterrupt, EOFError):
        ans = ""
    if ans == "y":
        launch_local_assistant_with_md(md_path, console)


# ── 功能 F：议题质量评分 ────────────────────────────────────────────────────

async def rate_debate(topic: str, history: list, summary: str, console: Console) -> dict | None:
    """让默认评估模型给本场辩论打分，返回 scores dict 或 None（失败时）。"""
    transcript = build_history(history)
    prompt = (
        "你是辩论质量评估官。基于以下辩论和总结，给出 3 个分数（1-5 整数）和简短理由。\n"
        "评分维度：\n"
        "1. 深度（1=表面；5=触及本质）\n"
        "2. 共识度（1=无共识；5=高度一致）\n"
        "3. 执行力（1=空谈；5=可立即落地）\n\n"
        f"## 议题\n{topic}\n\n## 辩论\n{transcript}\n\n## 秘书总结\n{summary}\n\n"
        "用 JSON 格式输出（仅 JSON，不要包代码块）：\n"
        '{"depth": 4, "consensus": 3, "execution": 5, "comment": "一句话评语"}'
    )

    client = AsyncOpenAI(api_key=RELAY_API_KEY or LOCAL_API_KEY_PLACEHOLDER, base_url=RELAY_BASE_URL)
    full = ""
    try:
        stream = await client.chat.completions.create(
            model=ANALYST_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                full += delta.content
    except Exception:
        return None

    # 解析 JSON（容错）
    try:
        match = re.search(r'\{[^{}]*\}', full)
        if match:
            scores = json.loads(match.group())
            return scores
    except Exception:
        pass
    return None


def display_scores(scores: dict, console: Console) -> None:
    """在终端显示评分卡。"""
    depth = scores.get("depth", "?")
    consensus = scores.get("consensus", "?")
    execution = scores.get("execution", "?")
    comment = scores.get("comment", "")
    lines = [
        f"🧠 深度：{depth}/5",
        f"🤝 共识：{consensus}/5",
        f"⚡ 执行：{execution}/5",
        f"",
        f'"{comment}"',
    ]
    panel = Panel(
        "\n".join(lines),
        title="[bold gold1]本场评分[/bold gold1]",
        border_style="gold1",
        expand=False,
    )
    console.print(panel)


# ── 功能 G：从 PDF/网页自动抽议题 ──────────────────────────────────────────

async def extract_topics_from_source(source: str, console: Console) -> list[str]:
    """从文件路径或 URL 提取议题列表。"""
    content = ""

    if source.startswith("http://") or source.startswith("https://"):
        # 网页 URL
        console.print(f"[dim]正在抓取网页：{source}[/dim]")
        try:
            from tools import web_fetch
            content = await web_fetch(source)
            if content.startswith("❌"):
                console.print(f"[red]{content}[/red]")
                return []
        except Exception as e:
            console.print(f"[red]抓取失败：{e}[/red]")
            return []
    else:
        # 本地文件
        try:
            p = Path(source).expanduser().resolve()
        except Exception as e:
            console.print(f"[red]路径解析失败：{e}[/red]")
            return []
        if not p.is_relative_to(HOME):
            console.print("[red]本地路径只允许读取用户 home 目录下的文件。[/red]")
            return []
        if not p.exists():
            console.print(f"[red]文件不存在：{p}[/red]")
            return []
        if not p.is_file():
            console.print(f"[red]路径不是文件：{p}[/red]")
            return []
        ext = p.suffix.lower()
        if ext == ".pdf":
            console.print(f"[dim]正在解析 PDF：{p.name}[/dim]")
            try:
                content, pages = _extract_pdf_text(p)
                console.print(f"[dim]已解析 {pages} 页[/dim]")
            except Exception as e:
                console.print(f"[red]PDF 解析失败：{e}[/red]")
                return []
        else:
            try:
                content = p.read_text(errors="ignore")
            except Exception as e:
                console.print(f"[red]文件读取失败：{e}[/red]")
                return []

    # 截断到 8000 字
    if len(content) > 8000:
        content = content[:8000] + "\n\n... (内容已截断)"

    if not content.strip():
        console.print("[red]内容为空，无法提取议题。[/red]")
        return []

    console.print("[dim]正在提取议题…[/dim]")
    prompt = (
        "下面是一份资料。请抽取 3-5 个最值得辩论的议题（核心决策点/有争议的判断/可深挖的角度）。"
        "每个议题一行，格式：「编号. 议题描述（一句话）」。\n\n"
        f"资料：\n{content}"
    )

    client = AsyncOpenAI(api_key=RELAY_API_KEY or LOCAL_API_KEY_PLACEHOLDER, base_url=RELAY_BASE_URL)
    full = ""
    try:
        stream = await client.chat.completions.create(
            model=ANALYST_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                full += delta.content
    except Exception as e:
        console.print(f"[red]提取失败：{e}[/red]")
        return []

    # 解析按编号的议题列表
    topics = []
    for line in full.splitlines():
        line = line.strip()
        m = re.match(r'^[「\[]?(\d+)[.、。\]」]\s*(.+)', line)
        if m:
            topics.append(m.group(2).strip())
    return topics


async def pick_extracted_topic(source: str, console: Console) -> str | None:
    """抽取议题并让 USER 选择，返回选中的议题文本，或 None。"""
    topics = await extract_topics_from_source(source, console)
    if not topics:
        console.print("[red]未能提取到议题，请手动输入议题。[/red]")
        return None

    console.print(f"\n[bold]从资料中抽出 {len(topics)} 个议题：[/bold]")
    for i, t in enumerate(topics, 1):
        console.print(f"  {i}. {t}")
    console.print()

    try:
        raw = Prompt.ask(
            f"[bold]选一个开始辩论[/bold] [dim](1-{len(topics)}，q 退出)[/dim]",
            default="1",
        )
        if raw.strip().lower() == "q":
            return None
        idx = int(raw.strip()) - 1
        if 0 <= idx < len(topics):
            return topics[idx]
    except (ValueError, KeyboardInterrupt, EOFError):
        pass
    return None


# ── 功能 B：议题 resume ─────────────────────────────────────────────────────

async def resume_debate_interactive(chain: list, console: Console) -> tuple[str, list] | None:
    """列出历史议题，让 USER 选择续辩。返回 (topic, prev_history) 或 None。"""
    if not chain:
        console.print("[dim]暂无历史议题。[/dim]")
        return None

    console.print(f"\n[bold]最近议题（共 {len(chain)} 个）：[/bold]")
    # 最新在前
    for i, entry in enumerate(reversed(chain), 1):
        topic = entry["topic"]
        turns = len(entry.get("history", []))
        tags = entry.get("tags", [])
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        console.print(f"  {i}. {topic}{tag_str} [dim]({turns} 轮)[/dim]")
    console.print()

    try:
        raw = Prompt.ask(
            f"[bold]选择继续[/bold] [dim](1-{len(chain)}，q 退出)[/dim]",
            default="q",
        )
        if raw.strip().lower() == "q":
            return None
        idx = int(raw.strip()) - 1
        if 0 <= idx < len(chain):
            # reversed 列表中第 idx 个对应 chain 中 chain[-(idx+1)]
            entry = chain[-(idx + 1)]
            return entry["topic"], list(entry.get("history", []))
    except (ValueError, KeyboardInterrupt, EOFError):
        pass
    return None


# ── 提示构建 ─────────────────────────────────────────────────────────────────

def build_history(history: list[tuple[str, str]]) -> str:
    parts = []
    for role, text in history:
        if role == "zhuge":
            name = "A 角色"
        elif role == "sima":
            name = "B 角色"
        else:
            name = "USER"
        parts.append(f"**{name}**：\n{text}")
    return "\n\n---\n\n".join(parts)


def build_history_with_names(history: list[tuple[str, str]], name_a: str = "分析型辩手", name_b: str = "执行型辩手") -> str:
    parts = []
    for role, text in history:
        if role == "zhuge":
            name = name_a
        elif role == "sima":
            name = name_b
        else:
            name = "USER"
        parts.append(f"**{name}**：\n{text}")
    return "\n\n---\n\n".join(parts)


def build_chain_context(chain: list) -> str:
    if not chain:
        return ""
    parts = ["【历史讨论背景 — 本轮议题与此相关，可参考衔接】\n"]
    for entry in chain[-2:]:
        parts.append(f"**议题**：{entry['topic']}\n")
        parts.append(build_history(entry["history"]))
        parts.append("\n")
    return "\n".join(parts) + "\n\n"


def build_zhuge_prompt(char: str, topic: str, history: list, turn_idx: int, prev_ctx: str = "", attach: str = "") -> str:
    if turn_idx == 0:
        return TOOL_HINT + char + prev_ctx + FIRST_RULE + f"# 当前议题\n\n{topic}{attach}"
    ctx = build_history(history)
    return (
        TOOL_HINT + char + prev_ctx + DEBATE_RULE +
        f"# 当前议题\n\n{topic}{attach}\n\n"
        f"# 本轮讨论\n\n{ctx}\n\n"
        "请分析对方最新一轮的推理，给出你的回应。"
    )


def build_sima_prompt(char: str, topic: str, history: list, prev_ctx: str = "", attach: str = "") -> str:
    ctx = build_history(history)
    rule = SIMA_FIRST_RULE if len(history) == 1 else SIMA_DEBATE_RULE
    return (
        TOOL_HINT + char + prev_ctx + rule +
        f"# 当前议题\n\n{topic}{attach}\n\n"
        f"# 本轮讨论\n\n{ctx}\n\n"
        "请分析对方最新一轮的推理，给出你的回应。"
    )


def _build_messages(prompt: str, images: list[str] | None):
    if not images:
        return [{"role": "user", "content": prompt}]
    content = [{"type": "text", "text": prompt}]
    for url in images:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return [{"role": "user", "content": content}]


MAX_TOOL_CALLS = 5        # 单轮工具调用上限
CONFIRM_THRESHOLD = _env_int("YUANZHUO_TOOL_CONFIRM_THRESHOLD", 0)  # 默认每次工具调用都问 USER


async def _cli_confirm_callback(action: str, data: dict, console: Console) -> bool:
    """USER 确认回调：第 6 次起询问是否允许工具调用。"""
    if action == "confirm":
        console.print(
            f"\n[yellow]⚠️ AI 想第 {data['count']} 次调工具：{data['name']}({data['args']})[/yellow]"
        )
        console.print("[bold]允许？[/bold] [dim](y/n，默认 y)[/dim]:", end=" ")
        try:
            ans = await asyncio.get_event_loop().run_in_executor(None, input)
            return ans.strip().lower() != "n"
        except (KeyboardInterrupt, EOFError):
            return False
    return False


async def stream_with_tools(model: str, prompt: str, images: list[str] | None, confirm_callback, max_tools: int | None = None):
    """支持工具调用的流式生成器。

    yield 格式：
    - {"type": "text", "content": str}
    - {"type": "tool_result", "summary": str}

    max_tools: 覆盖 MAX_TOOL_CALLS 上限（用于调研员等受限场景）
    """
    client = AsyncOpenAI(api_key=RELAY_API_KEY or LOCAL_API_KEY_PLACEHOLDER, base_url=RELAY_BASE_URL)
    messages = _build_messages(prompt, images)
    tool_call_count = 0
    effective_max = max_tools if max_tools is not None else MAX_TOOL_CALLS

    while True:
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                tools=TOOL_DEFS,
                tool_choice="auto",
                extra_body={"reasoning_effort": "high"},
            )
        except Exception:
            yield {"type": "text", "content": f"\n\n⚠️ 连不上中转站（{RELAY_BASE_URL}），请确认服务是否在运行。\n"}
            return

        accumulated_text = ""
        accumulated_tool_calls: list[dict] = []

        try:
            async for chunk in stream:
                delta = chunk.choices[0].delta

                if delta.content:
                    accumulated_text += delta.content
                    yield {"type": "text", "content": delta.content}

                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        while len(accumulated_tool_calls) <= idx:
                            accumulated_tool_calls.append({"id": "", "name": "", "args": ""})
                        if tc_delta.id:
                            accumulated_tool_calls[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                accumulated_tool_calls[idx]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                accumulated_tool_calls[idx]["args"] += tc_delta.function.arguments
        except Exception as e:
            yield {"type": "text", "content": f"\n\n⚠️ 流读取异常：{e}\n"}
            return

        # 没有工具调用 → 正常结束
        if not accumulated_tool_calls:
            return

        # 过滤掉 name 或 id 为空的不完整条目
        accumulated_tool_calls = [tc for tc in accumulated_tool_calls if tc["name"] and tc["id"]]
        if not accumulated_tool_calls:
            return

        # 触顶检查
        if tool_call_count + len(accumulated_tool_calls) > effective_max:
            yield {"type": "text", "content": "\n\n[⚠️ 工具调用已达本轮上限，用现有信息继续辩论]\n"}
            messages.append({"role": "assistant", "content": accumulated_text or "（思考中）"})
            messages.append({
                "role": "user",
                "content": f"工具调用已达本轮 {effective_max} 次上限，请基于现有信息直接给出你的发言，不要再调用工具。",
            })
            continue

        # 把 assistant tool_call 消息加入对话历史
        messages.append({
            "role": "assistant",
            "content": accumulated_text or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["args"]},
                }
                for tc in accumulated_tool_calls
            ],
        })

        # 执行每个工具
        for tc in accumulated_tool_calls:
            tool_call_count += 1

            # 超过免确认阈值，询问 USER
            if tool_call_count > CONFIRM_THRESHOLD:
                allowed = await confirm_callback("confirm", {
                    "name": tc["name"],
                    "args": tc["args"],
                    "count": tool_call_count,
                })
                if not allowed:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "❌ 用户拒绝了此次工具调用，请用其他方式继续",
                    })
                    continue

            try:
                args = json.loads(tc["args"]) if tc["args"] else {}
                handler = TOOL_HANDLERS.get(tc["name"])
                if not handler:
                    result = f"❌ 未知工具：{tc['name']}"
                elif inspect.iscoroutinefunction(handler):
                    result = await handler(**args)
                else:
                    result = handler(**args)
            except Exception as e:
                result = f"❌ 工具执行异常：{type(e).__name__}: {e}"

            summary = make_tool_summary(tc["name"], args, result)
            yield {"type": "tool_result", "summary": summary}

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result[:8000],
            })

        # 继续循环让 AI 消化工具结果


async def _deny_callback(action: str, data: dict) -> bool:
    return False


async def stream_model(model: str, prompt: str, images: list[str] | None = None, confirm_callback=None, max_tools: int | None = None):
    """Yield dict events from a given model with tool support."""
    cb = confirm_callback or _deny_callback
    async for event in stream_with_tools(model, prompt, images, cb, max_tools=max_tools):
        yield event


# ── 功能 I：议题预热 ─────────────────────────────────────────────────────────

async def run_research(topic: str, attach: str, image_urls: list[str], console: Console, max_tools: int = 3) -> str:
    """议题预热：调研员用工具收集背景信息，返回案情材料文本。"""
    role = load_role("researcher")
    if not role:
        return ""

    role_prompt = role["body"].replace("{max_tools}", str(max_tools))
    prompt = f"{role_prompt}\n\n# 议题\n{topic}{attach}"

    research_text = ""
    title = "[bold cyan]📚 议题预热[/bold cyan]"

    with Live(
        Panel("调研中…", title=title, border_style="cyan", expand=True),
        console=console,
        refresh_per_second=15,
        vertical_overflow="visible",
    ) as live:
        async for event in stream_with_tools(role["model"], prompt, image_urls or None, _deny_callback, max_tools=max_tools):
            if event["type"] == "text":
                research_text += event["content"]
                live.update(Panel(Markdown(research_text), title=title, border_style="cyan", expand=True))
            elif event["type"] == "tool_result":
                summary = event["summary"]
                research_text += f"\n\n> {summary}\n\n"
                live.update(Panel(Markdown(research_text), title=title, border_style="cyan", expand=True))

    return research_text.strip()


# ── 功能 J：AI 主持人 ─────────────────────────────────────────────────────────

async def run_moderator(topic: str, history: list, console: Console) -> str:
    """每轮辩完调主持人点评，返回点评文本（≤50字）。"""
    role = load_role("moderator")
    if not role:
        return ""

    transcript = build_history(history[-4:])  # 最近 4 条
    prompt = (
        f"{role['body']}\n\n"
        f"# 议题\n{topic}\n\n"
        f"# 最近发言\n{transcript}\n\n"
        "请点评（≤50 字）："
    )

    client = AsyncOpenAI(api_key=RELAY_API_KEY or LOCAL_API_KEY_PLACEHOLDER, base_url=RELAY_BASE_URL)
    text = ""
    try:
        stream = await client.chat.completions.create(
            model=role["model"],
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                text += delta.content
    except Exception:
        return ""
    return text.strip()[:100]  # 截断保护


# ── 功能 K：自动推断标签 ─────────────────────────────────────────────────────

async def auto_tag(topic: str) -> list[str]:
    """给议题自动打 1-3 个标签（fire-and-forget，失败静默）。"""
    prompt = (
        f"给以下议题打 1-3 个标签（从这些类别选）：选品/副业/家庭/技术/财务/职场/AI跨境/学习/其他\n"
        f"议题：{topic}\n"
        f"只输出标签（用逗号分隔），不要解释。"
    )
    client = AsyncOpenAI(api_key=RELAY_API_KEY or LOCAL_API_KEY_PLACEHOLDER, base_url=RELAY_BASE_URL)
    text = ""
    try:
        stream = await client.chat.completions.create(
            model=ANALYST_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                text += delta.content
    except Exception:
        return []
    tags = [t.strip() for t in text.strip().split(",") if t.strip()]
    return tags[:3]


async def secretary_summary(topic: str, history: list, console: Console) -> str:
    """生成秘书总结，返回总结文本（同时显示在终端）。"""
    transcript = build_history(history)
    prompt = (
        "你是一位客观的会议秘书，刚才见证了一场讨论。"
        "请根据下面的完整对话，输出以下内容（不超过 300 字）：\n"
        "1. 核心共识（1-2句）\n"
        "2. 主要分歧（如有，1-2句）\n"
        "3. 对 USER 的具体建议（2-3条）\n"
        "4. 一句话结论\n\n"
        "## 待办清单（USER 可执行）\n"
        "- [ ] 待办 1\n"
        "- [ ] 待办 2\n"
        "- [ ] 待办 3\n\n"
        "（请在上方 ## 待办清单 段落里列出 2-4 条 USER 能立即执行的具体行动，用 checkbox 格式）\n\n"
        f"## 议题\n{topic}\n\n"
        f"## 完整对话\n{transcript}"
    )

    summary = ""
    title = "[bold]秘书总结[/bold]"
    with Live(
        Panel("…", title=title, border_style="yellow", expand=True),
        console=console,
        refresh_per_second=15,
        vertical_overflow="visible",
    ) as live:
        async for event in stream_model(ANALYST_MODEL, prompt):
            if event["type"] == "text":
                summary += event["content"]
            live.update(Panel(Markdown(summary), title=title, border_style="yellow", expand=True))

    return summary


# ── 功能 L：待办追踪命令 ────────────────────────────────────────────────────

def cmd_status(console: Console) -> None:
    """列出所有未完成待办，按议题分组。"""
    chain = load_chain()
    any_pending = False
    for i, entry in enumerate(chain):
        todos = entry.get("todos", [])
        pending = [(j, t) for j, t in enumerate(todos) if t.get("status") == "pending"]
        if not pending:
            continue
        any_pending = True
        console.print(f"\n[bold]{i+1}. {entry['topic']}[/bold]")
        for j, todo in pending:
            age = ""
            try:
                created = datetime.fromisoformat(todo["created_at"])
                days = (datetime.now() - created).days
                if days > 0:
                    age = f" [dim]({days}天前)[/dim]"
            except Exception:
                pass
            console.print(f"  {i+1}.{j+1}  [ ] {todo['text']}{age}")

    if not any_pending:
        console.print("[dim]暂无未完成待办。[/dim]")
    else:
        console.print("\n[dim]用 yt --done <序号> 标记完成（如 yt --done 1.2）[/dim]")


def cmd_done(id_str: str, console: Console) -> None:
    """标记某个待办为完成。id_str 格式：议题序号.待办序号（如 1.2）。"""
    try:
        parts = id_str.strip().split(".")
        if len(parts) != 2:
            raise ValueError("格式错误")
        topic_idx = int(parts[0]) - 1
        todo_idx = int(parts[1]) - 1
    except ValueError:
        console.print("[red]格式错误，请用 议题序号.待办序号（如 yt --done 1.2）[/red]")
        return

    chain = load_chain()
    if topic_idx < 0 or topic_idx >= len(chain):
        console.print(f"[red]议题序号 {topic_idx+1} 不存在（共 {len(chain)} 个）[/red]")
        return

    entry = chain[topic_idx]
    todos = entry.get("todos", [])
    if todo_idx < 0 or todo_idx >= len(todos):
        console.print(f"[red]待办序号 {todo_idx+1} 不存在（共 {len(todos)} 个）[/red]")
        return

    todos[todo_idx]["status"] = "done"
    entry["todos"] = todos
    save_chain(chain)
    console.print(f"[green]✓ 已完成：{todos[todo_idx]['text']}[/green]")


def cmd_search(keyword: str | None, tag: str | None, console: Console) -> None:
    """搜索历史议题，支持关键词和标签过滤。"""
    chain = load_chain()
    if not chain:
        console.print("[dim]暂无历史议题。[/dim]")
        return

    results = []
    for i, entry in enumerate(chain):
        topic = entry.get("topic", "")
        tags = entry.get("tags", [])
        history = entry.get("history", [])

        # 标签过滤
        if tag and tag not in tags:
            continue

        # 关键词过滤
        if keyword:
            kw = keyword.lower()
            in_topic = kw in topic.lower()
            in_history = any(kw in text.lower() for _, text in history)
            if not in_topic and not in_history:
                continue

        results.append((i, entry))

    if not results:
        desc = []
        if keyword:
            desc.append(f'关键词="{keyword}"')
        if tag:
            desc.append(f'标签="{tag}"')
        console.print(f"[dim]未找到匹配的议题（{' '.join(desc)}）。[/dim]")
        return

    console.print(f"\n[bold]找到 {len(results)} 个议题：[/bold]")
    for i, entry in results:
        tags = entry.get("tags", [])
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        turns = len(entry.get("history", []))
        console.print(f"  {i+1}. {entry['topic']}{tag_str} [dim]({turns} 轮)[/dim]")


def check_stale_todos(console: Console) -> None:
    """启动时检查超过 7 天未处理的待办，有则提示。"""
    chain = load_chain()
    stale = []
    now = datetime.now()
    for entry in chain:
        for todo in entry.get("todos", []):
            if todo.get("status") == "pending":
                try:
                    created = datetime.fromisoformat(todo["created_at"])
                    if (now - created).days > 7:
                        stale.append((entry["topic"], todo["text"]))
                except Exception:
                    pass
    if stale:
        console.print(f"\n[yellow]📋 你有 {len(stale)} 项超过 7 天没勾选的待办：[/yellow]")
        for topic, text in stale[:5]:
            console.print(f"  • [{topic[:20]}] {text[:40]}")
        if len(stale) > 5:
            console.print(f"  [dim]… 还有 {len(stale)-5} 项[/dim]")
        console.print("[dim]  yt --status 查看全部，yt --done <id> 标记完成[/dim]\n")


async def run_debate(
    topic: str,
    chain: list,
    console: Console,
    topic_attach: str = "",
    topic_images: list[str] | None = None,
    template_name: str = "free",
    role_a: dict | None = None,
    role_b: dict | None = None,
    resume_history: list | None = None,
    do_research: bool = True,
    research_deep: bool = False,
    do_moderator: bool = True,
):
    topic_images = list(topic_images) if topic_images else []

    # 加载角色
    roles = load_roles()
    if role_a is None:
        role_a = roles[0]  # 默认 zhuge
    if role_b is None:
        role_b = roles[1]  # 默认 sima

    zhuge_char = role_a["body"]
    sima_char = role_b["body"]
    model_a = role_a["model"]
    model_b = role_b["model"]
    name_a = f"{role_a['icon']} {role_a['name']}"
    name_b = f"{role_b['icon']} {role_b['name']}"
    color_a = role_a.get("color", "green")
    color_b = role_b.get("color", "red")

    # rich 颜色映射（只支持 rich 内置颜色名）
    _color_map = {
        "green": "green", "red": "red", "pink": "magenta",
        "gold": "yellow", "blue": "blue", "orange": "orange1",
        "purple": "purple", "white": "white", "cyan": "cyan",
        "yellow": "yellow",
    }
    rc_a = _color_map.get(color_a, "green")
    rc_b = _color_map.get(color_b, "red")

    # ── 功能 I：议题预热 ────────────────────────────────────────────────────
    if do_research and not resume_history:
        max_tools = 5 if research_deep else 3
        console.print(f"\n[dim]正在启动议题预热（最多 {max_tools} 次工具调用）…[/dim]")
        research_text = await run_research(topic, topic_attach, topic_images, console, max_tools=max_tools)
        if research_text:
            topic_attach = topic_attach + "\n\n## 议题预热（调研员收集）\n\n" + research_text

    # resume 模式：加载已有历史作为 prev_ctx
    if resume_history:
        history: list[tuple[str, str]] = list(resume_history)
        prev_ctx = (
            "【续辩上下文 — 以下是之前的讨论记录，请在此基础上继续辩论】\n\n"
            + build_history(resume_history)
            + "\n\n"
        )
        # 下一个发言方 = 上次最后一个 role 的反方
        last_role = resume_history[-1][0] if resume_history else "sima"
        first_is_zhuge = (last_role == "sima")
        turn_offset = len(resume_history)
    else:
        history = []
        prev_ctx = build_chain_context(chain)
        first_is_zhuge = True
        turn_offset = 0

    start = time.time()

    # 用模板包装议题
    final_topic = apply_template(template_name, topic)

    # USER 确认回调，绑定当前 console
    async def confirm_callback(action: str, data: dict) -> bool:
        return await _cli_confirm_callback(action, data, console)

    # ── 功能 K：异步推断标签（fire-and-forget） ──────────────────────────
    tag_task = asyncio.ensure_future(auto_tag(topic))

    for turn_idx in range(MAX_TURNS):
        # resume 模式：第一轮由反方开始
        is_zhuge = ((turn_idx % 2 == 0) == first_is_zhuge)
        round_num = (turn_offset + turn_idx) // 2 + 1
        role = "zhuge" if is_zhuge else "sima"

        if is_zhuge:
            title = f"[bold {rc_a}]{name_a}[/bold {rc_a}] · 第 {round_num} 轮"
            border = rc_a
            prompt = build_zhuge_prompt(zhuge_char, final_topic, history, turn_idx if not resume_history else turn_idx + 1, prev_ctx, topic_attach)
            streamer = stream_model(model_a, prompt, topic_images, confirm_callback)
        else:
            title = f"[bold {rc_b}]{name_b}[/bold {rc_b}] · 第 {round_num} 轮"
            border = rc_b
            prompt = build_sima_prompt(sima_char, final_topic, history, prev_ctx, topic_attach)
            streamer = stream_model(model_b, prompt, topic_images, confirm_callback)

        full_text = ""
        with Live(
            Panel("…", title=title, border_style=border, expand=True),
            console=console,
            refresh_per_second=15,
            vertical_overflow="visible",
        ) as live:
            async for event in streamer:
                if event["type"] == "text":
                    full_text += event["content"]
                elif event["type"] == "tool_result":
                    full_text += f"\n\n> {event['summary']}\n\n"
                live.update(Panel(
                    Markdown(full_text),
                    title=title,
                    border_style=border,
                    expand=True,
                ))

        history.append((role, full_text))

        # ── 功能 J：主持人点评 ──────────────────────────────────────────
        if do_moderator and turn_idx > 0:
            moderator_text = await run_moderator(topic, history, console)
            if moderator_text:
                console.print(f"\n[dim yellow]🎙 {moderator_text}[/dim yellow]\n")

        if STOP_MARKER in full_text:
            break

        if USER_MARKER in full_text:
            console.print("\n[bold yellow]你[/bold yellow] [dim]（AI 邀请你发言）[/dim]", end=" ")
            try:
                user_raw = await asyncio.get_event_loop().run_in_executor(None, input)
                user_raw = user_raw.strip()
            except (KeyboardInterrupt, EOFError):
                user_raw = ""
            if user_raw:
                user_text, user_attach, user_imgs = parse_attachments(user_raw)
                if user_attach:
                    topic_attach += user_attach
                    count = user_attach.count("## 附件文件：") + user_attach.count("## PDF 附件：")
                    console.print(f"[dim]✓ 已加载 {count} 个附件[/dim]")
                if user_imgs:
                    topic_images.extend(user_imgs)
                    console.print(f"[dim]✓ 已加载 {len(user_imgs)} 张图片[/dim]")
                history.append(("user", user_text))
                console.print("[dim]已记录，下轮 AI 会回应你。[/dim]\n")

        if (turn_idx + 1) % UNIT_SIZE == 0 and turn_idx + 1 < MAX_TURNS:
            console.print("\n[bold cyan]━━ 阶段暂停 ━━[/bold cyan]")
            console.print("[dim]① 继续辩  ② 散会出总结  ③ 我插一句话[/dim]")
            console.print("[bold]请选择[/bold] [dim](默认 ①)[/dim]:", end=" ")
            try:
                choice = await asyncio.get_event_loop().run_in_executor(None, input)
                choice = choice.strip()
            except (KeyboardInterrupt, EOFError):
                choice = "2"
            if choice in ("2", "②", "散会"):
                break
            if choice in ("3", "③"):
                console.print("[bold yellow]你的发言[/bold yellow]:", end=" ")
                try:
                    user_raw = await asyncio.get_event_loop().run_in_executor(None, input)
                    user_raw = user_raw.strip()
                except (KeyboardInterrupt, EOFError):
                    user_raw = ""
                if user_raw:
                    user_text, user_attach, _ = parse_attachments(user_raw)
                    if user_attach:
                        topic_attach += user_attach
                        count = user_attach.count("## 附件文件：") + user_attach.count("## PDF 附件：")
                        console.print(f"[dim]✓ 已加载 {count} 个附件[/dim]")
                    history.append(("user", user_text))
                    console.print("[dim]已记录。[/dim]\n")
            console.print()

    # resume 模式：覆盖已有 chain entry，否则追加
    if resume_history:
        # 找到对应的 chain entry（匹配 topic），覆盖 history
        for entry in chain:
            if entry["topic"] == topic:
                entry["history"] = history
                break
        else:
            chain.append({"topic": topic, "history": history, "created_at": datetime.now().isoformat(timespec="seconds")})
    else:
        chain.append({"topic": topic, "history": history, "created_at": datetime.now().isoformat(timespec="seconds")})

    duration = int(time.time() - start)
    console.print(
        f"\n[dim]⏱ 用时 {duration} 秒 · 共 {len(history)} 轮 · 散会[/dim]\n",
        justify="center",
    )

    secretary = await secretary_summary(topic, history, console)

    # 解析待办清单
    todos = _parse_todos_from_secretary(secretary)

    # 功能 F：评分
    scores = None
    console.print("\n[dim]正在评分…[/dim]")
    scores = await rate_debate(topic, history, secretary, console)
    if scores:
        display_scores(scores, console)
    else:
        console.print("[dim]评分跳过（解析失败）[/dim]")

    # 等待标签推断完成（最多 10 秒）
    tags = []
    try:
        tags = await asyncio.wait_for(tag_task, timeout=10.0)
    except (asyncio.TimeoutError, Exception):
        pass

    # 导出决议
    export_path = None
    try:
        export_path = export_debate(topic, history, secretary, duration, role_a, role_b, scores, todos)
        rel = str(export_path).replace(str(HOME), "~")
        console.print(f"\n[bold green]✓ 决议已导出：{rel}[/bold green]")
    except Exception as e:
        console.print(f"\n[dim]导出失败：{e}[/dim]")

    # 持久化（含 scores / tags / todos）
    for entry in reversed(chain):
        if entry["topic"] == topic:
            if scores:
                entry["scores"] = scores
            if tags:
                entry["tags"] = tags
            if todos:
                entry["todos"] = todos
            break
    save_chain(chain)

    # 显示标签
    if tags:
        console.print(f"[dim]标签：{', '.join(tags)}[/dim]")

    # 显示待办汇总
    if todos:
        console.print(f"\n[bold]📋 待办清单（{len(todos)} 项）：[/bold]")
        for t in todos:
            console.print(f"  [ ] {t['text']}")
        console.print("[dim]  yt --status 查看，yt --done <id> 标记完成[/dim]\n")

    # 本机接力功能默认关闭，只有配置命令后才会询问。
    if export_path:
        await ask_local_assistant_handoff(export_path, console)


async def main():
    parser = argparse.ArgumentParser(description="圆桌会议 CLI")
    parser.add_argument(
        "--template", "-t",
        default=None,
        choices=[k for k, _ in TEMPLATE_LIST],
        help="直接指定议题模板，跳过启动选择",
    )
    parser.add_argument(
        "--role-a",
        default=None,
        help="A 角色 short name（如 analyst, investor, user）",
    )
    parser.add_argument(
        "--role-b",
        default=None,
        help="B 角色 short name（如 executor, boss, mentor）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="列出历史议题，选择续辩",
    )
    parser.add_argument(
        "--extract",
        default=None,
        metavar="FILE_OR_URL",
        help="从 PDF/网页自动提取议题",
    )
    # 功能 I
    parser.add_argument(
        "--no-research",
        action="store_true",
        help="关闭议题预热",
    )
    parser.add_argument(
        "--research-deep",
        action="store_true",
        help="深度预热（5 次工具调用，默认 3 次）",
    )
    # 功能 J
    parser.add_argument(
        "--no-moderator",
        action="store_true",
        help="关闭主持人点评",
    )
    # 功能 K：搜索
    parser.add_argument(
        "--search",
        default=None,
        metavar="KEYWORD",
        help="搜索历史议题内容",
    )
    parser.add_argument(
        "--tag",
        default=None,
        metavar="TAG",
        help="按标签筛选历史议题",
    )
    # 功能 L：待办
    parser.add_argument(
        "--status",
        action="store_true",
        help="列出所有未完成待办",
    )
    parser.add_argument(
        "--done",
        default=None,
        metavar="ID",
        help="标记待办完成（格式：议题序号.待办序号，如 1.2）",
    )
    args = parser.parse_args()

    console = Console()

    # ── 纯数据命令（不需要 ping 中转站） ──────────────────────────────────
    if args.status:
        cmd_status(console)
        return

    if args.done:
        cmd_done(args.done, console)
        return

    if args.search is not None or args.tag is not None:
        cmd_search(args.search, args.tag, console)
        return

    # 启动检查：ping 中转站
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as http:
            await http.get(RELAY_BASE_URL.rstrip("/v1").rstrip("/") + "/")
    except Exception:
        console.print(
            f"[bold red]❌ 中转站连不上（{RELAY_BASE_URL.rstrip('/v1')}），请先启动中转站再 yt[/bold red]"
        )
        return

    chain = load_chain()
    roles = load_roles()

    # ── 功能 L：启动时检查 stale 待办 ───────────────────────────────────
    check_stale_todos(console)

    console.print()
    console.rule("[bold]🏛  圆桌会议[/bold]")
    console.print(
        "[dim]分析型辩手 × 执行型辩手 · 输入 q 退出 · 议题中可拖入文件路径[/dim]\n",
        justify="center",
    )

    # ── 功能 B：--resume 模式 ────────────────────────────────────────────
    if args.resume:
        result = await resume_debate_interactive(chain, console)
        if result is None:
            console.print("[dim]散会。[/dim]")
            return
        resume_topic, resume_history = result

        # 选模板
        template_name = args.template or "free"

        # 选角色
        role_a, role_b = _resolve_roles(args, roles, console, skip_interactive=True)

        console.print(f"\n[dim]续辩议题：{resume_topic}[/dim]")
        console.print(f"[dim]已有 {len(resume_history)} 轮记录，接续辩论[/dim]\n")

        try:
            await run_debate(
                resume_topic, chain, console,
                template_name=template_name,
                role_a=role_a, role_b=role_b,
                resume_history=resume_history,
                do_research=False,  # resume 不重新预热
                do_moderator=not args.no_moderator,
            )
        except KeyboardInterrupt:
            console.print("\n[dim]已中断。[/dim]")
        return

    # ── 选择模板 ─────────────────────────────────────────────────────────
    if args.template:
        template_name = args.template
        label = next((l for k, l in TEMPLATE_LIST if k == template_name), template_name)
        console.print(f"[dim]模板：{label}[/dim]\n")
    else:
        template_name = pick_template_interactive(console)
        label = next((l for k, l in TEMPLATE_LIST if k == template_name), template_name)
        console.print(f"[dim]已选：{label}[/dim]\n")

    # ── 功能 D：选择角色 ──────────────────────────────────────────────────
    role_a, role_b = _resolve_roles(args, roles, console, skip_interactive=False)
    console.print(
        f"[dim]角色：{role_a['icon']} {role_a['name']} × {role_b['icon']} {role_b['name']}[/dim]\n"
    )

    while True:
        # ── 功能 G：--extract 模式（一次性） ─────────────────────────────
        if args.extract:
            topic = await pick_extracted_topic(args.extract, console)
            args.extract = None  # 只在第一次用
            if topic is None:
                console.print("[dim]未选择议题，请手动输入。[/dim]")
                try:
                    topic_raw = Prompt.ask("\n[bold]议题[/bold]")
                except (KeyboardInterrupt, EOFError):
                    console.print("\n[dim]散会。[/dim]")
                    break
            else:
                topic_raw = topic
                console.print(f"\n[dim]议题：{topic_raw}[/dim]\n")
        else:
            try:
                topic_raw = Prompt.ask("\n[bold]议题[/bold]")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]散会。[/dim]")
                break

        if topic_raw.strip().lower() in ("q", "quit", "exit", "退出"):
            console.print("[dim]散会。[/dim]")
            break

        if topic_raw.strip().lower() == "/template":
            template_name = pick_template_interactive(console)
            label = next((l for k, l in TEMPLATE_LIST if k == template_name), template_name)
            console.print(f"[dim]已切换：{label}[/dim]\n")
            continue

        if not topic_raw.strip():
            continue

        topic, attach, images = parse_attachments(topic_raw)

        # 统计附件提示
        if attach:
            txt_count = attach.count("## 附件文件：")
            pdf_count = attach.count("## PDF 附件：")
            if txt_count:
                console.print(f"[dim]✓ 已加载 {txt_count} 个文本附件[/dim]")
            if pdf_count:
                page_matches = re.findall(r"## PDF 附件：[^（]*（(\d+) 页）", attach)
                pages_info = f"（{page_matches[0]} 页）" if page_matches else ""
                console.print(f"[dim]✓ 已加载 {pdf_count} 个 PDF{pages_info}[/dim]")
        if images:
            console.print(f"[dim]✓ 已加载 {len(images)} 张图片[/dim]")
        console.print(f"\n[dim]议题：{topic}[/dim]\n")

        try:
            await run_debate(
                topic, chain, console, attach, images, template_name,
                role_a=role_a, role_b=role_b,
                do_research=not args.no_research,
                research_deep=args.research_deep,
                do_moderator=not args.no_moderator,
            )
        except KeyboardInterrupt:
            console.print("\n[dim]已中断，输入新议题继续。[/dim]\n")
            continue


def _resolve_roles(args, roles: list[dict], console: Console, skip_interactive: bool) -> tuple[dict, dict]:
    """根据命令行参数或交互选择，返回 (role_a, role_b)。"""
    role_a = None
    role_b = None

    # 命令行参数优先
    if args.role_a:
        role_a = next((r for r in roles if r["short"] == args.role_a), None)
        if role_a is None:
            console.print(f"[yellow]找不到角色 '{args.role_a}'，使用默认。[/yellow]")
    if args.role_b:
        role_b = next((r for r in roles if r["short"] == args.role_b), None)
        if role_b is None:
            console.print(f"[yellow]找不到角色 '{args.role_b}'，使用默认。[/yellow]")

    # 交互选择（仅在非 skip 模式且未通过命令行指定时）
    if not skip_interactive:
        if role_a is None:
            role_a = pick_role_interactive(console, roles, "A", default_idx=0)
        if role_b is None:
            role_b = pick_role_interactive(console, roles, "B", default_idx=1)
    else:
        if role_a is None:
            role_a = roles[0]
        if role_b is None:
            role_b = roles[1]

    return role_a, role_b


if __name__ == "__main__":
    asyncio.run(main())
