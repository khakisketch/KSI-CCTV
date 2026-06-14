#!/usr/bin/env bash
# KSI-CCTV 테스트 스크립트 (Phase 5)
# 모든 테스트를 실행하고 코드 품질을 검증합니다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# 색상 정의
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 가상환경 활성화
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    log_error ".venv가 없습니다. 먼저 ./scripts/install.sh를 실행하세요."
    exit 1
fi

# 환경 점검
log_info "환경 점검 중..."
python scripts/check_environment.py 2>&1 | tail -3

# 단위 테스트 실행
log_info "단위 테스트 실행 중..."
python -m pytest tests/ -v --tb=short 2>&1 | tail -20

# 커버리지 측정
if python -c "import pytest_cov" 2>/dev/null; then
    log_info "커버리지 측정 중..."
    python -m pytest tests/ --cov=src --cov-report=term-missing --cov-report=html --cov-fail-under=60 2>&1 | tail -30
else
    log_warn "pytest-cov 미설치. 커버리지를 측정하려면 pip install -r requirements-dev.txt"
fi

# Lint 검사 (선택)
if command -v ruff &> /dev/null; then
    log_info "Ruff lint 검사 중..."
    ruff check src/ tests/ --ignore=E501,E402,B008 2>&1 | tail -20 || log_warn "Ruff 검사에서 경고 발견"
else
    log_warn "Ruff 미설치. pip install -r requirements-dev.txt로 설치"
fi

log_info "============================================"
log_info "✅ 모든 검증 완료"
log_info "============================================"
