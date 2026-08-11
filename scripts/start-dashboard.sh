#!/usr/bin/env bash
# Sobe o dashboard local de monitoramento.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

HOST="${FINANCEIROS_DASH_HOST:-127.0.0.1}"
PORT="${FINANCEIROS_DASH_PORT:-8787}"
CONFIG="${FINANCEIROS_CONFIG:-config/default.yaml}"

echo "Dashboard em http://${HOST}:${PORT}"
exec financeiros --config "$CONFIG" dashboard --host "$HOST" --port "$PORT"
