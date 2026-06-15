#!/usr/bin/env bash
# KSI-CCTV Docker 컨테이너 / systemd 통합 진입점 스크립트
# 헬스체크 서버(8081)와 대시보드(8080)를 같은 프로세스 그룹에서 실행합니다.
# systemd ExecStart 래퍼로도 사용 — &(백그라운드) 패턴 없이 헬스서버를 같은 쉘에서 기동.
set -euo pipefail

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

# ── 비밀번호 가드 ──────────────────────────────────────────────────────────────
# 기본값(change-me)으로 기동하면 CCTV 대시보드가 약한 비밀번호로 외부 노출됨.
# .env 또는 환경변수로 반드시 변경하도록 기동 자체를 거부한다.
DASHBOARD_PASSWORD="${KSI_CCTV_DASHBOARD_PASSWORD:-}"
if [ -z "$DASHBOARD_PASSWORD" ] || [ "$DASHBOARD_PASSWORD" = "change-me" ]; then
    echo "[ERROR] KSI_CCTV_DASHBOARD_PASSWORD 가 미설정이거나 기본값(change-me)입니다." >&2
    echo "[ERROR] .env 파일에 강력한 비밀번호를 설정한 후 다시 시작하세요." >&2
    exit 1
fi

# ── 런타임 디렉토리 확인 ────────────────────────────────────────────────────────
echo "[INFO] KSI-CCTV 컨테이너/서비스 시작..."
mkdir -p logs data/captures models/uploaded

# ── 헬스체크 서버를 같은 쉘 내 백그라운드로 시작 ────────────────────────────────
# systemd: ExecStartPre(&) 대신 ExecStart 래퍼에서 기동 → cgroup 생명주기 공유됨.
# 이 프로세스(bash)가 종료되면 하위 프로세스도 함께 종료되어 고아 방지.
echo "[INFO] 헬스체크 서버 시작 (포트 8081)..."
# start_health_server()는 daemon thread를 띄우고 즉시 반환하므로, 이 백그라운드
# 프로세스가 곧장 종료되면 daemon thread도 함께 죽는다. 따라서 메인 스레드를
# 블로킹 루프로 살려 둬야 헬스서버가 유지된다(이전엔 import time만 있고 블록이 없어 즉사).
"$PYTHON" -c "
import time
from src.utils.health import start_health_server
start_health_server(host='0.0.0.0', port=8081)
while True:
    time.sleep(3600)
" &
HEALTH_PID=$!

# 헬스체크 서버 초기화 대기
sleep 2

# 헬스체크 서버 기동 확인
if ! kill -0 $HEALTH_PID 2>/dev/null; then
    echo "[WARN] 헬스체크 서버가 예상보다 일찍 종료됐습니다. 대시보드는 계속 시작합니다." >&2
fi

echo "[INFO] 대시보드 시작 (포트 8080)..."
# exec로 PID 1을 streamlit으로 교체 → 컨테이너 시그널(SIGTERM) 수신
exec "$PYTHON" -m streamlit run src/dashboard/app.py \
    --server.port=8080 \
    --server.address=0.0.0.0 \
    --server.headless=true
