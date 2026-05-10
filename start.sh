#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ -f ".env" ]; then
  export $(grep -v '^#' .env | xargs)
fi

python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --log-level "${LOG_LEVEL:-info}"
