#!/usr/bin/env bash
# Inicia o robô em paper trading (run-loop).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

INTERVAL="${FINANCEIROS_INTERVAL:-3600}"
CONFIG="${FINANCEIROS_CONFIG:-config/default.yaml}"

echo "Iniciando financeiros run-loop (interval=${INTERVAL}s, config=${CONFIG})"
exec financeiros --config "$CONFIG" run-loop --interval "$INTERVAL"
