#!/usr/bin/env bash
# KSI-CCTV 설치 스크립트 (Phase 4)
# Ubuntu/Debian 기반 시스템에서 KSI-CCTV를 설치하고 systemd 서비스로 등록합니다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Python 버전 확인
log_info "Python 버전 확인 중..."
if ! command -v python3 &> /dev/null; then
    log_error "Python 3가 설치되어 있지 않습니다. Python 3.10+을 설치하세요."
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
log_info "Python 버전: $PYTHON_VERSION"

if [[ "$(echo "$PYTHON_VERSION >= 3.10" | python3 -c "import sys; print(sys.version_info >= (3, 10))")" != "True" ]]; then
    log_error "Python 3.10 이상이 필요합니다. 현재: $PYTHON_VERSION"
    exit 1
fi

# 시스템 의존성 설치
log_info "시스템 의존성 설치 중..."
if command -v apt-get &> /dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3-venv python3-pip python3-dev build-essential libopencv-dev
elif command -v yum &> /dev/null; then
    sudo yum install -y python3-devel python3-pip gcc gcc-c++ make
fi

# 가상환경 생성
if [ ! -d ".venv" ]; then
    log_info "Python 가상환경 생성 중..."
    python3 -m venv .venv
fi

# pip 업그레이드 및 의존성 설치
log_info "Python 의존성 설치 중..."
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 모델 디렉토리 확인
if [ ! -d "models" ] || [ -z "$(ls -A models/*.onnx 2>/dev/null)" ]; then
    log_warn "ONNX 모델이 없습니다. ./models/ 디렉토리에 모델을 배치하세요."
    log_info "예: yolo export model=yolo26n.pt format=onnx imgsz=640 half=True device=0"
fi

# 데이터 디렉토리 생성
log_info "데이터 디렉토리 생성 중..."
mkdir -p data/captures logs data/sample

# .env 파일 생성 (없는 경우)
if [ ! -f ".env" ]; then
    log_info ".env 파일 생성 중..."
    cp .env.example .env
    log_warn ".env 파일을 편집하여 RTSP URL과 비밀번호를 설정하세요."
fi

# systemd 서비스 설치 (Linux만)
if [[ "$OSTYPE" == "linux-gnu"* ]] && command -v systemctl &> /dev/null; then
    SERVICE_NAME="ksi-cctv"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

    if [ -f "systemd/${SERVICE_NAME}.service" ]; then
        log_info "systemd 서비스 설치 중..."

        # 서비스 파일에서 경로 치환
        TEMP_SERVICE=$(mktemp)
        sed "s|WorkingDirectory=.*|WorkingDirectory=$(pwd)|g" \
            systemd/${SERVICE_NAME}.service > "$TEMP_SERVICE"
        sed -i "s|User=.*|User=$(whoami)|g" "$TEMP_SERVICE"
        sed -i "s|ExecStart=.*|ExecStart=$(pwd)/.venv/bin/streamlit run src/dashboard/app.py --server.port=8080 --server.address=0.0.0.0|g" "$TEMP_SERVICE"

        sudo cp "$TEMP_SERVICE" "$SERVICE_FILE"
        sudo systemctl daemon-reload
        sudo systemctl enable ${SERVICE_NAME}.service

        rm -f "$TEMP_SERVICE"
        log_info "systemd 서비스 등록 완료: $SERVICE_FILE"
        log_info "시작: sudo systemctl start $SERVICE_NAME"
        log_info "상태: sudo systemctl status $SERVICE_NAME"
    else
        log_warn "systemd/${SERVICE_NAME}.service 파일이 없습니다."
    fi
else
    log_warn "systemd를 사용할 수 없는 환경입니다. 수동으로 실행하세요."
fi

# 완료
log_info "============================================"
log_info "KSI-CCTV 설치 완료!"
log_info "============================================"
log_info ""
log_info "다음 단계:"
log_info "  1. .env 파일 편집: nano .env"
log_info "  2. 카메라 설정 편집: nano config/cameras.json"
log_info "  3. 대시보드 실행: ./scripts/run_dashboard.sh"
log_info "  4. 헬스체크 서버: python -c 'from src.utils.health import start_health_server; start_health_server()'"
log_info ""
log_info "문서: docs/"
log_info "============================================"
