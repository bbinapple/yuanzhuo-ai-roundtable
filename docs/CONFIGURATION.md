# Configuration

Yuanzhuo is BYOK-first. Most users can start the server and enter provider settings in the browser UI.

## Browser Settings

Open `Settings -> API Settings` and enter:

- API Base URL, for example `https://api.openai.com/v1`
- API Key
- analyst model
- executor model
- optional summary model

These values are stored in browser localStorage and sent to the local server when a roundtable starts.

## Environment Variables

Environment variables are optional local defaults:

| Name | Purpose | Default |
|---|---|---|
| `YUANZHUO_RELAY_BASE_URL` | OpenAI-compatible base URL | `https://api.openai.com/v1` |
| `YUANZHUO_RELAY_API_KEY` | Optional local relay key | empty |
| `YUANZHUO_ANALYST_MODEL` | default analyst model | `gpt-4.1` |
| `YUANZHUO_EXECUTOR_MODEL` | default executor model | `gpt-4.1` |
| `YUANZHUO_SECRETARY_MODEL` | default summary model | analyst model |
| `YUANZHUO_LOCAL_ASSISTANT_CMD` | optional local assistant launch command | empty |
| `YUANZHUO_EXPORT_DIR` | local Markdown export directory | `~/.yuanzhuo/exports` |
| `YUANZHUO_ENABLE_READ_LOCAL` | enable the `read_local` model tool in trusted local environments only | empty / disabled |
| `YUANZHUO_TOOL_CONFIRM_THRESHOLD` | number of tool calls allowed before user confirmation is required; `0` asks before every tool call | `0` |

Do not commit `.env` files.

`read_local` is disabled by default and is omitted from the model tool list unless `YUANZHUO_ENABLE_READ_LOCAL=1` is set. Only enable it on a trusted local machine.

## Local Data

Roundtable history is stored under:

```text
~/.yuanzhuo/history.json
```

Generated Markdown exports are stored under:

```text
~/.yuanzhuo/exports
```

Do not publish generated histories or exports unless you have reviewed their contents.
