#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# venv 사용 (존재하면 활성화)
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

python3 -m streamlit run src/dashboard/app.py --server.port=8080 --server.address=0.0.0.0
