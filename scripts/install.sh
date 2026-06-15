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

if [[ "$(python3 -c "import sys; print(sys.version_info >= (3, 10))")" != "True" ]]; then
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

# GPU(DGX) 환경이면 CPU onnxruntime를 onnxruntime-gpu로 교체한다.
# 두 패키지는 동일 import 네임스페이스를 제공하므로 공존하면 안 됨 → CPU 휠 제거 후 설치.
# ARM64에서 onnxruntime-gpu 휠을 찾지 못하면 CPU로 폴백(설치 실패로 전체 install 중단 방지).
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    log_info "NVIDIA GPU 감지 — onnxruntime-gpu로 교체 시도..."
    pip uninstall -y onnxruntime -q || true
    if ! pip install -q onnxruntime-gpu; then
        log_warn "onnxruntime-gpu 설치 실패(ARM64 휠 부재 가능) — CPU onnxruntime로 폴백합니다."
        pip install -q "onnxruntime==1.20.1"
    fi
fi

# 모델 디렉토리 확인
if [ ! -d "models" ] || [ -z "$(ls -A models/*.onnx 2>/dev/null)" ]; then
    log_warn "ONNX 모델이 없습니다. ./models/ 디렉토리에 모델을 배치하세요."
    log_info "예: yolo export model=yolo26n.pt format=onnx imgsz=640 half=True device=0"
fi

# 데이터 디렉토리 생성
log_info "데이터 디렉토리 생성 중..."
mkdir -p data/captures logs data/sample models/uploaded

# .env 파일 생성 (없는 경우)
if [ ! -f ".env" ]; then
    log_info ".env 파일 생성 중..."
    cp .env.example .env
    chmod 600 .env   # 비밀 파일 — 소유자만 읽기(group/other 노출 차단)
    log_warn ".env 파일을 편집하여 RTSP URL과 비밀번호를 설정하세요."
fi

# systemd 서비스 설치 (Linux만)
if [[ "$OSTYPE" == "linux-gnu"* ]] && command -v systemctl &> /dev/null; then
    SERVICE_NAME="ksi-cctv"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    INSTALL_DIR="$(pwd)"
    INSTALL_USER="$(whoami)"

    if [ -f "systemd/${SERVICE_NAME}.service" ]; then
        log_info "systemd 서비스 설치 중... (설치 경로: $INSTALL_DIR)"

        TEMP_SERVICE=$(mktemp)
        # ── 경로 일괄 치환 ──────────────────────────────────────────────────
        # 1. /opt/ksi-cctv 절대경로(ExecStart의 .venv·src/run.py, EnvironmentFile .env,
        #    ReadWritePaths 등 포함)를 실제 설치 경로로 일괄 치환
        # 2. WorkingDirectory / User / Group 치환
        # ExecStart(=.venv/bin/python src/run.py 데몬)와 EnvironmentFile(=.env)은 서비스 파일에
        # /opt/ksi-cctv 기준으로 이미 정의돼 있어 1번 치환으로 INSTALL_DIR에 맞춰진다(별도 override 불필요).
        sed "s|/opt/ksi-cctv|${INSTALL_DIR}|g" \
            "systemd/${SERVICE_NAME}.service" > "$TEMP_SERVICE"
        sed -i "s|WorkingDirectory=.*|WorkingDirectory=${INSTALL_DIR}|g" "$TEMP_SERVICE"
        sed -i "s|User=.*|User=${INSTALL_USER}|g" "$TEMP_SERVICE"
        sed -i "s|Group=.*|Group=${INSTALL_USER}|g" "$TEMP_SERVICE"
        # ProtectHome=true는 /home,/root를 서비스 mount namespace에서 빈 디렉터리로 마스킹한다.
        # 설치 경로가 그 아래이면 ExecStart(.venv/python)·WorkingDirectory를 찾지 못해 즉시
        # crash-loop가 되므로 read-only로 완화한다(ReadWritePaths가 logs/data 쓰기 경로는 다시 열어줌).
        case "$INSTALL_DIR" in
            /home/*|/root/*)
                sed -i "s|^ProtectHome=.*|ProtectHome=read-only|" "$TEMP_SERVICE"
                log_warn "설치 경로가 홈 디렉터리($INSTALL_DIR) 하위 — ProtectHome=read-only로 조정(true면 crash-loop). 운영은 /opt 권장."
                ;;
        esac

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

# ── 배포 게이트: 설치 후 readiness 점검 ────────────────────────────────────────
log_info "배포 readiness 점검 실행 중..."
if python3 scripts/check_environment.py; then
    log_info "환경 점검 통과."
else
    log_warn "환경 점검에서 일부 경고/오류 발생. 위 메시지를 확인하세요."
fi

# 완료
log_info "============================================"
log_info "KSI-CCTV 설치 완료!"
log_info "============================================"
log_info ""
log_info "다음 단계:"
log_info "  1. .env 파일 편집: nano .env"
log_info "     (KSI_CCTV_DASHBOARD_PASSWORD 를 반드시 변경하세요)"
log_info "  2. 카메라 설정 편집: nano config/cameras.json"
log_info "  3. 배포 readiness 점검: python3 scripts/healthcheck.py"
log_info "  4. 대시보드 실행: ./scripts/run_dashboard.sh"
log_info ""
log_info "문서: docs/"
log_info "============================================"
