# Contributing

Thanks for your interest in Yuanzhuo AI Roundtable.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
bash scripts/check.sh
```

## Pull Request Guidelines

- Keep changes focused and explain the user-facing behavior.
- Do not commit `.env`, API keys, local histories, generated exports, screenshots containing credentials, or private meeting content.
- Add or update documentation when changing public behavior, routes, templates, or role metadata.
- Run `bash scripts/check.sh` before opening a pull request.
- Prefer small, readable changes over large rewrites.

## Role And Template Contributions

New roles and templates should be generic enough for public use. Avoid prompts that include private business data, personal details, customer names, or claims that the tool can guarantee financial, legal, medical, or operational outcomes.

## Issue Reports

Please include:

- operating system and Python version
- install command used
- route or command that failed
- expected behavior
- actual behavior
- sanitized logs if available
