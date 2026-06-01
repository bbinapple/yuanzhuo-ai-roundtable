#!/usr/bin/env python3
import asyncio
import base64
import inspect
import json
import os
import re
import socket
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pypdf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from openai import AsyncOpenAI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

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

app = FastAPI()
sessions: dict[str, dict] = {}
chain: list[dict] = []  # 跨轮上下文链，键: {topic, history}


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
SECRETARY_MODEL = os.getenv("YUANZHUO_SECRETARY_MODEL") or ANALYST_MODEL
LOCAL_ASSISTANT_CMD = os.getenv("YUANZHUO_LOCAL_ASSISTANT_CMD", "").strip()

ATTACH_MAX_BYTES = 50_000
ATTACH_IMG_MAX_BYTES = 5_000_000
PDF_MAX_BYTES = 5_000_000  # base64 上传 PDF 最大 5MB

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

MAX_TOOL_CALLS = 5        # 单轮工具调用上限
CONFIRM_THRESHOLD = _env_int("YUANZHUO_TOOL_CONFIRM_THRESHOLD", 0)  # 默认每次工具调用都问 USER

TEMPLATES_DIR = BASE / "templates"
ROLES_DIR = BASE / "roles"
ROLE_SHORT_ALIASES = {
    "claude": "analyst",
    "codex": "executor",
    "zhuge": "analyst",
    "sima": "executor",
}

TEMPLATE_MAP = {
    "selection":   "选品评估",
    "sidehustle":  "副业方向利弊分析",
    "negotiation": "谈判/沟通脚本",
    "swot":        "SWOT 框架决策",
    "free":        "自由议题",
}


# ── 角色系统 ──────────────────────────────────────────────────────────────────

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


def normalize_role_short(value: str) -> str:
    short = (value or "").strip().lower()
    return ROLE_SHORT_ALIASES.get(short, short)


def find_role(role_pool: list[dict], requested: str, fallback: dict | None = None) -> dict | None:
    wanted = normalize_role_short(requested)
    for role in role_pool:
        if normalize_role_short(role.get("short", "")) == wanted:
            return role
        if normalize_role_short(role.get("file", "")) == wanted:
            return role
    return fallback


def load_roles() -> list[dict]:
    """从 roles/ 目录加载角色列表（跳过 hidden: true 的内部角色）。"""
    roles = []
    order = ["zhuge", "sima", "investor", "user", "boss", "mentor"]
    if ROLES_DIR.exists():
        for name in order:
            path = ROLES_DIR / f"{name}.md"
            if path.exists():
                raw = path.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(raw)
                if meta.get("hidden", "").lower() == "true":
                    continue
                roles.append({
                    "file": name,
                    "name": meta.get("name", name),
                    "short": meta.get("short", name),
                    "color": meta.get("color", "white"),
                    "icon": meta.get("icon", "🤖"),
                    "model": _model_for_role(name, meta),
                    "subtitle": meta.get("subtitle", ""),
                    "tags": meta.get("tags", ""),
                    "body": body,
                })
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


# ── 模板 ─────────────────────────────────────────────────────────────────────

def load_template(name: str) -> str:
    """加载模板，去掉 YAML frontmatter，返回 body。失败返回空字符串。"""
    path = TEMPLATES_DIR / f"{name}.md"
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---"):
        end = raw.find("---", 3)
        if end != -1:
            raw = raw[end + 3:].lstrip("\n")
    return raw


def apply_template(template_name: str, topic: str) -> str:
    """把模板里的 {topic} 替换为实际议题。失败降级用原始议题。"""
    body = load_template(template_name)
    if not body:
        return topic
    return body.replace("{topic}", topic)


# ── PDF 解析 ─────────────────────────────────────────────────────────────────

def _extract_pdf_from_bytes(data: bytes) -> tuple[str, int]:
    """从 bytes 解析 PDF，返回 (text, page_count)。失败抛 Exception。"""
    import io
    reader = pypdf.PdfReader(io.BytesIO(data))
    pages = len(reader.pages)
    text = "\n\n".join(p.extract_text() or "" for p in reader.pages)
    return text, pages


def _extract_pdf_from_path(path: Path) -> tuple[str, int]:
    """从文件路径解析 PDF。"""
    reader = pypdf.PdfReader(str(path))
    pages = len(reader.pages)
    text = "\n\n".join(p.extract_text() or "" for p in reader.pages)
    return text, pages


# ── 导出 ─────────────────────────────────────────────────────────────────────

def _make_slug(topic: str) -> str:
    """生成文件名 slug：只保留中文/英文/数字，空格换 -，取前 30 字符。"""
    chars = []
    for ch in topic:
        if '一' <= ch <= '鿿' or ch.isalnum():
            chars.append(ch)
        elif ch in (' ', '\t'):
            chars.append('-')
    slug = ''.join(chars)[:30].strip('-')
    return slug or "yuanzhuo"


def _parse_todos_from_secretary(secretary_text: str) -> list[dict]:
    """从秘书总结中提取待办清单（## 待办清单 下的 - [ ] 条目）。"""
    todos = []
    in_todo_section = False
    now_iso = datetime.now().isoformat(timespec="seconds")
    for line in secretary_text.splitlines():
        if re.match(r"^#{1,3}\s*待办清单", line):
            in_todo_section = True
            continue
        if in_todo_section:
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


def build_export_md(
    topic: str,
    history: list,
    secretary: str,
    duration_ms: int,
    role_a: dict | None = None,
    role_b: dict | None = None,
    role_c: dict | None = None,
    scores: dict | None = None,
    todos: list | None = None,
) -> str:
    """生成导出 Markdown 字符串。"""
    duration_s = duration_ms // 1000
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    turns = len(history)

    name_a = role_a["name"] if role_a else "分析型辩手"
    name_b = role_b["name"] if role_b else "执行型辩手"
    name_c = role_c["name"] if role_c else ""
    model_a = role_a["model"] if role_a else ANALYST_MODEL
    model_b = role_b["model"] if role_b else EXECUTOR_MODEL
    model_c = role_c["model"] if role_c else ""
    role_desc = f"{name_a}({model_a}) × {name_b}({model_b})"
    if role_c:
        role_desc += f" × {name_c}({model_c})"

    lines = [
        f"# 议题：{topic}",
        "",
        f"*{ts} · 共 {turns} 轮 · 用时 {duration_s} 秒 · {role_desc}*",
        "",
        "## 完整辩论",
        "",
    ]
    for role, text in history:
        name = name_a if role == "zhuge" else (name_b if role == "sima" else (name_c if role == "third" else "USER"))
        lines.append(f"### {name}")
        lines.append("")
        lines.append(text)
        lines.append("")

    lines += [
        "## 秘书总结",
        "",
        secretary,
        "",
    ]

    # 待办清单（checkbox 格式）
    if todos:
        lines += ["## 待办清单", ""]
        for t in todos:
            check = "x" if t.get("status") == "done" else " "
            lines.append(f"- [{check}] {t['text']}")
        lines.append("")

    # 功能 F：评分段落
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

    lines += [
        "---",
        "*由圆桌会议生成 · ~/bin/yuanzhuo*",
    ]
    return "\n".join(lines)


# ── 持久化 ───────────────────────────────────────────────────────────────────

def load_chain() -> list:
    """从 ~/.yuanzhuo/history.json 读取历史链，失败返回空列表。"""
    try:
        return json.loads(HISTORY_FILE.read_text())
    except Exception:
        return []


def save_chain(c: list) -> None:
    """持久化 chain，只保留最近 HISTORY_MAX 个议题。图片 base64 不存储。"""
    trimmed = c[-HISTORY_MAX:]
    data = []
    for e in trimmed:
        entry = {
            "topic": e["topic"],
            "history": [[role, text] for role, text in e["history"]],
        }
        for field in ("session_id", "scores", "tags", "created_at", "todos", "status", "updated_at"):
            if field in e:
                entry[field] = e[field]
        data.append(entry)
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def find_chain_entry(session_id: str = "", topic: str = "") -> dict | None:
    for entry in reversed(chain):
        if session_id and entry.get("session_id") == session_id:
            return entry
        if topic and entry.get("topic") == topic:
            return entry
    return None


def upsert_chain_entry(session_id: str, topic: str, **fields) -> dict:
    entry = find_chain_entry(session_id=session_id) or find_chain_entry(topic=topic)
    now = datetime.now().isoformat(timespec="seconds")
    if entry is None:
        entry = {
            "session_id": session_id,
            "topic": topic,
            "history": [],
            "created_at": now,
            "status": "running",
        }
        chain.append(entry)
    else:
        entry.setdefault("session_id", session_id)
        entry.setdefault("created_at", now)
    entry["updated_at"] = now
    for key, value in fields.items():
        if value is not None:
            entry[key] = value
    save_chain(chain)
    return entry


# ── 启动时加载历史 ────────────────────────────────────────────────────────────

chain = load_chain()


# ── Pydantic 模型 ─────────────────────────────────────────────────────────────

class PdfFile(BaseModel):
    name: str
    data: str  # base64-encoded PDF bytes


class CustomRole(BaseModel):
    name: str
    short: str
    model: str
    body: str
    api_key: str = ""
    base_url: str = ""
    reasoning: str = ""
    icon: str = "🧩"
    color: str = "purple"
    subtitle: str = "自定义"
    tags: str = ""


class ModelsRequest(BaseModel):
    api_key: str = ""
    base_url: str = ""


class ApiSettings(BaseModel):
    api_key: str = ""
    base_url: str = ""
    analyst_model: str = ""
    executor_model: str = ""
    secretary_model: str = ""


class RoundRequest(BaseModel):
    topic: str
    attach: str = ""
    image_urls: list[str] = []
    template: str = "free"
    pdf_files: list[PdfFile] = []
    role_a: str = "analyst"  # short name
    role_b: str = "executor" # short name
    role_c: str = ""         # optional third debater short name
    role_secretary: str = "" # optional secretary role short name
    custom_roles: list[CustomRole] = []
    api_settings: ApiSettings = ApiSettings()
    resume_session_id: str = ""  # 功能 B：续辩
    research: str = "on"    # "on" | "off" | "deep"  (功能 I)
    moderator: bool = True   # 功能 J
    reasoning: str = "high"  # legacy debate reasoning level


class RespondPayload(BaseModel):
    kind: str  # "insert" | "stage" | "tool_confirm"
    value: str = ""
    text: str = ""  # 仅 kind=stage 且 value=insert 时用


def normalize_custom_roles(custom_roles: list[CustomRole]) -> list[dict]:
    """把前端本地自定义角色转成内部 role dict。"""
    roles = []
    seen = set()
    for idx, role in enumerate(custom_roles[:20], start=1):
        name = role.name.strip()[:40] or f"自定义角色 {idx}"
        short = role.short.strip().lower()
        short = re.sub(r"[^a-z0-9_-]+", "-", short).strip("-_")[:50]
        if not short:
            short = f"custom-{idx}"
        if not short.startswith("custom-"):
            short = f"custom-{short}"
        base = short
        n = 2
        while short in seen:
            short = f"{base}-{n}"
            n += 1
        seen.add(short)

        body = role.body.strip()
        model = role.model.strip()
        if not body or not model:
            continue
        base_url = role.base_url.strip().rstrip("/")
        api_key = role.api_key.strip()
        reasoning = role.reasoning.strip().lower()
        if reasoning not in {"xhigh", "high", "medium", "low"}:
            reasoning = ""
        roles.append({
            "file": short,
            "name": name,
            "short": short,
            "color": role.color.strip()[:20] or "purple",
            "icon": role.icon.strip()[:4] or "🧩",
            "model": model[:80],
            "api_key": api_key,
            "base_url": base_url[:200],
            "reasoning": reasoning,
            "subtitle": role.subtitle.strip()[:40] or "自定义",
            "tags": role.tags.strip()[:120],
            "body": body[:20000],
            "custom": True,
        })
    return roles


def apply_user_api_settings(role: dict | None, settings: ApiSettings | None, slot: str = "") -> dict | None:
    """Apply browser-provided API settings to a role for this session only."""
    if not role:
        return None
    updated = dict(role)
    settings = settings or ApiSettings()
    base_url = settings.base_url.strip().rstrip("/")
    api_key = settings.api_key.strip()
    analyst_model = settings.analyst_model.strip()
    executor_model = settings.executor_model.strip()
    secretary_model = settings.secretary_model.strip()
    if base_url and not updated.get("base_url"):
        updated["base_url"] = base_url[:200]
    if api_key and not updated.get("api_key"):
        updated["api_key"] = api_key
    short = normalize_role_short(updated.get("short", ""))
    if secretary_model and slot in {"secretary", "research", "moderator", "tag", "score"}:
        updated["model"] = secretary_model[:80]
    elif analyst_model and (slot in {"a", "secretary", "research", "moderator", "tag", "score"} or short in {"analyst", "researcher", "moderator"}):
        updated["model"] = analyst_model[:80]
    if executor_model and (slot == "b" or short == "executor"):
        updated["model"] = executor_model[:80]
    return updated


def analyst_model_from_settings(settings: ApiSettings | None) -> str:
    settings = settings or ApiSettings()
    return (settings.analyst_model or ANALYST_MODEL).strip()


def secretary_model_from_settings(settings: ApiSettings | None) -> str:
    settings = settings or ApiSettings()
    return (settings.secretary_model or settings.analyst_model or SECRETARY_MODEL).strip()


class ExtractRequest(BaseModel):
    source: str  # URL 或本地文件路径
    api_settings: ApiSettings = ApiSettings()


class LaunchAssistantRequest(BaseModel):
    session_id: str


class TodoUpdatePayload(BaseModel):
    status: str  # "pending" | "done"


async def _fetch_model_ids(base_url: str, api_key: str) -> list[str]:
    client = AsyncOpenAI(
        api_key=api_key or LOCAL_API_KEY_PLACEHOLDER,
        base_url=base_url.rstrip("/"),
    )
    models = await client.models.list()
    return sorted({m.id for m in models.data if getattr(m, "id", "")})


# ── 路由 ──────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(BASE / "public" / "index.html")


@app.get("/dual")
async def dual():
    return FileResponse(BASE / "public" / "dual.html")


@app.get("/simple")
async def simple():
    # /simple 保留为兼容入口，实际复用统一主应用并默认进入简易模式。
    return FileResponse(BASE / "public" / "index.html")


@app.get("/panel.js")
async def panel_js():
    return FileResponse(BASE / "public" / "panel.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/roles")
async def get_roles():
    """返回可用角色列表（不含 body）。"""
    roles = load_roles()
    return [
        {
            "file": r["file"],
            "name": r["name"],
            "short": r["short"],
            "color": r["color"],
            "icon": r["icon"],
            "model": r["model"],
            "subtitle": r.get("subtitle", ""),
            "tags": r.get("tags", ""),
        }
        for r in roles
    ]


@app.get("/api/role/{short}")
async def get_role_detail(short: str):
    """返回单个角色的完整定义（含 body / system prompt）。"""
    roles = load_roles()
    role = find_role(roles, short)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return {
        "file": role["file"],
        "name": role["name"],
        "short": role["short"],
        "icon": role["icon"],
        "model": role["model"],
        "subtitle": role.get("subtitle", ""),
        "body": role["body"],
    }


@app.get("/api/health")
async def health():
    """返回基础配置状态；公网部署不使用服务端 API Key 拉取模型列表。"""
    default_models = {
        "analyst": ANALYST_MODEL,
        "executor": EXECUTOR_MODEL,
        "secretary": SECRETARY_MODEL,
    }
    available_models = []
    models_error = "请用户在设置里输入自己的 API Key 后再拉取模型"
    missing = []
    return {
        "relay_base_url": RELAY_BASE_URL,
        "available_models": available_models,
        "default_models": default_models,
        "missing_default_models": missing,
        "models_error": models_error,
    }


@app.post("/api/models")
async def list_models(req: ModelsRequest):
    """列出 OpenAI-compatible endpoint 的模型；必须使用用户提供的 API Key。"""
    base_url = (req.base_url.strip().rstrip("/") or RELAY_BASE_URL)
    api_key = req.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="请先输入你自己的 API Key")
    try:
        ids = await _fetch_model_ids(base_url, api_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"models": ids}


@app.post("/api/regenerate_last/{session_id}")
async def regenerate_last(session_id: str):
    """B1：重新生成最后一轮 AI 发言。返回新的 session_id。"""
    h = recent_histories.get(session_id)
    if not h:
        raise HTTPException(status_code=404, detail="Session expired or not found (recent_histories has a short TTL)")
    history = h["history"]
    if not history:
        raise HTTPException(status_code=400, detail="Empty history")
    # 找最后一条 AI 发言（忽略末尾的 user 插话）
    last_ai_idx = len(history) - 1
    while last_ai_idx >= 0 and history[last_ai_idx][0] not in ("zhuge", "sima", "third"):
        last_ai_idx -= 1
    if last_ai_idx < 0:
        raise HTTPException(status_code=400, detail="No AI turns found in history")
    truncated = history[:last_ai_idx]  # 去掉最后一条 AI 发言
    new_sid = uuid.uuid4().hex[:12]
    sessions[new_sid] = {
        "topic": h["topic"],
        "final_topic": h["topic"],
        "attach": h.get("attach", ""),
        "image_urls": h.get("image_urls", []),
        "user_event": None,
        "user_answer": None,
        "pdf_names": [],
        "role_a": h["role_a"],
        "role_b": h["role_b"],
        "role_c": h.get("role_c"),
        "resume_history": truncated if truncated else None,
        "research": "off",
        "moderator": False,
        "reasoning": h.get("reasoning", "high"),
        "regenerate_only": True,
    }
    return {"session_id": new_sid}


@app.post("/api/round")
async def create_round(req: RoundRequest):
    session_id = uuid.uuid4().hex
    if not req.api_settings.api_key.strip():
        raise HTTPException(status_code=400, detail="请先在设置里输入你自己的 API Key")

    # 加载角色。自定义角色由前端本地保存，开局时随请求带入。
    roles = load_roles()
    role_pool = roles + normalize_custom_roles(req.custom_roles)
    role_a = find_role(role_pool, req.role_a, role_pool[0])
    role_b = find_role(role_pool, req.role_b, role_pool[1] if len(role_pool) > 1 else role_pool[0])
    role_c = find_role(role_pool, req.role_c) if req.role_c else None
    role_secretary = find_role(role_pool, req.role_secretary) if req.role_secretary else None
    role_a = apply_user_api_settings(role_a, req.api_settings, "a")
    role_b = apply_user_api_settings(role_b, req.api_settings, "b")
    role_c = apply_user_api_settings(role_c, req.api_settings, "c")
    role_secretary = apply_user_api_settings(role_secretary, req.api_settings, "secretary")

    # 处理 PDF 附件
    pdf_attach = ""
    pdf_names: list[str] = []
    for pf in req.pdf_files:
        try:
            raw_bytes = base64.b64decode(pf.data)
            if len(raw_bytes) > PDF_MAX_BYTES:
                pdf_attach += f"\n\n[PDF 跳过：{pf.name}（超过 5MB）]"
                continue
            text, pages = _extract_pdf_from_bytes(raw_bytes)
            if len(text) > ATTACH_MAX_BYTES:
                text = text[:ATTACH_MAX_BYTES] + "\n\n... (内容已截断)"
            pdf_attach += f"\n\n## PDF 附件：{pf.name}（{pages} 页）\n```\n{text}\n```"
            pdf_names.append(f"{pf.name}（{pages}页）")
        except Exception as e:
            pdf_attach += f"\n\n[PDF读取失败：{pf.name}（{e}）]"

    full_attach = req.attach + pdf_attach
    final_topic = apply_template(req.template, req.topic)

    # 功能 B：resume 模式 — 加载已有 history
    resume_history = None
    chain_session_id = session_id
    if req.resume_session_id:
        for entry in chain:
            if entry.get("session_id") == req.resume_session_id or entry.get("topic") == req.topic:
                resume_history = list(entry.get("history", []))
                chain_session_id = entry.get("session_id") or session_id
                entry["session_id"] = chain_session_id
                entry["status"] = "running"
                entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
                save_chain(chain)
                break

    if resume_history is None:
        upsert_chain_entry(
            session_id=session_id,
            topic=req.topic,
            history=[],
            status="running",
            tags=[],
            todos=[],
        )

    sessions[session_id] = {
        "topic": req.topic,
        "final_topic": final_topic,
        "attach": full_attach,
        "image_urls": req.image_urls,
        "user_event": None,
        "user_answer": None,
        "pdf_names": pdf_names,
        "role_a": role_a,
        "role_b": role_b,
        "role_c": role_c,
        "role_secretary": role_secretary,
        "api_settings": req.api_settings,
        "chain_session_id": chain_session_id,
        "resume_history": resume_history,
        "research": req.research,   # "on" | "off" | "deep"
        "moderator": req.moderator,
        "reasoning": req.reasoning,  # "low" | "medium" | "high"
    }
    return {"session_id": session_id, "pdf_names": pdf_names}


@app.get("/api/stream/{session_id}")
async def stream_round(session_id: str):
    sess = sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return EventSourceResponse(generate(session_id))


@app.post("/api/respond/{session_id}")
async def respond(session_id: str, payload: RespondPayload):
    sess = sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    sess["user_answer"] = {"kind": payload.kind, "value": payload.value, "text": payload.text}
    ev = sess.get("user_event")
    if ev:
        ev.set()
    return {"ok": True}


@app.delete("/api/history/{topic_idx}")
async def delete_history(topic_idx: int):
    if topic_idx < 0 or topic_idx >= len(chain):
        raise HTTPException(status_code=404, detail="Topic not found")
    chain.pop(topic_idx)
    save_chain(chain)
    return {"ok": True}


@app.get("/api/history")
async def get_history():
    """返回 chain 给前端侧栏展示。"""
    result = []
    for entry in chain:
        todos = entry.get("todos", [])
        total_todos = len(todos)
        done_todos = sum(1 for t in todos if t.get("status") == "done")
        result.append({
            "session_id": entry.get("session_id", ""),
            "topic": entry["topic"],
            "history": [[role, text] for role, text in entry["history"]],
            "scores": entry.get("scores"),
            "tags": entry.get("tags", []),
            "created_at": entry.get("created_at", ""),
            "updated_at": entry.get("updated_at", ""),
            "status": entry.get("status", "completed" if entry.get("history") else "running"),
            "todos": todos,
            "todo_total": total_todos,
            "todo_done": done_todos,
        })
    return result


@app.get("/api/export/{session_id}")
async def export_session(session_id: str):
    """返回该 session 的导出 Markdown 内容，供前端触发下载。"""
    data = completed_sessions.get(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Export not ready or session not found")
    md = data["export_md"]
    topic = data["topic"]
    slug = _make_slug(topic)
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    filename = f"yuanzhuo-{slug}-{ts}.md"
    encoded_filename = quote(filename)
    return PlainTextResponse(
        content=md,
        headers={
            "Content-Disposition": (
                f'attachment; filename="yuanzhuo-export-{ts}.md"; '
                f"filename*=UTF-8''{encoded_filename}"
            )
        },
        media_type="text/markdown; charset=utf-8",
    )


# 本机接力功能默认关闭；公网部署不要启用。
@app.post("/api/launch_local_assistant/{session_id}")
async def launch_local_assistant(session_id: str):
    """在服务器端用 osascript 启动本机助手命令。"""
    if not LOCAL_ASSISTANT_CMD:
        raise HTTPException(status_code=404, detail="本机接力功能未启用")
    data = completed_sessions.get(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found or export not ready")
    md_path = data.get("export_path", "")
    if not md_path:
        raise HTTPException(status_code=400, detail="Export path not available")
    try:
        cmd = f"{LOCAL_ASSISTANT_CMD} '请读 {md_path} 的待办清单并按顺序协助我执行'"
        osa_cmd = cmd.replace("'", "'\\''")
        osa = f'tell application "Terminal" to do script "{osa_cmd}"'
        subprocess.Popen(["osascript", "-e", osa])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e), "cmd": cmd}


# 功能 G：Web 版抽议题
@app.post("/api/extract_topics")
async def extract_topics(req: ExtractRequest):
    """从 URL 或本地文件路径提取议题列表。"""
    if not req.api_settings.api_key.strip():
        raise HTTPException(status_code=400, detail="请先在设置里输入你自己的 API Key")
    source = req.source.strip()
    content = ""

    if source.startswith("http://") or source.startswith("https://"):
        try:
            from tools import web_fetch
            content = await web_fetch(source)
            if content.startswith("❌"):
                raise HTTPException(status_code=400, detail=content)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"抓取失败：{e}")
    else:
        try:
            p = Path(source).expanduser().resolve()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"路径解析失败：{e}")
        if not p.is_relative_to(HOME):
            raise HTTPException(status_code=403, detail="本地路径只允许读取用户 home 目录下的文件")
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在：{p}")
        if not p.is_file():
            raise HTTPException(status_code=400, detail=f"路径不是文件：{p}")
        ext = p.suffix.lower()
        if ext == ".pdf":
            try:
                content, pages = _extract_pdf_from_path(p)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"PDF 解析失败：{e}")
        else:
            try:
                content = p.read_text(errors="ignore")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"文件读取失败：{e}")

    if len(content) > 8000:
        content = content[:8000] + "\n\n... (内容已截断)"

    if not content.strip():
        raise HTTPException(status_code=400, detail="内容为空，无法提取议题")

    prompt = (
        "下面是一份资料。请抽取 3-5 个最值得辩论的议题（核心决策点/有争议的判断/可深挖的角度）。"
        "每个议题一行，格式：「编号. 议题描述（一句话）」。\n\n"
        f"资料：\n{content}"
    )

    client = AsyncOpenAI(
        api_key=req.api_settings.api_key.strip() or LOCAL_API_KEY_PLACEHOLDER,
        base_url=(req.api_settings.base_url.strip().rstrip("/") or RELAY_BASE_URL),
    )
    full = ""
    try:
        stream = await client.chat.completions.create(
            model=analyst_model_from_settings(req.api_settings),
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                full += delta.content
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"提取失败：{e}")

    topics = []
    for line in full.splitlines():
        line = line.strip()
        m = re.match(r'^[「\[]?(\d+)[.、。\]」]\s*(.+)', line)
        if m:
            topics.append(m.group(2).strip())

    if not topics:
        raise HTTPException(status_code=500, detail="未能解析出议题，请手动输入")

    return {"topics": topics}


# 功能 L：待办状态更新
@app.post("/api/todos/{topic_idx}/{todo_idx}")
async def update_todo(topic_idx: int, todo_idx: int, payload: TodoUpdatePayload):
    """更新某议题某待办的状态。"""
    if topic_idx < 0 or topic_idx >= len(chain):
        raise HTTPException(status_code=404, detail="议题不存在")
    entry = chain[topic_idx]
    todos = entry.get("todos", [])
    if todo_idx < 0 or todo_idx >= len(todos):
        raise HTTPException(status_code=404, detail="待办不存在")
    todos[todo_idx]["status"] = payload.status
    entry["todos"] = todos
    save_chain(chain)
    return {"ok": True, "status": payload.status}


# 存放已完成的 session 导出数据
completed_sessions: dict[str, dict] = {}

# B1：存放最近完成的完整 history（用于「重新生成最后一轮」）
recent_histories: dict[str, dict] = {}


# ── 提示构建 ──────────────────────────────────────────────────────────────────

def build_history(history: list) -> str:
    parts = []
    for role, text in history:
        if role == "zhuge":
            name = "A 角色"
        elif role == "sima":
            name = "B 角色"
        elif role == "third":
            name = "C 角色"
        else:
            name = "USER"
        parts.append(f"**{name}**：\n{text}")
    return "\n\n---\n\n".join(parts)


def build_chain_context(c: list) -> str:
    usable = [entry for entry in c if entry.get("history")]
    if not usable:
        return ""
    parts = ["【历史讨论背景 — 本轮议题与此相关，可参考衔接】\n"]
    for entry in usable[-2:]:
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


def build_third_prompt(char: str, topic: str, history: list, prev_ctx: str = "", attach: str = "") -> str:
    ctx = build_history(history)
    rule = (
        "\n\n【第三辩手规则】不超过 160 字。你不是裁判，要明确提出第三视角："
        "先指出 A/B 双方都忽略或低估的一点，再给出你的修正判断。"
        "不要重复前两位的话，不写 [散会]。如果必须问 USER 才能判断，在末尾加 [ASK_USER]。\n\n"
    )
    return (
        TOOL_HINT + char + prev_ctx + rule +
        f"# 当前议题\n\n{topic}{attach}\n\n"
        f"# 本轮讨论\n\n{ctx}\n\n"
        "请基于前两位发言补充第三视角。"
    )


def _build_messages(prompt: str, images: list[str] | None):
    if not images:
        return [{"role": "user", "content": prompt}]
    content = [{"type": "text", "text": prompt}]
    for url in images:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return [{"role": "user", "content": content}]


# ── 工具流式生成 ──────────────────────────────────────────────────────────────

async def stream_with_tools(
    model: str,
    prompt: str,
    images: list[str] | None,
    confirm_callback,
    max_tools: int | None = None,
    reasoning: str = "high",
    api_key: str | None = None,
    base_url: str | None = None,
):
    """支持工具调用的流式生成器。max_tools 覆盖默认上限（用于受限场景）。"""
    endpoint = (base_url or RELAY_BASE_URL).rstrip("/")
    client = AsyncOpenAI(api_key=api_key or LOCAL_API_KEY_PLACEHOLDER, base_url=endpoint)
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
                extra_body={"reasoning_effort": reasoning},
            )
        except Exception:
            yield {"type": "text", "content": f"\n\n⚠️ 连不上模型接口（{endpoint}），请确认 API Key、Base URL 和模型是否可用。\n"}
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

        if not accumulated_tool_calls:
            return

        accumulated_tool_calls = [tc for tc in accumulated_tool_calls if tc["name"] and tc["id"]]
        if not accumulated_tool_calls:
            return

        if tool_call_count + len(accumulated_tool_calls) > effective_max:
            yield {"type": "text", "content": "\n\n[⚠️ 工具调用已达本轮上限，用现有信息继续辩论]\n"}
            messages.append({"role": "assistant", "content": accumulated_text or "（思考中）"})
            messages.append({
                "role": "user",
                "content": f"工具调用已达本轮 {effective_max} 次上限，请基于现有信息直接给出你的发言，不要再调用工具。",
            })
            continue

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

        for tc in accumulated_tool_calls:
            tool_call_count += 1

            yield {"type": "tool_call", "name": tc["name"], "args": tc["args"]}

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


async def _deny_callback(action: str, data: dict) -> bool:
    return False


# ── 功能 I：议题预热生成器 ────────────────────────────────────────────────────

async def research_gen(topic: str, attach: str, image_urls: list[str], max_tools: int = 3, api_settings: ApiSettings | None = None):
    """预热调研员，yield SSE-ready dict events。"""
    role = load_role("researcher")
    if not role:
        return
    role = apply_user_api_settings(role, api_settings, "research")

    role_prompt = role["body"].replace("{max_tools}", str(max_tools))
    prompt = f"{role_prompt}\n\n# 议题\n{topic}{attach}"
    images = image_urls if image_urls else None

    async for event in stream_with_tools(
        role["model"],
        prompt,
        images,
        _deny_callback,
        max_tools=max_tools,
        api_key=role.get("api_key"),
        base_url=role.get("base_url"),
    ):
        if event["type"] == "text":
            yield {"type": "research_text", "content": event["content"]}
        elif event["type"] == "tool_result":
            yield {"type": "research_tool", "summary": event["summary"]}


# ── 功能 J：主持人点评 ────────────────────────────────────────────────────────

async def moderator_comment(topic: str, history: list, api_settings: ApiSettings | None = None) -> str:
    """调主持人出点评（≤50字），失败返回空字符串。"""
    role = load_role("moderator")
    if not role:
        return ""
    role = apply_user_api_settings(role, api_settings, "moderator")

    def build_hist(h: list) -> str:
        parts = []
        for r, t in h:
            name = "A角色" if r == "zhuge" else ("B角色" if r == "sima" else ("C角色" if r == "third" else "USER"))
            parts.append(f"**{name}**：\n{t}")
        return "\n\n---\n\n".join(parts)

    transcript = build_hist(history[-4:])
    prompt = (
        f"{role['body']}\n\n"
        f"# 议题\n{topic}\n\n"
        f"# 最近发言\n{transcript}\n\n"
        "请点评（≤50 字）："
    )

    client = AsyncOpenAI(
        api_key=role.get("api_key") or LOCAL_API_KEY_PLACEHOLDER,
        base_url=(role.get("base_url") or RELAY_BASE_URL),
    )
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
    return text.strip()[:100]


# ── 功能 K：自动推断标签 ─────────────────────────────────────────────────────

async def auto_tag(topic: str, api_settings: ApiSettings | None = None) -> list[str]:
    """异步推断标签，失败静默返回空列表。"""
    prompt = (
        f"给以下议题打 1-3 个标签（从这些类别选）：选品/副业/家庭/技术/财务/职场/AI跨境/学习/其他\n"
        f"议题：{topic}\n"
        f"只输出标签（用逗号分隔），不要解释。"
    )
    settings = api_settings or ApiSettings()
    client = AsyncOpenAI(
        api_key=settings.api_key.strip() or LOCAL_API_KEY_PLACEHOLDER,
        base_url=(settings.base_url.strip().rstrip("/") or RELAY_BASE_URL),
    )
    text = ""
    try:
        stream = await client.chat.completions.create(
            model=analyst_model_from_settings(settings),
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


# ── 功能 F：评分 ──────────────────────────────────────────────────────────────

async def rate_debate_gen(topic: str, history: list, summary: str, api_settings: ApiSettings | None = None):
    """调用默认评估模型评分，返回 scores dict 或 None。"""
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
    settings = api_settings or ApiSettings()
    client = AsyncOpenAI(
        api_key=settings.api_key.strip() or LOCAL_API_KEY_PLACEHOLDER,
        base_url=(settings.base_url.strip().rstrip("/") or RELAY_BASE_URL),
    )
    full = ""
    try:
        stream = await client.chat.completions.create(
            model=analyst_model_from_settings(settings),
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                full += delta.content
    except Exception:
        return None

    try:
        match = re.search(r'\{[^{}]*\}', full)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return None


# ── 秘书总结 ──────────────────────────────────────────────────────────────────

async def secretary_summary_gen(topic: str, history: list, secretary_role: dict | None = None, api_settings: ApiSettings | None = None):
    """yield SSE-ready dict events for the secretary summary."""
    transcript = build_history(history)
    settings = api_settings or ApiSettings()
    secretary_model = secretary_role["model"] if secretary_role else secretary_model_from_settings(settings)
    secretary_api_key = secretary_role.get("api_key") if secretary_role else settings.api_key.strip()
    secretary_base_url = secretary_role.get("base_url") if secretary_role else settings.base_url.strip().rstrip("/")
    secretary_system = (
        secretary_role.get("body")
        if secretary_role and secretary_role.get("body")
        else "你是一位客观的会议秘书，刚才见证了一场讨论。"
    )
    prompt = (
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
    client = AsyncOpenAI(
        api_key=secretary_api_key or LOCAL_API_KEY_PLACEHOLDER,
        base_url=(secretary_base_url or RELAY_BASE_URL),
    )
    try:
        stream = await client.chat.completions.create(
            model=secretary_model,
            messages=[
                {"role": "system", "content": secretary_system},
                {"role": "user", "content": prompt},
            ],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield {"type": "secretary", "content": delta.content}
    except Exception as e:
        yield {"type": "secretary", "content": f"\n\n⚠️ 秘书总结失败：{e}\n"}


# ── 主生成器 ──────────────────────────────────────────────────────────────────

async def generate(session_id: str):
    sess = sessions.get(session_id)
    if not sess:
        yield {"event": "error", "data": json.dumps({"message": "Session not found"})}
        return

    topic = sess["topic"]
    final_topic = sess["final_topic"]
    attach = sess.get("attach", "")
    image_urls = list(sess.get("image_urls", []))
    role_a = sess.get("role_a") or load_roles()[0]
    role_b = sess.get("role_b") or load_roles()[1]
    role_c = sess.get("role_c")
    role_secretary = sess.get("role_secretary")
    api_settings = sess.get("api_settings") or ApiSettings()
    chain_session_id = sess.get("chain_session_id") or session_id
    resume_history = sess.get("resume_history")
    research_mode = sess.get("research", "on")   # "on" | "off" | "deep"
    do_moderator = sess.get("moderator", True)

    zhuge_char = role_a["body"]
    sima_char = role_b["body"]
    third_char = role_c["body"] if role_c else ""
    model_a = role_a["model"]
    model_b = role_b["model"]
    model_c = role_c["model"] if role_c else ""
    debaters = [
        {"key": "zhuge", "slot": "a", "role": role_a, "char": zhuge_char, "model": model_a},
        {"key": "sima", "slot": "b", "role": role_b, "char": sima_char, "model": model_b},
    ]
    if role_c:
        debaters.append({"key": "third", "slot": "c", "role": role_c, "char": third_char, "model": model_c})

    # ── 功能 I：议题预热 ────────────────────────────────────────────────────
    research_text = ""
    if research_mode != "off" and not resume_history:
        max_tools = 5 if research_mode == "deep" else 3
        yield {
            "event": "research_start",
            "data": json.dumps({"max_tools": max_tools}, ensure_ascii=False),
        }
        async for rev in research_gen(topic, attach, image_urls, max_tools=max_tools, api_settings=api_settings):
            if rev["type"] == "research_text":
                research_text += rev["content"]
                yield {
                    "event": "research",
                    "data": json.dumps({"chunk": rev["content"]}, ensure_ascii=False),
                }
            elif rev["type"] == "research_tool":
                yield {
                    "event": "research_tool",
                    "data": json.dumps({"summary": rev["summary"]}, ensure_ascii=False),
                }
        yield {
            "event": "research_done",
            "data": json.dumps({}, ensure_ascii=False),
        }
        if research_text.strip():
            attach = attach + "\n\n## 议题预热（调研员收集）\n\n" + research_text.strip()

    # resume 模式
    if resume_history:
        history: list = list(resume_history)
        prev_ctx = (
            "【续辩上下文 — 以下是之前的讨论记录，请在此基础上继续辩论】\n\n"
            + build_history(resume_history)
            + "\n\n"
        )
        last_role = next((r for r, _ in reversed(resume_history) if r in {d["key"] for d in debaters}), "sima")
        start_idx = (next((i for i, d in enumerate(debaters) if d["key"] == last_role), 1) + 1) % len(debaters)
        turn_offset = len(resume_history)
    else:
        history = []
        prev_ctx = build_chain_context(chain)
        start_idx = 0
        turn_offset = 0

    start = time.time()

    # 通知前端角色信息
    yield {
        "event": "roles",
        "data": json.dumps({
            "role_a": {"name": role_a["name"], "icon": role_a["icon"], "color": role_a["color"], "model": model_a},
            "role_b": {"name": role_b["name"], "icon": role_b["icon"], "color": role_b["color"], "model": model_b},
            "role_c": ({"name": role_c["name"], "icon": role_c["icon"], "color": role_c["color"], "model": model_c} if role_c else None),
        }, ensure_ascii=False),
    }

    # ── 功能 K：异步推断标签（fire-and-forget） ──────────────────────────
    tag_task = asyncio.ensure_future(auto_tag(topic, api_settings))

    async def confirm_callback(action: str, data: dict) -> bool:
        if action != "confirm":
            return False
        sess["_pending_sse"] = {
            "event": "ask_user",
            "data": json.dumps({
                "kind": "tool_confirm",
                "name": data["name"],
                "args": data["args"],
                "count": data["count"],
            }, ensure_ascii=False),
        }
        event = asyncio.Event()
        sess["user_event"] = event
        sess["user_answer"] = None
        try:
            await asyncio.wait_for(event.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            return False
        answer = sess.get("user_answer") or {}
        return answer.get("value", "").lower() not in ("n", "no", "拒绝", "deny")

    for turn_idx in range(MAX_TURNS):
        debater = debaters[(start_idx + turn_idx) % len(debaters)]
        round_num = (turn_offset + turn_idx) // len(debaters) + 1
        role = debater["key"]

        yield {
            "event": "turn_open",
            "data": json.dumps({"role": role, "round": round_num}, ensure_ascii=False),
        }

        full_text = ""

        if role == "zhuge":
            prompt = build_zhuge_prompt(debater["char"], final_topic, history, turn_idx if not resume_history else turn_idx + 1, prev_ctx, attach)
            model_gen = stream_with_tools(
                debater["model"],
                prompt,
                image_urls,
                confirm_callback,
                reasoning=debater["role"].get("reasoning") or sess.get("reasoning", "high"),
                api_key=debater["role"].get("api_key"),
                base_url=debater["role"].get("base_url"),
            )
            evt_name = "zhuge"
        elif role == "sima":
            prompt = build_sima_prompt(debater["char"], final_topic, history, prev_ctx, attach)
            model_gen = stream_with_tools(
                debater["model"],
                prompt,
                image_urls,
                confirm_callback,
                reasoning=debater["role"].get("reasoning") or sess.get("reasoning", "high"),
                api_key=debater["role"].get("api_key"),
                base_url=debater["role"].get("base_url"),
            )
            evt_name = "sima"
        else:
            prompt = build_third_prompt(debater["char"], final_topic, history, prev_ctx, attach)
            model_gen = stream_with_tools(
                debater["model"],
                prompt,
                image_urls,
                confirm_callback,
                reasoning=debater["role"].get("reasoning") or sess.get("reasoning", "high"),
                api_key=debater["role"].get("api_key"),
                base_url=debater["role"].get("base_url"),
            )
            evt_name = "third"

        async for event in model_gen:
            pending = sess.pop("_pending_sse", None)
            if pending:
                yield pending

            if event["type"] == "text":
                chunk = event["content"]
                full_text += chunk
                yield {
                    "event": evt_name,
                    "data": json.dumps({"chunk": chunk}, ensure_ascii=False),
                }
            elif event["type"] == "tool_call":
                yield {
                    "event": "tool_call",
                    "data": json.dumps({
                        "name": event["name"],
                        "args": event["args"],
                    }, ensure_ascii=False),
                }
            elif event["type"] == "tool_result":
                summary = event["summary"]
                full_text += f"\n\n> {summary}\n\n"
                yield {
                    "event": "tool_result",
                    "data": json.dumps({"summary": summary}, ensure_ascii=False),
                }

        history.append((role, full_text))
        upsert_chain_entry(chain_session_id, topic, history=list(history), status="running")

        # B1: regenerate_only — 只跑一轮就结束，跳过后续所有流程
        if sess.get("regenerate_only"):
            break

        # ── 功能 J：主持人点评 ──────────────────────────────────────────
        if do_moderator and turn_idx > 0:
            mod_text = await moderator_comment(topic, history, api_settings)
            if mod_text:
                yield {
                    "event": "moderator",
                    "data": json.dumps({"text": mod_text}, ensure_ascii=False),
                }

        if STOP_MARKER in full_text:
            break

        if USER_MARKER in full_text:
            event_obj = asyncio.Event()
            sess["user_event"] = event_obj
            sess["user_answer"] = None
            yield {
                "event": "ask_user",
                "data": json.dumps({"kind": "insert", "round": round_num}, ensure_ascii=False),
            }
            try:
                await asyncio.wait_for(event_obj.wait(), timeout=300.0)
            except asyncio.TimeoutError:
                pass
            answer = sess.get("user_answer") or {}
            user_text = answer.get("text", answer.get("value", "")).strip()
            if user_text:
                history.append(("user", user_text))
                upsert_chain_entry(chain_session_id, topic, history=list(history), status="running")

        if (turn_idx + 1) % UNIT_SIZE == 0 and turn_idx + 1 < MAX_TURNS:
            event_obj = asyncio.Event()
            sess["user_event"] = event_obj
            sess["user_answer"] = None
            yield {
                "event": "ask_user",
                "data": json.dumps({"kind": "stage", "round": round_num}, ensure_ascii=False),
            }
            try:
                await asyncio.wait_for(event_obj.wait(), timeout=300.0)
            except asyncio.TimeoutError:
                pass
            answer = sess.get("user_answer") or {}
            value = answer.get("value", "continue")
            if value == "stop":
                break
            if value == "insert":
                user_text = answer.get("text", "").strip()
                if user_text:
                    history.append(("user", user_text))
                    upsert_chain_entry(chain_session_id, topic, history=list(history), status="running")

    # B1: regenerate_only 快速路径 — 保存 recent_histories 并直接发 done
    if sess.get("regenerate_only"):
        recent_histories[session_id] = {
            "topic": topic,
            "history": list(history),
            "role_a": role_a,
            "role_b": role_b,
            "role_c": role_c,
            "attach": attach,
            "image_urls": image_urls,
            "reasoning": sess.get("reasoning", "high"),
        }
        duration_ms = int((time.time() - start) * 1000)
        yield {
            "event": "done",
            "data": json.dumps({
                "duration_ms": duration_ms,
                "turns": len(history),
                "session_id": session_id,
                "export_path": "",
                "topic_idx": None,
            }, ensure_ascii=False),
        }
        sessions.pop(session_id, None)
        return

    upsert_chain_entry(chain_session_id, topic, history=list(history), status="summarizing")
    duration_ms = int((time.time() - start) * 1000)

    # 秘书总结
    secretary_text = ""
    async for sec_event in secretary_summary_gen(topic, history, role_secretary, api_settings):
        secretary_text += sec_event["content"]
        yield {
            "event": "secretary",
            "data": json.dumps({"chunk": sec_event["content"]}, ensure_ascii=False),
        }

    # 解析待办清单
    todos = _parse_todos_from_secretary(secretary_text)

    # 功能 F：评分
    scores = await rate_debate_gen(topic, history, secretary_text, api_settings)
    if scores:
        upsert_chain_entry(chain_session_id, topic, scores=scores)
        yield {
            "event": "scores",
            "data": json.dumps(scores, ensure_ascii=False),
        }

    # 等标签（最多 10s）
    tags = []
    try:
        tags = await asyncio.wait_for(tag_task, timeout=10.0)
    except (asyncio.TimeoutError, Exception):
        pass

    if todos or tags:
        upsert_chain_entry(chain_session_id, topic, todos=todos if todos else None, tags=tags if tags else None)

    # 推送 todos 和 tags 给前端
    if todos:
        yield {
            "event": "todos",
            "data": json.dumps({"todos": todos}, ensure_ascii=False),
        }
    if tags:
        yield {
            "event": "tags",
            "data": json.dumps({"tags": tags}, ensure_ascii=False),
        }

    # 生成导出 Markdown
    # 获取该议题的最终 chain index
    topic_idx = next((i for i, e in enumerate(chain) if e.get("session_id") == chain_session_id), None)
    if topic_idx is None:
        topic_idx = next((i for i, e in enumerate(chain) if e["topic"] == topic), None)
    export_md = build_export_md(topic, history, secretary_text, duration_ms, role_a, role_b, role_c, scores, todos)

    # Save local Markdown export outside the repository.
    slug = _make_slug(topic)
    ts_str = datetime.now().strftime("%Y%m%d-%H%M")
    export_filename = f"yuanzhuo-{slug}-{ts_str}.md"
    export_path_obj = EXPORT_DIR / export_filename
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        export_path_obj.write_text(export_md, encoding="utf-8")
        export_path = str(export_path_obj)
    except Exception:
        export_path = ""

    completed_sessions[session_id] = {
        "topic": topic,
        "export_md": export_md,
        "export_path": export_path,
    }
    # B1：记录完整 history 供「重新生成最后一轮」使用
    recent_histories[session_id] = {
        "topic": topic,
        "history": list(history),
        "role_a": role_a,
        "role_b": role_b,
        "role_c": role_c,
        "attach": attach,
        "image_urls": image_urls,
        "reasoning": sess.get("reasoning", "high"),
    }
    upsert_chain_entry(chain_session_id, topic, history=list(history), status="completed")

    yield {
        "event": "done",
        "data": json.dumps({
            "duration_ms": duration_ms,
            "turns": len(history),
            "session_id": session_id,
            "export_path": export_path,
            "topic_idx": topic_idx,
        }, ensure_ascii=False),
    }

    sessions.pop(session_id, None)


if __name__ == "__main__":
    port = 8888
    with socket.socket() as s:
        if s.connect_ex(("localhost", port)) == 0:
            port = 8889
            print(f"⚠️  端口 8888 被占用，切换到 {port}")

    print("🏛️  圆桌会议服务启动")
    print(f"🌐  http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
