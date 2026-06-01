# Yuanzhuo AI Roundtable

An open-source multi-agent roundtable workbench for structured AI debates, decision review, meeting summaries, action items, and Markdown exports. Bring your own OpenAI-compatible API key.

Yuanzhuo helps a user turn an ambiguous question into a structured discussion between multiple AI roles. It is designed for local-first decision support: agents debate, a moderator can keep the discussion on track, and a summary agent turns the conversation into takeaways and next actions.

> 中文摘要：Yuanzhuo AI Roundtable 是一个通用多 Agent 圆桌决策工作台。用户输入议题，多个 Agent 从不同角度讨论，最后生成总结、待办、标签、评分和 Markdown 导出。项目默认不内置服务方 API Key，用户在浏览器里填写自己的 OpenAI-compatible API 配置。

## Features

- Multi-agent debate with two required roles and an optional third role.
- Built-in analyst, executor, investor, user advocate, mentor, moderator, and researcher roles.
- BYOK model settings through the browser UI.
- Topic templates for free discussion, SWOT decisions, negotiation prep, side project review, and ecommerce opportunity review.
- Optional pre-discussion research stage. Review tool access before enabling it in shared deployments.
- Streaming web UI with pause, user interjection, snapshots, summaries, todos, tags, scores, and Markdown export.
- Local-first FastAPI server with OpenAI-compatible model endpoints.

## Quick Start

```bash
cd yuanzhuo-ai-roundtable
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

Open the URL printed by the server, usually:

```text
http://127.0.0.1:8888/
```

Run the local checks:

```bash
bash scripts/check.sh
```

Expected output:

```text
== Python syntax ==
python ast ok
== JavaScript syntax ==
== HTTP smoke on 127.0.0.1:<port> ==
All checks passed.
```

## BYOK API Settings

Yuanzhuo does not require the project maintainer to provide a shared model key. Each user can open `Settings -> API Settings` in the web UI and enter:

- `API Base URL`, for example `https://api.openai.com/v1`
- `API Key`, provided by the user
- default analyst model
- default executor model
- optional summary model

The browser stores these settings in localStorage. They are sent to the local server only when a roundtable run starts. The project does not write user API keys to repository files.

For public deployment, use HTTPS and review your own data-handling obligations before allowing users to enter provider credentials.

## Web UI

- `/` - primary single-panel roundtable workspace
- `/dual` - two-panel comparison workspace
- `/simple` - compatibility entry that reuses the main workspace

## API Surface

- `GET /api/health`
- `POST /api/models`
- `GET /api/roles`
- `POST /api/round`
- `GET /api/stream/{session_id}`
- `POST /api/respond/{session_id}`
- `GET /api/export/{session_id}`

## Architecture

```text
Browser UI
  -> FastAPI server
    -> role and template loader
    -> OpenAI-compatible chat completions
    -> optional tool calls for research
    -> SSE streaming events
    -> summary, todos, tags, scores, Markdown export
```

Core files:

- `server.py` - FastAPI app, streaming debate orchestration, export endpoints
- `cli.py` - command-line interface for local roundtable sessions
- `roles/` - built-in role prompts with frontmatter metadata
- `templates/` - topic templates
- `public/` - browser UI
- `scripts/check.sh` - syntax and HTTP smoke checks

More docs:

- [Configuration](docs/CONFIGURATION.md)
- [Privacy](docs/PRIVACY.md)
- [Deployment](docs/DEPLOYMENT.md)
- [v0.1.0 Release Notes](docs/release/v0.1.0.md)
- [Starter GitHub Issues](docs/GITHUB_ISSUES_TO_CREATE.md)

## Safety And Privacy Notes

- Yuanzhuo is a decision-support tool, not an autonomous decision maker.
- The example templates do not provide financial, legal, medical, or business-result guarantees.
- Users remain responsible for checking model output and making final decisions.
- Do not commit `.env`, provider credentials, private meeting notes, customer data, or generated histories.
- The default `.gitignore` excludes local environment files, cache files, session exports, and agent workspace metadata.
- Tool features can fetch web pages. The `read_local` model tool is disabled by default; enable it only on a trusted local machine. Review `tools.py` before deploying Yuanzhuo beyond a trusted local environment.

## Roadmap

- More provider presets.
- Better role and template marketplace.
- Optional local storage encryption.
- Export formats beyond Markdown.
- More example workflows.
- Docker packaging.

## Maintainer Use Of Codex

This repository is a good fit for AI-assisted OSS maintenance workflows:

- pull request review for prompt, privacy, and tool-access changes
- regression tests for streaming sessions and BYOK validation
- release checklist generation and changelog drafting
- documentation review for local-first and shared-deployment safety
- issue triage for provider presets, template packs, and export formats

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## Security

Please report security issues privately. See [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
