# Codex For OSS Application Draft

Official form: https://openai.com/form/codex-for-oss/

## Final Form Values

Use these values when submitting the form.

| Field | Value |
|---|---|
| First name | your first name |
| Last name | your last name |
| Email | email associated with your ChatGPT account |
| GitHub username | bbinapple |
| GitHub repository URL | https://github.com/bbinapple/yuanzhuo-ai-roundtable |
| Describe your role | Primary maintainer |
| I'm interested in | Codex Security; API credits for my project |
| OpenAI Organization ID | TODO: paste from https://platform.openai.com/settings/organization/general |

## Why This Repository Qualifies (500 Characters Max)

Yuanzhuo AI Roundtable is a new local-first OSS workbench for structured multi-agent discussion, decision review, summaries, action items, and Markdown export. It is BYOK-first, transparent, and self-hostable, with clear privacy and deployment boundaries. I am not claiming large stars, downloads, or user numbers; its value is as an inspectable starting point for human-in-the-loop AI workflows.

Character count: 396

## How API Credits Would Be Used (500 Characters Max)

I would use API credits for core OSS maintenance: testing OpenAI-compatible behavior across roles, generating regression cases for streaming sessions, improving summary/action-item prompts, reviewing PRs for safety and privacy issues, validating examples before releases, and drafting documentation updates that make the project easier and safer for contributors to run.

Character count: 370

## Anything Else We Should Know (500 Characters Max)

The repository is public, MIT-licensed, and includes v0.1.0 release notes, security/privacy/deployment docs, issue templates, and a visible roadmap. The project is new, so I am intentionally not claiming broad adoption; I plan to maintain it through issues, releases, regression tests, and safety-focused PR review.

Character count: 315

## Public Project Evidence

- Repository: https://github.com/bbinapple/yuanzhuo-ai-roundtable
- Release: https://github.com/bbinapple/yuanzhuo-ai-roundtable/releases/tag/v0.1.0
- Roadmap issues: https://github.com/bbinapple/yuanzhuo-ai-roundtable/issues
- Security policy: https://github.com/bbinapple/yuanzhuo-ai-roundtable/blob/main/SECURITY.md
- Privacy notes: https://github.com/bbinapple/yuanzhuo-ai-roundtable/blob/main/docs/PRIVACY.md
- Deployment notes: https://github.com/bbinapple/yuanzhuo-ai-roundtable/blob/main/docs/DEPLOYMENT.md

## Submission Checklist

- [x] Public GitHub repository.
- [x] Public release `v0.1.0`.
- [x] Public roadmap issues.
- [x] MIT license.
- [x] Security, privacy, deployment, and contributing docs.
- [x] No maintainer-owned API key in the repository.
- [x] No large stars/downloads/user claims.
- [ ] OpenAI Organization ID pasted into the form.
- [ ] Manual final review and submit.

## Repository Description

Yuanzhuo AI Roundtable is an open-source multi-agent roundtable workbench for structured AI debates, decision review, meeting summaries, action items, and Markdown exports. Users bring their own OpenAI-compatible API key.

## README Tagline

Turn an ambiguous question into a structured multi-agent roundtable, then export the debate, takeaways, todos, tags, and scores.

## Release Notes For v0.1.0

This first public package contains the local-first Yuanzhuo AI Roundtable workbench. It includes a FastAPI backend, streaming browser UI, role and template system, optional research stage, user interjections, summaries, todos, tags, scoring, and Markdown export. The project is BYOK-first and does not ship with maintainer-owned model credentials.

## Why This Repository Qualifies

Yuanzhuo AI Roundtable is a new local-first OSS workbench for structured multi-agent discussion, decision review, summaries, action items, and Markdown export. It is BYOK-first, transparent, and self-hostable, with clear privacy and deployment boundaries. I am not claiming large stars, downloads, or user numbers; its value is as an inspectable starting point for human-in-the-loop AI workflows.

## How API Credits Would Be Used

I would use API credits for core OSS maintenance: testing OpenAI-compatible behavior across roles, generating regression cases for streaming sessions, improving summary/action-item prompts, reviewing PRs for safety and privacy issues, validating examples before releases, and drafting documentation updates that make the project easier and safer for contributors to run.

## 中文版：为什么这个仓库符合要求

Yuanzhuo AI Roundtable 是一个小型但完整的开源多 Agent 圆桌决策工具。它包含本地优先的 FastAPI 后端、浏览器界面、内置角色、议题模板、流式输出、Markdown 导出，以及清晰的 BYOK 隐私边界。这个仓库是新整理的公开版本，目前不会夸大 stars、下载量或用户规模。它的价值在于为开发者和运营者提供一个透明、可检查、可本地运行的多 Agent 讨论与人机协同决策实验起点，而不是依赖封闭托管产品。

## 中文版：API 额度将如何使用

API 额度会用于提升开源项目维护质量，包括测试不同角色在 OpenAI-compatible 模型下的表现、生成流式圆桌会话的回归场景、改进总结和待办 prompt、审查 PR 中的隐私与安全风险、更新文档，以及在发布前验证示例 workflow。目标是降低维护成本，同时让项目更安全、更容易运行，也更适合社区贡献。
