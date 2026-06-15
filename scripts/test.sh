#!/usr/bin/env bash
# KSI-CCTV 테스트/품질 게이트 (Phase 5 / M5.2)
# 차단형: lint·테스트 실패 시 비0으로 종료한다(가짜 초록불 방지).
#   - ruff lint  : 차단 (위반 시 exit 1)
#   - pytest     : 차단 (실패 시 exit 1, 캐시 우회로 가짜 green 방지)
#   - coverage   : pytest-cov 있으면 리포트(현재 임계 미설정 — 측정 후 도입)
#   - mypy       : advisory(비차단) — 미타입 레거시 점진 도입
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 가상환경 활성화
if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    log_error ".venv가 없습니다. 먼저 ./scripts/install.sh를 실행하세요."
    exit 1
fi

# ── 1) Lint (차단) ────────────────────────────────────────────────────────────
RUFF_BIN="$(command -v ruff || true)"
if [ -z "$RUFF_BIN" ]; then
    log_error "ruff 미설치 — 'pip install -r requirements-dev.txt' 후 재실행 (lint 게이트 미실행)."
    exit 1
fi
log_info "Ruff lint (차단)..."
"$RUFF_BIN" check src/ tests/ scripts/

# ── 2) 단위 테스트 (차단, 캐시 우회) ──────────────────────────────────────────
# tail 파이프로 종료코드를 가리지 않는다(파이프 마지막 명령의 코드가 새므로 분리 실행).
log_info "pytest (차단, 캐시 우회)..."
python -B -m pytest tests/ -q -p no:cacheprovider

# ── 3) 커버리지 (pytest-cov 있을 때 차단) ─────────────────────────────────────
if python -c "import pytest_cov" 2>/dev/null; then
    # 실측 기준 src 전체 ~56%(핵심 로직 모듈은 ~90%, dashboard UI는 13%로 전체를 낮춤 —
    # UI는 브라우저 검증 영역). 회귀 방지용 보수적 하한 50%를 차단 게이트로 둔다.
    log_info "커버리지 (차단, 하한 50%)..."
    python -B -m pytest tests/ -q -p no:cacheprovider \
        --cov=src --cov-report=term-missing --cov-fail-under=50
else
    log_warn "pytest-cov 미설치 — 커버리지 게이트 미실행 (pip install -r requirements-dev.txt)."
fi

# ── 4) mypy (advisory, 비차단) ────────────────────────────────────────────────
MYPY_BIN="$(command -v mypy || true)"
if [ -n "$MYPY_BIN" ]; then
    log_info "mypy 타입 검사 (advisory, 비차단)..."
    "$MYPY_BIN" src/ || log_warn "mypy 타입 경고 발견 — 점진 해소 대상(게이트 차단 아님)."
else
    log_warn "mypy 미설치 — 타입 점검 생략 (pip install -r requirements-dev.txt)."
fi

log_info "============================================"
log_info "✅ 차단 게이트(lint + pytest) 통과"
log_info "============================================"
