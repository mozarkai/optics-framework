#!/usr/bin/env bash
set -euo pipefail

HOST=${1:-http://127.0.0.1:8090}

curl -fsS "$HOST/health" || exit 1
echo "ok"
