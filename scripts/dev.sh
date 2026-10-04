#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d backend/.venv ]]; then
  (cd backend && uv sync --extra dev)
fi
if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm install)
fi

# shellcheck disable=SC1091
source backend/.venv/bin/activate
mkdir -p data
uvicorn aerp.api.main:app --app-dir backend --host 127.0.0.1 --port 8000 &
API_PID=$!
trap 'kill $API_PID' EXIT
(cd frontend && npm run dev -- --host 127.0.0.1 --port 5173)
