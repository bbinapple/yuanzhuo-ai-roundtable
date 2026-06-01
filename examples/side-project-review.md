# Side Project Review

## Topic

Should we build a small open-source tool for structured AI meeting reviews?

## Roundtable Summary

The roundtable agreed that the project is worth building if the first version stays narrow: local-first setup, bring-your-own-key configuration, a small set of role templates, and Markdown export. The main risk is building too many collaboration features before users can clearly see the output quality.

## Agent Positions

### Analyst

The strongest reason to proceed is that many people already use AI for meeting notes, but fewer tools make the reasoning process visible. A roundtable format can help users compare tradeoffs instead of accepting one generated answer.

### Executor

The first release should avoid accounts, billing, cloud sync, or shared workspaces. A local FastAPI app with browser settings is enough to validate the workflow and reduce operational risk.

### User Advocate

The tool must show value before asking the user to configure too much. Good example outputs, clear API-key guidance, and a one-command smoke check matter more than advanced settings.

## Recommended Decision

Proceed with a small public release focused on:

- local-first usage
- clear BYOK setup
- three to five high-quality templates
- Markdown export
- strong privacy notes

## Action Items

- [ ] Add a short demo flow to the README.
- [ ] Create sanitized example exports.
- [ ] Add CI for the local smoke test.
- [ ] Add one Docker-based quick start.

## Tags

`local-first`, `multi-agent`, `meeting-review`, `oss`

## Scorecard

| Dimension | Score | Notes |
|---|---:|---|
| User value | 8 | Clear workflow pain point. |
| Build complexity | 5 | Manageable if scope stays local-first. |
| Maintenance risk | 4 | Provider differences need careful docs. |
| OSS fit | 8 | Easy to inspect, fork, and extend. |
