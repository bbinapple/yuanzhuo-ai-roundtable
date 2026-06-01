# Codex For OSS Application Draft

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
