#!/usr/bin/env bash
# Runner-neutral blocking software gate for an Ubuntu ARM64 Appliance release candidate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STRICT=0
WITH_BROWSER=1
ARTIFACT_DIR="${KSI_CCTV_ARTIFACT_DIR:-$ROOT/reports/release-candidate}"
case "$ARTIFACT_DIR" in
    /*) ;;
    *) ARTIFACT_DIR="$ROOT/$ARTIFACT_DIR" ;;
esac
ATTESTATION_PATH="${KSI_CCTV_ATTESTATION_PATH:-$ARTIFACT_DIR/release-attestation.json}"
case "$ATTESTATION_PATH" in
    /*) ;;
    *) ATTESTATION_PATH="$ROOT/$ATTESTATION_PATH" ;;
esac
ATTESTATION_STARTED=0

usage() {
    cat <<'EOF'
Usage: scripts/verify_release_candidate.sh [--strict] [--skip-browser]

Runs the reproducible software gate used by an internal or self-hosted CI runner:
Ruff, Python tests, shell syntax, Compose rendering, frontend lint/tests/build, and Playwright.

--strict         Require shellcheck and mypy in addition to the portable baseline.
--skip-browser   Skip Playwright only for a developer fast path; never use for release evidence.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --strict) STRICT=1; shift ;;
        --skip-browser) WITH_BROWSER=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'error: unknown option %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$STRICT" -eq 1 ] && [ "$WITH_BROWSER" -eq 0 ]; then
    printf '%s\n' 'error: --skip-browser is incompatible with --strict' >&2
    exit 2
fi

cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    PYTHON="${PYTHON_FALLBACK:-python3}"
fi
command -v "$PYTHON" >/dev/null 2>&1 || { printf 'error: Python runtime not found\n' >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { printf 'error: docker compose is required\n' >&2; exit 2; }
command -v npm >/dev/null 2>&1 || { printf 'error: npm is required\n' >&2; exit 2; }
if [ -n "${KSI_CCTV_ATTESTATION_PRIVATE_KEY:-}" ]; then
    printf '%s\n' 'error: run the gate without signing credentials; sign the finalized attestation in a separate trusted job' >&2
    exit 2
fi

mkdir -p "$ARTIFACT_DIR"
ATTESTATION_COMMAND="bash scripts/verify_release_candidate.sh"
if [ "$STRICT" -eq 1 ]; then
    ATTESTATION_COMMAND="$ATTESTATION_COMMAND --strict"
fi
if [ "$WITH_BROWSER" -eq 0 ]; then
    ATTESTATION_COMMAND="$ATTESTATION_COMMAND --skip-browser"
fi
attestation_init_args=(
    initialize --output "$ATTESTATION_PATH" --root "$ROOT" --command "$ATTESTATION_COMMAND"
)
if [ "$STRICT" -eq 1 ]; then
    attestation_init_args+=(--strict)
fi
"$PYTHON" scripts/release_attestation.py "${attestation_init_args[@]}"
ATTESTATION_STARTED=1

finalize_attestation() {
    status=$?
    trap - EXIT
    if [ "$ATTESTATION_STARTED" -eq 1 ]; then
        if [ "$status" -eq 0 ]; then
            attestation_args=(
                finalize --output "$ATTESTATION_PATH" --root "$ROOT" --passed
            )
            for evidence in \
                "pytest=$ARTIFACT_DIR/pytest-summary.json" \
                "skip=$ARTIFACT_DIR/skip-summary.json" \
                "pip=$ARTIFACT_DIR/pip-audit-summary.json" \
                "npm=$ARTIFACT_DIR/npm-audit-summary.json" \
                "playwright=$ARTIFACT_DIR/playwright-summary.json"; do
                evidence_path="${evidence#*=}"
                if [ -e "$evidence_path" ]; then
                    attestation_args+=(--evidence "$evidence")
                fi
            done
            "$PYTHON" scripts/release_attestation.py "${attestation_args[@]}" || status=$?
        else
            "$PYTHON" scripts/release_attestation.py \
                finalize --output "$ATTESTATION_PATH" --root "$ROOT" --failed >/dev/null 2>&1 || true
        fi
    fi
    exit "$status"
}
trap finalize_attestation EXIT

if [ "$STRICT" -eq 1 ] && [ -n "$(git status --porcelain)" ]; then
    printf '%s\n' 'error: --strict release evidence requires a clean, frozen Git worktree' >&2
    exit 2
fi
if [ "$STRICT" -eq 1 ] && [ -n "$(git ls-files --others --ignored --exclude-standard -- src frontend/src frontend/public)" ]; then
    printf '%s\n' 'error: production Docker contexts contain ignored files' >&2
    exit 2
fi

{
    git rev-parse HEAD
    uname -a
    "$PYTHON" --version
    node --version
    npm --version
    docker --version
    docker compose version
} > "$ARTIFACT_DIR/runner-metadata.txt"

printf '%s\n' '[1/9] Python lint and test suite'
"$PYTHON" -m ruff check src tests scripts installer
"$PYTHON" -B -m pytest tests -q -p no:cacheprovider -ra \
    --junitxml "$ARTIFACT_DIR/pytest.xml"
"$PYTHON" scripts/check_test_skips.py \
    --junit "$ARTIFACT_DIR/pytest.xml" \
    --json-out "$ARTIFACT_DIR/skip-summary.json" \
    --pytest-json-out "$ARTIFACT_DIR/pytest-summary.json"

printf '%s\n' '[2/9] Shell syntax'
for script in scripts/*.sh; do
    bash -n "$script"
done
bash -n installer/ksi-cctv-appliance-launcher
bash -n installer/install_host_tools.sh

if command -v shellcheck >/dev/null 2>&1; then
    printf '%s\n' '[3/9] ShellCheck'
    shellcheck --shell=bash scripts/*.sh installer/ksi-cctv-appliance-launcher installer/install_host_tools.sh
elif [ "$STRICT" -eq 1 ]; then
    printf 'error: shellcheck is required in --strict mode\n' >&2
    exit 2
else
    printf '%s\n' 'warning: shellcheck unavailable; use --strict on the release runner'
fi

if "$PYTHON" -m mypy --version >/dev/null 2>&1; then
    printf '%s\n' '[4/9] Core type check'
    if ! "$PYTHON" -m mypy --follow-imports=skip \
        src/api/app.py src/api/auth.py src/api/service.py src/run.py src/incidents/store.py \
        src/storage/schema.py src/storage/sqlite_events.py src/storage/sqlite_observations.py \
        installer/activation_health.py installer/transaction_guard.py installer/ksi_cctv_appliance.py installer/release_builder.py \
        installer/release_contracts.py installer/verify_systemd_unit.py \
        scripts/release_attestation.py scripts/bundle_attestation.py; then
        if [ "$STRICT" -eq 1 ]; then
            printf 'error: mypy findings are blocking in --strict mode\n' >&2
            exit 1
        fi
        printf '%s\n' 'warning: mypy findings recorded for follow-up'
    fi
elif [ "$STRICT" -eq 1 ]; then
    printf 'error: mypy is required in --strict mode\n' >&2
    exit 2
else
    printf '%s\n' 'warning: mypy unavailable; install requirements-dev.txt on the release runner'
fi

printf '%s\n' '[5/9] Compose contracts'
# This deterministic key is only scoped to Compose interpolation; it is not a deployment secret.
COMPOSE_RENDER_CAPTURE_KEY='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
KSI_CCTV_CAPTURE_KEY="$COMPOSE_RENDER_CAPTURE_KEY" docker compose -f docker-compose.yml config --quiet
KSI_CCTV_CAPTURE_KEY="$COMPOSE_RENDER_CAPTURE_KEY" docker compose -f docker-compose.yml -f docker-compose.gpu.yml config --quiet
KSI_CCTV_CAPTURE_KEY="$COMPOSE_RENDER_CAPTURE_KEY" docker compose -f docker-compose.yml -f docker-compose.diagnostics.yml config --quiet
"$PYTHON" installer/verify_systemd_unit.py

printf '%s\n' '[6/9] Python dependency audit'
if "$PYTHON" -m pip_audit --version >/dev/null 2>&1; then
    set +e
    "$PYTHON" -m pip_audit -r requirements.txt --format json > "$ARTIFACT_DIR/pip-audit-runtime.json"
    runtime_audit_status=$?
    "$PYTHON" -m pip_audit -r requirements-onvif.txt --format json > "$ARTIFACT_DIR/pip-audit-onvif.json"
    onvif_audit_status=$?
    "$PYTHON" -m pip_audit --no-deps -r installer/requirements.txt --format json > "$ARTIFACT_DIR/pip-audit-installer.json"
    installer_audit_status=$?
    set -e
    if [ "$runtime_audit_status" -gt 1 ] || [ "$onvif_audit_status" -gt 1 ] || [ "$installer_audit_status" -gt 1 ]; then
        printf 'error: pip-audit could not complete\n' >&2
        exit 2
    fi
    set +e
    "$PYTHON" "$ROOT/scripts/check_pip_audit.py" \
        --audit "$ARTIFACT_DIR/pip-audit-runtime.json" \
        --audit "$ARTIFACT_DIR/pip-audit-onvif.json" \
        --audit "$ARTIFACT_DIR/pip-audit-installer.json" \
        --json-out "$ARTIFACT_DIR/pip-audit-summary.json"
    pip_audit_check_status=$?
    set -e
    if [ "$pip_audit_check_status" -ne 0 ]; then
        printf 'error: Python dependency audit findings are blocking\n' >&2
        exit 1
    fi
elif [ "$STRICT" -eq 1 ]; then
    printf 'error: pip-audit is required in --strict mode\n' >&2
    exit 2
else
    printf '%s\n' 'warning: pip-audit unavailable; use --strict on the release runner'
fi

printf '%s\n' '[7/9] Frontend unit/build gate'
(
    cd frontend
    npm ci --no-audit
    set +e
    npm audit --omit=dev --json > "$ARTIFACT_DIR/npm-audit.json"
    audit_status=$?
    set -e
    if [ "$audit_status" -gt 1 ]; then
        printf 'error: npm audit could not complete\n' >&2
        exit "$audit_status"
    fi
    set +e
    "$PYTHON" "$ROOT/scripts/check_npm_audit.py" \
        --audit "$ARTIFACT_DIR/npm-audit.json" \
        --json-out "$ARTIFACT_DIR/npm-audit-summary.json"
    npm_audit_check_status=$?
    set -e
    if [ "$npm_audit_check_status" -eq 2 ]; then
        printf 'error: production npm audit evidence is incomplete\n' >&2
        exit 2
    elif [ "$npm_audit_check_status" -ne 0 ]; then
        if [ "$STRICT" -eq 1 ]; then
            printf 'error: production npm audit findings are blocking in --strict mode\n' >&2
            exit 1
        fi
        printf '%s\n' 'warning: production npm audit findings recorded for strict-gate remediation'
    fi
    npm run lint
    npm test
    npm run test:auth-boundary
    npm run test:local-auth-reset
    npm run build
    npm run test:standalone
    if [ "$WITH_BROWSER" -eq 1 ]; then
        npx playwright install chromium
        PLAYWRIGHT_JSON_OUTPUT_DIR="$ARTIFACT_DIR" \
            PLAYWRIGHT_JSON_OUTPUT_NAME="playwright.json" \
            CI=1 npx playwright test --reporter=line,json
        "$PYTHON" "$ROOT/scripts/check_playwright_report.py" \
            --report "$ARTIFACT_DIR/playwright.json" \
            --json-out "$ARTIFACT_DIR/playwright-summary.json"
    fi
)

printf '%s\n' '[9/9] Release contract regression coverage included in Python suite'
# 실행 모드를 그대로 말한다. 두 경우에 같은 문구를 내면 보관된 로그만 보고
# strict 실행을 개발용 실행으로 오해한다(증적 판독 오류).
if [ "$STRICT" -eq 1 ]; then
    printf '%s\n' 'PASS: --strict gate completed; retain this log and reports/release-candidate/ as release evidence.'
else
    printf '%s\n' 'PASS: runner-neutral developer baseline completed; only --strict is release evidence.'
fi
