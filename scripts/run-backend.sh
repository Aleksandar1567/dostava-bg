#!/usr/bin/env bash
# API + web app on http://localhost:8000 (reachable from the LAN on 0.0.0.0).
# OSRM_URL=http://localhost:5000 to use a local OSRM instead of the public demo server.
set -euo pipefail
cd "$(dirname "$0")/../backend"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
