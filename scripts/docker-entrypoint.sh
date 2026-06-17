#!/usr/bin/env bash
# KSI-CCTV Docker 컨테이너 진입점
#   무인 탐지 데몬 src/run.py 실행 — 파이프라인 자동 기동 + 엔진 API/헬스(8081, pipeline-aware).
# 운영자 UI는 별도 컨테이너(ksi-cctv-web, Next.js)다 — 이 진입점은 탐지 엔진만 책임진다.
# (systemd는 docker-entrypoint 대신 src/run.py를 직접 ExecStart로 실행한다.)
set -euo pipefail

# ── 작업 디렉토리/인터프리터 결정 ──────────────────────────────────────────────
# 스크립트 위치 기준으로 프로젝트 루트로 이동(systemd/Docker 양쪽에서 src/·.venv 탐색 보장).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Python 인터프리터: 환경변수 > .venv > python3 > python 순으로 결정.
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
echo "[INFO] KSI-CCTV 컨테이너 시작..."
mkdir -p logs data/captures models/uploaded

# ── 무인 탐지 데몬: 탐지 + 엔진 API/헬스(8081) ─────────────────────────────────
# src/run.py가 파이프라인을 자동 기동하고 FastAPI(API+제어+헬스, pipeline-aware)를 띄운다.
echo "[INFO] 무인 탐지 데몬 시작 (src/run.py, API/health 포트 8081)..."
exec "$PYTHON" src/run.py
