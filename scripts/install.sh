#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv}"

info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

cd "${ROOT_DIR}"

info "KSI-CCTV install scaffold"
info "Project root: ${ROOT_DIR}"

command -v "${PYTHON_BIN}" >/dev/null 2>&1 || fail "Python not found: ${PYTHON_BIN}"

if [ ! -d "${VENV_DIR}" ]; then
  info "Creating virtual environment: ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
else
  info "Virtual environment already exists: ${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

info "Upgrading pip tooling"
python -m pip install --upgrade pip setuptools wheel

info "Installing Python dependencies from requirements.txt"
python -m pip install -r requirements.txt

info "Ensuring runtime directories"
mkdir -p logs data/captures models/uploaded

if [ ! -f ".env" ]; then
  warn ".env does not exist. Create it from .env.example and replace placeholders before production use:"
  warn "  cp .env.example .env"
else
  info ".env exists; not overwriting."
fi

if [ ! -f "models/yolo26.onnx" ]; then
  warn "models/yolo26.onnx not found. Prepare the model before running the service:"
  warn "  python3 scripts/prepare_yolo26.py --model yolo26n.pt"
else
  info "Found models/yolo26.onnx"
fi

info "Running local healthcheck"
python scripts/healthcheck.py || warn "Healthcheck reported issues. Review output above before service rollout."

cat <<'EOF'

Systemd template is available at:
  systemd/ksi-cctv.service

This installer does not run sudo, install units, or start services.
For a site deployment, review paths/users in the unit, then install manually, e.g.:
  sudo install -D -m 0644 systemd/ksi-cctv.service /etc/systemd/system/ksi-cctv.service
  sudo mkdir -p /etc/ksi-cctv
  sudo install -m 0600 .env /etc/ksi-cctv/ksi-cctv.env
  sudo systemctl daemon-reload
  sudo systemctl enable --now ksi-cctv

Rollback example:
  sudo systemctl disable --now ksi-cctv
  sudo rm -f /etc/systemd/system/ksi-cctv.service
  sudo systemctl daemon-reload

EOF

info "Install scaffold complete"
