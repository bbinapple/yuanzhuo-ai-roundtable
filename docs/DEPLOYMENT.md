# Deployment

Yuanzhuo is safest as a local developer tool. You can deploy it for a trusted team, but you should add operational controls first.

## Local Use

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

## Shared Deployment Checklist

- Serve over HTTPS.
- Add authentication before exposing the app publicly.
- Keep `YUANZHUO_RELAY_API_KEY` empty unless you intentionally operate a relay.
- If you operate a relay, add rate limits, usage monitoring, and abuse controls.
- Keep `YUANZHUO_ENABLE_READ_LOCAL` empty in shared deployments.
- Review tool functions in `tools.py`; default settings ask for confirmation before every model tool call.
- Do not store user API keys server-side unless you have a clear security model.
- Do not enable `YUANZHUO_LOCAL_ASSISTANT_CMD` on a public service unless you add authentication and origin restrictions.
- Rotate any credentials that were accidentally logged or committed.

## Smoke Checks

```bash
bash scripts/check.sh
curl -fsS http://127.0.0.1:8888/api/health
curl -fsS http://127.0.0.1:8888/api/roles
```
