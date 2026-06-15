#!/usr/bin/env bash
# KSI-CCTV Docker 컨테이너 진입점 (mode-aware)
#   KSI_CCTV_MODE=daemon  (기본): 무인 탐지 데몬 src/run.py 실행
#                                  — 파이프라인 자동 기동 + /health(8081, pipeline-aware)
#   KSI_CCTV_MODE=dashboard      : 운영자 대시보드 streamlit 실행(8080)
# 24시간 탐지/알람/헬스는 daemon이 책임지고, 대시보드는 별도 운영자 도구다.
# (systemd는 docker-entrypoint 대신 src/run.py를 직접 ExecStart로 실행한다.)
set -euo pipefail

MODE="${KSI_CCTV_MODE:-daemon}"

# ── 작업 디렉토리/인터프리터 결정 ──────────────────────────────────────────────
# 스크립트 위치 기준으로 프로젝트 루트로 이동(systemd/Docker 양쪽에서 src/·.venv 탐색 보장).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Python 인터프리터: 환경변수 > .venv > python3 > python 순으로 결정.
# (systemd 호스트는 .venv에만 streamlit이 설치되고 bare `python`이 없을 수 있음 — bare python 금지.)
if [ -n "${KSI_CCTV_PYTHON:-}" ]; then
    PYTHON="$KSI_CCTV_PYTHON"
elif [ -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    PYTHON="${PROJECT_ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
else
    echo "[ERROR] Python 인터프리터를 찾을 수 없습니다(.venv/python3/python 모두 없음)." >&2
    exit 1
fi
echo "[INFO] Python 인터프리터: $PYTHON"

# ── 런타임 디렉토리 확인 ────────────────────────────────────────────────────────
echo "[INFO] KSI-CCTV 컨테이너 시작 (MODE=$MODE)..."
mkdir -p logs data/captures models/uploaded

if [ "$MODE" = "dashboard" ]; then
    # ── 대시보드 모드: 비밀번호 가드 후 streamlit ──────────────────────────────
    # 기본값(change-me)으로 기동하면 CCTV 대시보드가 약한 비밀번호로 외부 노출됨.
    DASHBOARD_PASSWORD="${KSI_CCTV_DASHBOARD_PASSWORD:-}"
    if [ -z "$DASHBOARD_PASSWORD" ] || [ "$DASHBOARD_PASSWORD" = "change-me" ]; then
        echo "[ERROR] KSI_CCTV_DASHBOARD_PASSWORD 가 미설정이거나 기본값(change-me)입니다." >&2
        echo "[ERROR] .env 파일에 강력한 비밀번호를 설정한 후 다시 시작하세요." >&2
        exit 1
    fi
    echo "[INFO] 대시보드 시작 (포트 8080)..."
    # exec로 PID 1을 streamlit으로 교체 → 컨테이너 시그널(SIGTERM) 수신
    exec "$PYTHON" -m streamlit run src/dashboard/app.py \
        --server.port=8080 \
        --server.address=0.0.0.0 \
        --server.headless=true
fi

# ── 데몬 모드(기본): 무인 탐지 + /health(8081) ─────────────────────────────────
# src/run.py가 파이프라인을 자동 기동하고 자체 헬스 서버(pipeline-aware)를 띄운다.
echo "[INFO] 무인 탐지 데몬 시작 (src/run.py, health 포트 8081)..."
exec "$PYTHON" src/run.py
