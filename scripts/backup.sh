#!/usr/bin/env bash
# KSI-CCTV 설정·업로드모델 백업/복구 (batch A — 무인운영 DATA-안전 자동화)
#
# 사용:
#   scripts/backup.sh                 # 백업 생성 (backups/ksi-cctv-config-<ts>.tar.gz)
#   scripts/backup.sh backup [--with-logs]
#   scripts/backup.sh list            # 기존 백업 목록
#   scripts/backup.sh restore <tarball>   # 복구(확인 후) + check_environment 재검증
#
# 백업 대상: config/(*.yaml·cameras.json)·models/uploaded/·model_profiles.yaml.
# **제외(의도적): .env(시크릿 — 보안저장소에서 별도 관리)·data/captures(PII 이미지)·logs(기본 제외).**
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

BACKUP_DIR="backups"

do_backup() {
    local with_logs="${1:-}"
    mkdir -p "$BACKUP_DIR"
    local ts; ts="$(date +%Y%m%d-%H%M%S)"
    local out="$BACKUP_DIR/ksi-cctv-config-${ts}.tar.gz"

    # 포함 대상 — 존재하는 것만(없으면 건너뜀).
    local items=()
    [ -d config ] && items+=("config")
    [ -d models/uploaded ] && items+=("models/uploaded")
    if [ "$with_logs" = "--with-logs" ]; then
        [ -d logs ] && items+=("logs")
        log_warn "logs 포함 — 운영 로그에 PII가 있을 수 있으니 백업 보관에 주의하세요."
    fi
    if [ ${#items[@]} -eq 0 ]; then
        log_error "백업할 대상이 없습니다(config/ 등 미존재)."; exit 1
    fi

    # 시크릿/대용량/민감 데이터는 제외(.env·captures·sample 영상).
    tar --exclude='.env' --exclude='*/.env' \
        --exclude='data/captures' --exclude='config/cameras.local.json' \
        -czf "$out" "${items[@]}"
    chmod 600 "$out"   # 설정/모델 백업 — 소유자만 접근

    log_info "백업 생성: $out ($(du -h "$out" | cut -f1))"
    log_warn ".env(시크릿)는 백업에 포함되지 않습니다 — 보안저장소에서 별도 관리하세요."
    echo "$out"
}

do_list() {
    if [ -d "$BACKUP_DIR" ] && ls "$BACKUP_DIR"/ksi-cctv-config-*.tar.gz >/dev/null 2>&1; then
        ls -lh "$BACKUP_DIR"/ksi-cctv-config-*.tar.gz
    else
        log_info "백업이 없습니다($BACKUP_DIR/)."
    fi
}

do_restore() {
    local tarball="${1:-}"
    if [ -z "$tarball" ] || [ ! -f "$tarball" ]; then
        log_error "복구할 tarball 경로를 지정하세요: scripts/backup.sh restore <tarball>"; exit 1
    fi
    log_warn "복구는 현재 config/·models/uploaded/를 백업 내용으로 덮어씁니다."
    echo "포함 내용:"; tar -tzf "$tarball" | head -20
    read -r -p "계속하시겠습니까? (yes/no) " ans
    if [ "$ans" != "yes" ]; then log_info "복구 취소."; exit 0; fi

    # 복구 전 현재 상태를 안전 백업(롤백 가능하게).
    log_info "복구 전 현재 상태를 백업합니다..."
    do_backup >/dev/null || true

    tar -xzf "$tarball" -C .
    log_info "복구 완료. 환경 재검증 실행..."
    if [ -x ".venv/bin/python" ]; then
        .venv/bin/python scripts/check_environment.py || log_warn "환경 점검에서 경고/오류 — 위 메시지 확인."
    else
        python3 scripts/check_environment.py || log_warn "환경 점검에서 경고/오류 — 위 메시지 확인."
    fi
}

cmd="${1:-backup}"
case "$cmd" in
    backup) do_backup "${2:-}";;
    list)   do_list;;
    restore) do_restore "${2:-}";;
    *) log_error "알 수 없는 명령: $cmd (backup|list|restore)"; exit 1;;
esac
