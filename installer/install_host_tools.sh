#!/bin/bash
set -euo pipefail

ROOT=/
if [ "${1:-}" = "--root" ]; then
    if [ "$#" -ne 2 ] || [[ "$2" != /* ]]; then
        printf 'error: --root requires one absolute path\n' >&2
        exit 2
    fi
    ROOT=${2%/}
    [ -n "$ROOT" ] || ROOT=/
elif [ "$#" -ne 0 ]; then
    printf 'Usage: %s [--root /absolute/test-root]\n' "$0" >&2
    exit 2
fi

if [ "$ROOT" = / ] && [ "$(id -u)" -ne 0 ]; then
    printf 'error: production host-tool installation requires root\n' >&2
    exit 2
fi

SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT=$(cd -- "$SOURCE/.." && pwd)
INSTALLER_ROOT="$ROOT/opt/ksi-cctv/installer"
CONFIG_ROOT="$ROOT/opt/ksi-cctv/config"

install -d -m 0755 "$INSTALLER_ROOT" "$INSTALLER_ROOT/systemd" "$CONFIG_ROOT" "$ROOT/usr/local/sbin"
install -m 0644 \
    "$SOURCE/ksi_cctv_appliance.py" \
    "$SOURCE/release_contracts.py" \
    "$SOURCE/verify_systemd_unit.py" \
    "$SOURCE/activation_health.py" \
    "$SOURCE/transaction_guard.py" \
    "$SOURCE/compose.appliance.yaml.template" \
    "$SOURCE/Caddyfile" \
    "$SOURCE/requirements.in" \
    "$SOURCE/requirements.txt" \
    "$SOURCE/wheelhouse.sha256" \
    "$INSTALLER_ROOT/"
install -m 0644 "$PROJECT/config/mediamtx.yml" "$CONFIG_ROOT/mediamtx.yml"
install -m 0644 "$SOURCE/systemd/ksi-cctv-compose.service" "$INSTALLER_ROOT/systemd/ksi-cctv-compose.service"
install -m 0755 "$SOURCE/ksi-cctv-appliance-launcher" "$ROOT/usr/local/sbin/ksi-cctv-appliance"
