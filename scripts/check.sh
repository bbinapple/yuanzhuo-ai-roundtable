#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== Python syntax =="
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import ast
from pathlib import Path

for filename in ("server.py", "cli.py", "tools.py"):
    ast.parse(Path(filename).read_text(encoding="utf-8"), filename=filename)

print("python ast ok")
PY

echo "== JavaScript syntax =="
node --check public/panel.js

TMP_DIR="$(mktemp -d)"
cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

python3 - <<'PY' public/index.html "$TMP_DIR/index.inline.js"
import re
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text(encoding="utf-8")
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, flags=re.S | re.I)
Path(sys.argv[2]).write_text("\n;\n".join(blocks), encoding="utf-8")
PY
node --check "$TMP_DIR/index.inline.js"

python3 - <<'PY' public/dual.html "$TMP_DIR/dual.inline.js"
import re
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text(encoding="utf-8")
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, flags=re.S | re.I)
Path(sys.argv[2]).write_text("\n;\n".join(blocks), encoding="utf-8")
PY
node --check "$TMP_DIR/dual.inline.js"

PORT="$(python3 - <<'PY'
import socket

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])
PY
)"

echo "== HTTP smoke on 127.0.0.1:${PORT} =="
PYTHONDONTWRITEBYTECODE=1 python3 -m uvicorn server:app --host 127.0.0.1 --port "$PORT" --log-level warning >"$TMP_DIR/server.log" 2>&1 &
SERVER_PID="$!"

for _ in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null
curl -fsS "http://127.0.0.1:${PORT}/dual" >/dev/null
curl -fsS "http://127.0.0.1:${PORT}/api/roles" >/dev/null
curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null

echo "All checks passed."
