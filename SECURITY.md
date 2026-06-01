# Security Policy

## Supported Versions

The public repository starts at `v0.1.0`. Security fixes will target the latest public release.

## Reporting A Vulnerability

Please do not open a public issue for credential leaks, prompt-injection bypasses, or data-exposure vulnerabilities.

Report privately by opening a minimal private advisory or contacting the maintainer through the security contact listed on the GitHub repository.

Include:

- affected version or commit
- reproduction steps
- expected impact
- whether credentials, local files, browser storage, or exported histories are involved

## Credential Handling

Yuanzhuo is BYOK-first. Users enter provider settings in the browser UI. The project should not commit or distribute:

- `.env` files
- API keys or bearer tokens
- provider credentials
- customer or meeting data
- local histories or generated exports

If you add provider integrations, sanitize errors and avoid logging request headers, full payloads, or credentials.

## Tool Access

Yuanzhuo includes optional research tools such as web search and web fetch. The `read_local` model tool is disabled by default and is omitted from the tool list unless `YUANZHUO_ENABLE_READ_LOCAL=1` is set. If enabled, it can read text files under the user's home directory and can send excerpts to the configured model provider. Use it only on a trusted local machine.

By default, `YUANZHUO_TOOL_CONFIRM_THRESHOLD=0`, so model tool calls require user confirmation before execution. Review `tools.py`, keep unneeded tools disabled, and add authentication, authorization, and auditing before exposing the app to untrusted users.

`YUANZHUO_LOCAL_ASSISTANT_CMD` is an optional local-only integration hook. Keep it empty for shared deployments unless you fully control the host environment and have added authentication and origin restrictions.
