#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-/workspace/project}"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

[[ -x "${PYTHON}" ]] || { echo "Missing project Python: ${PYTHON}" >&2; exit 1; }
"${PYTHON}" -c 'import aiohttp' ||
  { echo "aiohttp is missing; install the gateway package into the shared environment" >&2; exit 1; }

export HF_HOME="${HF_HOME:-${PROJECT_ROOT}/.cache/huggingface}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-${HF_HOME}/hub}"
export GATEWAY_HOST="${GATEWAY_HOST:-127.0.0.1}"
export GATEWAY_PORT="${GATEWAY_PORT:-8080}"
export GATEWAY_REPLICA1_URL="${GATEWAY_REPLICA1_URL:-http://127.0.0.1:8101}"
export GATEWAY_REPLICA2_URL="${GATEWAY_REPLICA2_URL:-http://127.0.0.1:8102}"
export GATEWAY_REPLICA2_ENABLED="${GATEWAY_REPLICA2_ENABLED:-0}"
export GATEWAY_TOKENIZER_MODEL="${GATEWAY_TOKENIZER_MODEL:-Qwen/Qwen3-4B-Instruct-2507}"
export PYTHONPATH="${SOURCE_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

exec "${PYTHON}" -m gateway
