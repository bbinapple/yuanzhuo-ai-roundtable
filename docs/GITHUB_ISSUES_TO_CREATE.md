# Starter GitHub Issues

These issues can be created after the public repository is uploaded. They make the roadmap visible without pretending the project already has external users.

## 1. Add Docker packaging

Create a `Dockerfile` and `docker-compose.yml` for local self-hosting.

Acceptance criteria:

- `docker compose up` starts the app locally.
- Docs explain BYOK settings and volume mounts.
- No maintainer API key is baked into the image.

Labels: `enhancement`, `deployment`

## 2. Add provider presets

Add optional presets for common OpenAI-compatible providers while keeping custom Base URL support.

Acceptance criteria:

- Presets fill only Base URL and suggested model IDs.
- Users still provide their own API key.
- Docs explain that presets are examples, not endorsements.

Labels: `enhancement`, `providers`

## 3. Add regression tests for streaming sessions

Create tests for SSE event ordering, done/error handling, and BYOK rejection behavior.

Acceptance criteria:

- Tests cover `/api/round`, `/api/stream/{session_id}`, and no-key rejection paths.
- Tests avoid real provider calls.
- `scripts/check.sh` includes the new test command if dependencies stay lightweight.

Labels: `testing`, `maintenance`

## 4. Improve i18n coverage

Move key UI strings into a small translation map and expand English/Chinese coverage.

Acceptance criteria:

- Homepage, settings, API key alerts, Agent management, export, and error messages are covered.
- No major UI redesign.
- Existing localStorage settings continue to work.

Labels: `i18n`, `frontend`

## 5. Harden tool permission UX

Improve the user confirmation flow for web search, web fetch, and optional local file access.

Acceptance criteria:

- Tool confirmation clearly shows tool name and target.
- `read_local` remains disabled by default.
- Shared deployment warnings stay visible in docs.

Labels: `security`, `ux`

## 6. Add more example workflows

Add generic, non-sensitive workflow templates for decision review, meeting summaries, product planning, and project retrospectives.

Acceptance criteria:

- Examples are generic and do not include private business data.
- No financial, legal, medical, or commercial guarantees.
- README links to the examples.

Labels: `templates`, `documentation`

## 7. Add export formats beyond Markdown

Explore HTML and JSON export options.

Acceptance criteria:

- Markdown export remains unchanged.
- JSON export preserves topic, roles, turns, summary, todos, tags, and scores.
- Docs explain where exports are stored.

Labels: `enhancement`, `export`

## 8. Add screenshot or GIF demo

Add visual assets for the README once the repository is public.

Acceptance criteria:

- Include a homepage screenshot.
- Include a short roundtable flow screenshot or GIF.
- Do not show real API keys, private topics, or generated private histories.

Labels: `documentation`, `good first issue`
