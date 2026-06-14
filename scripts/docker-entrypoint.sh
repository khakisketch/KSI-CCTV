#!/usr/bin/env bash
# KSI-CCTV Docker 컨테이너 진입점 스크립트
# 헬스체크 서버와 대시보드를 동시에 실행합니다.
set -e

echo "[INFO] KSI-CCTV Docker 컨테이너 시작..."

# 헬스체크 서버를 백그라운드로 시작
echo "[INFO] 헬스체크 서버 시작 (포트 8081)..."
python -c "from src.utils.health import start_health_server; start_health_server(host='0.0.0.0', port=8081)" &

# 잠시 대기 (헬스체크 서버 초기화)
sleep 2

# 대시보드 시작 (메인 프로세스)
echo "[INFO] 대시보드 시작 (포트 8080)..."
exec python -m streamlit run src/dashboard/app.py \
    --server.port=8080 \
    --server.address=0.0.0.0 \
    --server.headless=true
