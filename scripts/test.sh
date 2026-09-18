#!/usr/bin/env bash
# KSI-CCTV 테스트/품질 게이트 (Phase 5 / M5.2)
# 차단형: lint·테스트 실패 시 비0으로 종료한다(가짜 초록불 방지).
#   - ruff lint  : 차단 (위반 시 exit 1)
#   - pytest     : 차단 (실패 시 exit 1, 캐시 우회로 가짜 green 방지)
#   - shell syntax: 배포/검증 스크립트의 bash 파싱 오류 차단
#   - coverage   : pytest-cov 있으면 50% 하한으로 차단
#                  (커버리지 하한의 유일한 집행점. release gate는 무실패+skip accounting만
#                   주장하고 커버리지는 측정하지 않는다 — scripts/verify_release_candidate.sh [1/8] 참고)
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
"$RUFF_BIN" check src/ tests/ scripts/ installer/

# ── 2) Shell syntax (차단) ───────────────────────────────────────────────────
log_info "Bash syntax (차단)..."
for script in scripts/*.sh; do
    bash -n "$script"
done

# ── 3) 단위 테스트/커버리지 (차단, 캐시 우회) ─────────────────────────────────
if python -c "import pytest_cov" 2>/dev/null; then
    log_info "pytest + 커버리지 (차단, 하한 50%)..."
    python -B -m pytest tests/ -q -p no:cacheprovider \
        --cov=src --cov-report=term-missing --cov-fail-under=50
else
    log_warn "pytest-cov 미설치 — 커버리지 없이 pytest만 실행합니다 (pip install -r requirements-dev.txt 권장)."
    python -B -m pytest tests/ -q -p no:cacheprovider
fi

# ── 4) mypy (advisory, 비차단) ────────────────────────────────────────────────
MYPY_BIN="$(command -v mypy || true)"
if [ -n "$MYPY_BIN" ]; then
    log_info "mypy 타입 검사 (advisory, 비차단)..."
    "$MYPY_BIN" src/api src/storage installer || log_warn "mypy 타입 경고 발견 — 점진 해소 대상(게이트 차단 아님)."
else
    log_warn "mypy 미설치 — 타입 점검 생략 (pip install -r requirements-dev.txt)."
fi

log_info "============================================"
log_info "✅ 차단 게이트(lint + pytest) 통과"
log_info "Release candidate 전체 게이트: bash scripts/verify_release_candidate.sh --strict"
log_info "============================================"
