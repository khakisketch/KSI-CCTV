#!/usr/bin/env bash
# Validate a signed Appliance bundle before it reaches a target device.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    PYTHON="${PYTHON_FALLBACK:-python3}"
fi
command -v "$PYTHON" >/dev/null 2>&1 || { printf 'error: Python runtime not found\n' >&2; exit 2; }
RELEASE_DIR=""
PROFILE=""
PUBLIC_KEY=""
ATTESTATION_PUBLIC_KEY=""
SITE_ADDRESS="bundle-verify.local"
RUN_SYSTEMD_ANALYZE=0

usage() {
    cat <<'EOF'
Usage: scripts/verify_appliance_bundle.sh --release-dir DIR --profile PROFILE --public-key FILE --attestation-public-key FILE [options]

Required:
  --release-dir DIR       Signed offline Appliance bundle directory
  --profile PROFILE       Supported Appliance profile (dgx-spark-arm64)
  --public-key FILE       Trusted Ed25519 release public key outside the bundle
  --attestation-public-key FILE
                          Trusted CI Ed25519 attestation key outside the bundle

Options:
  --site-address NAME     Safe hostname used only to render Compose (default: bundle-verify.local)
  --systemd-analyze       Also run host systemd-analyze verification (staging hosts only)
  -h, --help              Show this help

This is a preflight gate. It verifies signature/hash/image mapping, Compose rendering, and
the systemd contract. It never loads images, starts containers, or claims target qualification.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --release-dir) RELEASE_DIR="${2:?missing value for --release-dir}"; shift 2 ;;
        --profile) PROFILE="${2:?missing value for --profile}"; shift 2 ;;
        --public-key) PUBLIC_KEY="${2:?missing value for --public-key}"; shift 2 ;;
        --attestation-public-key) ATTESTATION_PUBLIC_KEY="${2:?missing value for --attestation-public-key}"; shift 2 ;;
        --site-address) SITE_ADDRESS="${2:?missing value for --site-address}"; shift 2 ;;
        --systemd-analyze) RUN_SYSTEMD_ANALYZE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'error: unknown option %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

for value in RELEASE_DIR PROFILE PUBLIC_KEY ATTESTATION_PUBLIC_KEY; do
    if [ -z "${!value}" ]; then
        printf 'error: --%s is required\n' "$(printf '%s' "$value" | tr '[:upper:]_' '[:lower:]-')" >&2
        usage >&2
        exit 2
    fi
done

UNIT_FILE="$RELEASE_DIR/systemd/ksi-cctv-compose.service"

if ! command -v docker >/dev/null 2>&1; then
    printf 'error: docker compose is required to render the bundle Compose file\n' >&2
    exit 2
fi

"$PYTHON" "$ROOT/installer/ksi_cctv_appliance.py" verify \
    --release-dir "$RELEASE_DIR" \
    --profile "$PROFILE" \
    --public-key "$PUBLIC_KEY" \
    --attestation-public-key "$ATTESTATION_PUBLIC_KEY"
"$PYTHON" "$ROOT/installer/verify_systemd_unit.py" --unit "$UNIT_FILE"

TEMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TEMP_DIR"; }
trap cleanup EXIT
SAFE_ENV="$TEMP_DIR/site.env"
SAFE_COMPOSE="$TEMP_DIR/compose.yaml"
cat >"$SAFE_ENV" <<'EOF'
KSI_CCTV_API_KEY=bundle-verify-api-key-not-for-production
KSI_CCTV_WEB_PASSWORD=bundle-verify-password-not-for-production
KSI_CCTV_WEB_SECRET=bundle-verify-session-secret-not-for-production
KSI_CCTV_CAPTURE_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
KSI_CCTV_SITE_ADDRESS=bundle-verify.local
EOF

# Compose's Appliance env_file is intentionally absolute. Replace it only in a temporary
# rendered copy so the bundle itself and its site secrets remain untouched.
"$PYTHON" - "$RELEASE_DIR/compose.yaml" "$SAFE_COMPOSE" "$SAFE_ENV" <<'PY'
from pathlib import Path
import sys

source, output, safe_env = map(Path, sys.argv[1:])
text = source.read_text(encoding="utf-8")
output.write_text(
    text.replace("/etc/ksi-cctv/engine.env", str(safe_env)).replace(
        "/etc/ksi-cctv/web.env", str(safe_env)
    ),
    encoding="utf-8",
)
PY

KSI_CCTV_SITE_ADDRESS="$SITE_ADDRESS" docker compose -f "$SAFE_COMPOSE" config --quiet

if [ "$RUN_SYSTEMD_ANALYZE" -eq 1 ] && command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify "$UNIT_FILE"
fi

printf 'PASS: signed bundle preflight completed; target install/boot qualification remains required.\n'
