# Privacy

Yuanzhuo is a local-first, bring-your-own-key tool. It does not bundle a maintainer-owned API key.

## Data Flow

1. The user enters a topic and optional attachments in the browser.
2. The browser sends the roundtable request to the local FastAPI server.
3. The server calls the configured OpenAI-compatible endpoint.
4. Streaming events return to the browser over SSE.
5. Summaries, todos, tags, scores, and exports are generated locally.

## Credentials

Browser API settings may be stored in localStorage. This is convenient for local use but is not a complete security model for shared deployments.

For public or team deployments:

- use HTTPS
- add authentication
- avoid shared server-side API keys unless you also add rate limits and abuse controls
- explain credential handling to users
- avoid logging request headers and full provider payloads

## Attachments And Local Files

Attachments can be sent to the configured model provider as part of a roundtable request. Optional research tools can fetch web pages and read limited local files under the user's home directory.

Review `tools.py` and disable capabilities that do not match your deployment model.
