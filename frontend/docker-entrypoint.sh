#!/bin/sh
set -eu

if [ "${KSI_CCTV_DEV_AUTH_BYPASS:-}" = "1" ]; then
  echo "KSI_CCTV_DEV_AUTH_BYPASS is forbidden in the production web image." >&2
  exit 78
fi

KSI_CCTV_WEB_AUTH_DIR=/var/lib/ksi-cctv-web
export KSI_CCTV_WEB_AUTH_DIR

exec node server.js
