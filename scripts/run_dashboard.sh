#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# venv 사용 (존재하면 활성화)
if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# .env 로드(있으면) — 대시보드 비밀번호 가드용
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# 비밀번호 가드 — change-me/빈값으로 기동하면 대시보드가 fail-closed로 접근을 차단하므로 사전 거부한다
# (docker-entrypoint.sh의 dashboard 모드와 동일 정책).
PW="${KSI_CCTV_DASHBOARD_PASSWORD:-}"
if [ -z "$PW" ] || [ "$PW" = "change-me" ]; then
    echo "[ERROR] KSI_CCTV_DASHBOARD_PASSWORD가 미설정이거나 기본값(change-me)입니다." >&2
    echo "[ERROR] .env에 강력한 비밀번호를 설정한 뒤 다시 실행하세요(미설정 시 대시보드 접근 차단됨)." >&2
    exit 1
fi

# 바인드 주소 — 기본 0.0.0.0(운영은 reverse proxy + TLS 권장, docs/operations.md). 루프백만 원하면
# KSI_CCTV_DASHBOARD_HOST=127.0.0.1로 설정.
HOST="${KSI_CCTV_DASHBOARD_HOST:-0.0.0.0}"
python3 -m streamlit run src/dashboard/app.py --server.port=8080 --server.address="$HOST"
