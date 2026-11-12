#!/usr/bin/env bash
# One-time setup: Python venv + dependencies, Belgrade address index, test data.
set -euo pipefail
cd "$(dirname "$0")/../backend"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
# pyenv builds without libsqlite3-dev have no sqlite3 module; use the bundled one
.venv/bin/python -c "import sqlite3" 2>/dev/null || .venv/bin/pip install -q pysqlite3-binary
[ -f data/addresses.sqlite ] || .venv/bin/python tools/build_address_index.py
[ -f ../test-data/rejon-nbg-zemun-100.csv ] || .venv/bin/python tools/make_test_data.py
echo "Setup OK. Start the server with: scripts/run-backend.sh"
