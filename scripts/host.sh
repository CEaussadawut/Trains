#!/usr/bin/env bash
# Build the frontend and serve the whole app from one Python process.
#
# Binds to 127.0.0.1 on purpose: cloudflared connects to it locally, so the app
# is never directly reachable on the LAN. Everything public goes through the
# tunnel.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"

if [ ! -d web/node_modules ]; then
  echo "==> installing frontend dependencies"
  (cd web && npm ci --no-fund --no-audit)
fi

echo "==> type-checking (vite build does not do this)"
(cd web && npm run typecheck)

echo "==> building frontend"
(cd web && npm run build)

echo "==> serving http://127.0.0.1:${PORT}"
exec python3 -m server --dist web/dist --port "${PORT}"
