#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT/backend"
if command -v uv >/dev/null 2>&1; then
  uv python install 3.11 >/dev/null 2>&1 || true
  if [[ ! -d .venv ]]; then
    uv venv --python 3.11 .venv
  fi
  source .venv/bin/activate
  uv pip install -e ".[dev]"
else
  if [[ ! -d .venv ]]; then
    python3.11 -m venv .venv || python3 -m venv .venv
  fi
  source .venv/bin/activate
  pip install -U pip
  pip install -e ".[dev]"
fi

cd "$ROOT/frontend"
npm install

echo "安装完成。启动：./scripts/dev.sh 或分别启动后端与前端（见 README）。"
