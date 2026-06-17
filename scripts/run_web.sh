#!/usr/bin/env bash
# 로컬에서 웹 콘솔 전체 스택을 한 번에 띄워 UI/UX를 직접 확인한다.
#   엔진 API(FastAPI) + 영상 게이트웨이(MediaMTX, docker 있으면) + Next.js 웹 콘솔
#
# 사용:
#   bash scripts/run_web.sh                 # http://localhost:3000 (로그인 비번: 아래 출력)
#   KSI_CCTV_DEMO=1 bash scripts/run_web.sh # 데모 이벤트/캡처 시드(빈 로그일 때만) — 화면을 채워서 확인
#   KSI_CCTV_WEB_PASSWORD=강한비번 bash scripts/run_web.sh
# 종료: Ctrl+C (모든 프로세스/컨테이너 정리)
#
# 참고: 헤드리스 Chromium은 H.264 미탑재라 라이브 영상이 안 보일 수 있으나, 실 브라우저
# (Chrome/Edge/Safari/Firefox)에서는 정상 재생된다. 원격은 Tailscale IP:3000으로 접속.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export KSI_CCTV_API_KEY="${KSI_CCTV_API_KEY:-local-dev-key}"
export KSI_CCTV_WEB_PASSWORD="${KSI_CCTV_WEB_PASSWORD:-ksicctv}"
ENGINE_PORT="${KSI_CCTV_HEALTH_PORT:-8081}"
WEB_PORT="${KSI_CCTV_WEB_PORT:-3000}"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

pids=()
cleanup() {
  echo; echo "[run_web] 종료 중..."
  for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done
  docker rm -f ksi-cctv-gateway-dev >/dev/null 2>&1 || true
}
trap cleanup INT TERM EXIT

# ── 데모 시드(선택) — 로그가 비어 있을 때만 ──────────────────────────────────────
if [ "${KSI_CCTV_DEMO:-}" = "1" ] && [ ! -s logs/alerts.log ]; then
  echo "[run_web] 데모 데이터 시드..."
  mkdir -p logs
  "$PY" - <<'PY' || echo "[run_web] 데모 시드 생략(의존성 부족)"
from datetime import datetime, timezone
import json, os
ts = datetime.now(timezone.utc).isoformat()
rows = [
 {"event_id":"d1","event_type":"fire","camera_id":"cam_001","alert_level":"critical","description":"화재 의심","confidence":0.9,"timestamp":ts,"vlm_risk_level":"high","vlm_confidence":0.85,"vlm_detected_events":["연기","불꽃"],"vlm_recommended_action":"즉시 대피 및 소방 신고"},
 {"event_id":"d2","event_type":"no_helmet","camera_id":"cam_001","alert_level":"high","description":"안전모 미착용","confidence":0.82,"timestamp":ts},
 {"event_id":"d3","event_type":"person_detected","camera_id":"cam_002","alert_level":"low","description":"사람 감지","confidence":0.71,"timestamp":ts},
]
with open("logs/alerts.log","a",encoding="utf-8") as f:
    for r in rows:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | INFO | EVENT | {json.dumps(r,ensure_ascii=False)}\n")
try:
    import cv2, numpy as np
    d = "data/captures/" + datetime.now().strftime("%Y%m%d") + "/cam_001"
    os.makedirs(d, exist_ok=True)
    img = np.full((240,320,3),(50,90,160),dtype=np.uint8)
    cv2.putText(img,"DEMO CAPTURE",(20,130),cv2.FONT_HERSHEY_SIMPLEX,0.8,(255,255,255),2)
    cv2.imwrite(f"{d}/no_helmet_demo.jpg", img)
except Exception:
    pass
print("seeded")
PY
fi

mkdir -p logs data/captures models/uploaded

# ── 엔진 API ─────────────────────────────────────────────────────────────────────
echo "[run_web] 엔진 API 시작 (:$ENGINE_PORT)..."
"$PY" -m uvicorn src.api.app:app --host 0.0.0.0 --port "$ENGINE_PORT" --log-level warning &
pids+=($!)

# ── 영상 게이트웨이(선택) ──────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1; then
  echo "[run_web] 영상 게이트웨이(MediaMTX) 시작 (:8554/:8888)..."
  docker rm -f ksi-cctv-gateway-dev >/dev/null 2>&1 || true
  docker run -d --rm --name ksi-cctv-gateway-dev \
    -p 8554:8554 -p 8888:8888 -p 8889:8889 -p 8189:8189/udp \
    -v "$ROOT/config/mediamtx.yml:/mediamtx.yml:ro" \
    bluenviron/mediamtx:1.19.1 >/dev/null 2>&1 || echo "[run_web] 게이트웨이 생략(docker 실패 — 영상 없이 진행)"
else
  echo "[run_web] docker 없음 — 영상 게이트웨이 생략(영상 외 기능은 정상)"
fi

# ── 웹 콘솔(Next) ─────────────────────────────────────────────────────────────
echo "[run_web] 웹 콘솔(Next.js) 시작 (:$WEB_PORT)..."
(
  cd frontend
  KSI_CCTV_ENGINE_URL="http://127.0.0.1:$ENGINE_PORT" \
  KSI_CCTV_GATEWAY_URL="http://127.0.0.1:8888" \
  npm run dev -- -H 0.0.0.0 -p "$WEB_PORT"
) &
pids+=($!)

echo
echo "════════════════════════════════════════════════════════════════"
echo "  웹 콘솔   : http://localhost:$WEB_PORT   (원격: http://<Tailscale-IP>:$WEB_PORT)"
echo "  로그인 비번: $KSI_CCTV_WEB_PASSWORD"
echo "  엔진 API  : http://localhost:$ENGINE_PORT/api/health/live"
echo "  종료      : Ctrl+C"
echo "════════════════════════════════════════════════════════════════"
wait
