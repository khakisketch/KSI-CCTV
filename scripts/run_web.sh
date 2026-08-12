#!/usr/bin/env bash
# DEVELOPMENT/LAB ONLY: 로컬에서 웹 콘솔 전체 스택을 한 번에 띄워 UI/UX를 직접 확인한다.
# 운영 납품은 서명된 오프라인 Appliance만 사용한다(docs/offline-appliance.md).
#   엔진 API(FastAPI) + 영상 게이트웨이(MediaMTX, docker 있으면) + Next.js 웹 콘솔
#
# 포트는 KSI-CCTV 전용 대역을 써서 다른 프로젝트(예: KSI-Scout·carescience의 3000/8000/8080)와
# 충돌하지 않는다: 웹 3100 · 엔진 8181 · 게이트웨이 HLS 8910 / RTSP 8954. (모두 env로 override 가능)
#
# 사용:
#   bash scripts/run_web.sh                 # http://localhost:3100
#   KSI_CCTV_DEMO=1 bash scripts/run_web.sh # 데모 이벤트/캡처 시드 + sample.mp4를 cam_001 라이브로 송출
#   KSI_CCTV_WEB_PASSWORD='local-password' bash scripts/run_web.sh
#   KSI_CCTV_WEB_DEV=1 KSI_CCTV_DEV_AUTH_BYPASS=1 bash scripts/run_web.sh # loopback + 일회용 browser capability
#   KSI_CCTV_BIND_HOST=192.0.2.10 KSI_CCTV_INSECURE_REMOTE_DEV=1 bash scripts/run_web.sh
# 종료: Ctrl+C (모든 프로세스/컨테이너 정리)
#
# 참고: 헤드리스 Chromium은 H.264 미탑재라 라이브 영상이 안 보일 수 있으나, 실 브라우저
# (Chrome/Edge/Safari/Firefox)에서는 정상 재생된다. 원격 개발은 평문 HTTP이므로 비공개 시험망에서만 한다.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BIND_HOST="${KSI_CCTV_BIND_HOST:-127.0.0.1}"
case "$BIND_HOST" in
  127.0.0.1|::1|localhost) ;;
  *)
    if [ "${KSI_CCTV_INSECURE_REMOTE_DEV:-}" != "1" ]; then
      echo "[run_web] Refusing non-loopback bind ($BIND_HOST)."
      echo "[run_web] This development/lab launcher uses plaintext HTTP; set KSI_CCTV_INSECURE_REMOTE_DEV=1 only on an isolated test network."
      exit 2
    fi
    ;;
esac

DEV_AUTH_BYPASS=0
if [ "${KSI_CCTV_DEV_AUTH_BYPASS:-}" = "1" ]; then
  DEV_AUTH_BYPASS=1
fi
if [ "$DEV_AUTH_BYPASS" = "1" ]; then
  if [ "${KSI_CCTV_WEB_DEV:-}" != "1" ]; then
    echo "[run_web] Refusing auth bypass outside KSI_CCTV_WEB_DEV=1."
    exit 2
  fi
  case "$BIND_HOST" in
    127.0.0.1|::1|localhost) ;;
    *)
      echo "[run_web] Refusing auth bypass on non-loopback bind ($BIND_HOST)."
      exit 2
      ;;
  esac
fi
export KSI_CCTV_WEB_BIND_HOST="$BIND_HOST"
# 개발/랩 launcher에서는 재로그인 마찰을 줄이되, Appliance 기본값은 12시간으로 유지한다.
export KSI_CCTV_WEB_SESSION_TTL_SEC="${KSI_CCTV_WEB_SESSION_TTL_SEC:-604800}"

generate_secret() {
  od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}

CREDENTIALS_FILE=""
GENERATED_CREDENTIALS=0
if [ -z "${KSI_CCTV_API_KEY:-}" ]; then
  export KSI_CCTV_API_KEY="$(generate_secret)"
  GENERATED_CREDENTIALS=1
fi
if [ "$DEV_AUTH_BYPASS" = "0" ] && [ -z "${KSI_CCTV_WEB_PASSWORD:-}" ]; then
  export KSI_CCTV_WEB_PASSWORD="$(generate_secret)"
  GENERATED_CREDENTIALS=1
fi
if [ "$DEV_AUTH_BYPASS" = "0" ] && [ -z "${KSI_CCTV_WEB_SECRET:-}" ]; then
  export KSI_CCTV_WEB_SECRET="$(generate_secret)"
  GENERATED_CREDENTIALS=1
fi
ENGINE_PORT="${KSI_CCTV_ENGINE_PORT:-8181}"   # KSI-CCTV 전용(데몬 8081과도 분리)
WEB_PORT="${KSI_CCTV_WEB_PORT:-3100}"         # 3000 회피(타 프로젝트 Next 기본값)
GW_HLS_PORT="${KSI_CCTV_GW_HLS_PORT:-8910}"   # 게이트웨이 HLS(컨테이너 8888 → 호스트 8910)
GW_RTSP_PORT="${KSI_CCTV_GW_RTSP_PORT:-8954}" # 게이트웨이 RTSP(컨테이너 8554 → 호스트 8954)

if [ "$GENERATED_CREDENTIALS" = "1" ]; then
  umask 077
  CREDENTIALS_FILE="$(mktemp "${TMPDIR:-/tmp}/ksi-cctv-dev-credentials.XXXXXX")"
  {
    printf 'KSI_CCTV_API_KEY=%s\n' "$KSI_CCTV_API_KEY"
    [ "$DEV_AUTH_BYPASS" = "1" ] || printf 'KSI_CCTV_WEB_PASSWORD=%s\n' "$KSI_CCTV_WEB_PASSWORD"
    [ "$DEV_AUTH_BYPASS" = "1" ] || printf 'KSI_CCTV_WEB_SECRET=%s\n' "$KSI_CCTV_WEB_SECRET"
  } > "$CREDENTIALS_FILE"
fi

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

pids=()
# 프로세스와 그 자손까지 모두 종료한다.
# (npm run dev → next dev → next-server 처럼 손자 프로세스가 남아 포트/stale 컴파일을
#  점유한 채 orphan으로 떠도는 것을 막는다 — 이게 안 되면 다음 세션이 옛 빌드를 본다.)
kill_tree() {
  local pid="$1"
  local c
  for c in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$c"; done
  kill "$pid" 2>/dev/null || true
}
cleanup() {
  echo; echo "[run_web] 종료 중..."
  for p in "${pids[@]:-}"; do kill_tree "$p"; done
  docker rm -f ksi-cctv-gateway-dev >/dev/null 2>&1 || true
  [ -z "$CREDENTIALS_FILE" ] || rm -f "$CREDENTIALS_FILE"
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
# DEMO 모드는 합성 파이프라인 주입 앱(demo_app)을 서빙 → 텔레메트리·제어가 데이터를 갖고 동작.
APP_MODULE="src.api.app:app"
[ "${KSI_CCTV_DEMO:-}" = "1" ] && APP_MODULE="src.api.demo_app:app"
echo "[run_web] 엔진 API 시작 (127.0.0.1:$ENGINE_PORT, $APP_MODULE)..."
"$PY" -m uvicorn "$APP_MODULE" --host 127.0.0.1 --port "$ENGINE_PORT" --log-level warning &
pids+=($!)

# ── 영상 게이트웨이(선택) ──────────────────────────────────────────────────────
GATEWAY_UP=0
if command -v docker >/dev/null 2>&1; then
  echo "[run_web] 영상 게이트웨이(MediaMTX) 시작 (loopback HLS :$GW_HLS_PORT / RTSP :$GW_RTSP_PORT)..."
  docker rm -f ksi-cctv-gateway-dev >/dev/null 2>&1 || true
  if docker run -d --rm --name ksi-cctv-gateway-dev \
      -p "127.0.0.1:$GW_RTSP_PORT":8554 -p "127.0.0.1:$GW_HLS_PORT":8888 \
      -v "$ROOT/config/mediamtx.yml:/mediamtx.yml:ro" \
      bluenviron/mediamtx:1.19.1 >/dev/null 2>&1; then
    GATEWAY_UP=1
  else
    echo "[run_web] 게이트웨이 생략(docker 실패 — 영상 외 기능은 정상)"
  fi
else
  echo "[run_web] docker 없음 — 영상 게이트웨이 생략(영상 외 기능은 정상)"
fi

# ── 데모 라이브 스트림(선택) — sample.mp4를 cam_001로 송출 ──────────────────────
if [ "${KSI_CCTV_DEMO:-}" = "1" ] && [ "$GATEWAY_UP" = "1" ] && command -v ffmpeg >/dev/null 2>&1 && [ -f data/sample.mp4 ]; then
  echo "[run_web] 데모 라이브 스트림 송출(cam_001 ← sample.mp4)..."
  sleep 2
  ffmpeg -hide_banner -loglevel error -re -stream_loop -1 -i data/sample.mp4 \
    -c:v libx264 -preset ultrafast -tune zerolatency -an \
    -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:$GW_RTSP_PORT/cam_001" &
  pids+=($!)
fi

# ── 웹 콘솔(Next) ─────────────────────────────────────────────────────────────
# 프로덕션 빌드를 서빙한다(dev 아님): 원격 점검 박스에서 HMR 웹소켓 의존 없이 안정적이고,
# 실제 배포 산출물과 동일하다. (KSI_CCTV_WEB_DEV=1 이면 dev 서버로 빠르게 띄움 — 프론트 수정 중일 때만)
if [ "${KSI_CCTV_WEB_DEV:-}" = "1" ]; then
  echo "[run_web] 웹 콘솔(Next.js, DEV) 시작 (:$WEB_PORT)..."
  ( cd frontend
    KSI_CCTV_ENGINE_URL="http://127.0.0.1:$ENGINE_PORT" \
    KSI_CCTV_GATEWAY_URL="http://127.0.0.1:$GW_HLS_PORT" \
    KSI_CCTV_WEB_BIND_HOST="$BIND_HOST" \
    npm run dev -- -H "$BIND_HOST" -p "$WEB_PORT" ) &
  pids+=($!)
else
  echo "[run_web] 웹 콘솔(Next.js) 빌드 중... (최초/코드변경 시 수십 초)"
  if ( cd frontend && npm run build >/tmp/ksi_web_build.log 2>&1 ); then
    echo "[run_web] 웹 콘솔(Next.js) 시작 (:$WEB_PORT)..."
    ( cd frontend
      KSI_CCTV_ENGINE_URL="http://127.0.0.1:$ENGINE_PORT" \
      KSI_CCTV_GATEWAY_URL="http://127.0.0.1:$GW_HLS_PORT" \
      npm run start -- -H "$BIND_HOST" -p "$WEB_PORT" ) &
    pids+=($!)
  else
    echo "[run_web] ✗ 웹 빌드 실패 — /tmp/ksi_web_build.log 확인. (DEV로 재시도하려면 KSI_CCTV_WEB_DEV=1)"
    tail -20 /tmp/ksi_web_build.log
  fi
fi

echo
echo "════════════════════════════════════════════════════════════════"
echo "  웹 콘솔   : http://$BIND_HOST:$WEB_PORT"
echo "  엔진 API  : http://localhost:$ENGINE_PORT/api/health/live"
echo "  포트(전용): 웹 $WEB_PORT · 엔진/게이트웨이(loopback) $ENGINE_PORT/$GW_HLS_PORT/$GW_RTSP_PORT"
echo "  바인드     : $BIND_HOST"
if [ "$DEV_AUTH_BYPASS" = "1" ]; then
  echo "  인증 모드  : 개발 전용 bypass (loopback-only)"
else
  echo "  인증 모드  : password (session ${KSI_CCTV_WEB_SESSION_TTL_SEC}s)"
fi
echo "  자격증명  : ${CREDENTIALS_FILE:-환경변수에서 제공됨} (종료 시 생성 파일 삭제, 내용은 출력하지 않음)"
echo "  종료      : Ctrl+C"
echo "════════════════════════════════════════════════════════════════"
wait
