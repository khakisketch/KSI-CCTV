# KSI-CCTV — Codex 작업 핸드오프 (역사적 기록 · 2026-08-12 폐기)

> **⚠️ 이 문서는 더 이상 유효하지 않다. 잔여작업 목록으로 쓰지 마라.**
>
> 아래 §A가 "즉시 착수"로 지목한 항목 중 A1(proxy 마이그레이션)·A2(이벤트 필터·페이지네이션)·
> A4(끊김 시뮬레이션)·A5(WebSocket)는 **모두 구현 완료**됐다. §B 문서 동기화, §C 하드웨어 항목도
> 상당수가 진행됐다. 이 문서를 근거로 작업하면 이미 있는 것을 다시 만들게 된다.
>
> 현재 기준: **`docs/release-gate.md`**(출고 승인 SSOT) · `docs/internal-ci.md`(게이트 실행) ·
> `.ai/plan.md` 상단(현행 마일스톤).
>
> 아래 내용은 2026-06-18 시점의 판단 기록으로만 남긴다.

---

> 작성: 2026-06-18 · 근거: master HEAD `63f4660`, 로컬 `pytest 451 passed / 3 skipped`(454 collected).
> 이 문서는 **실제 repo·git·테스트를 검증해** 만든 잔여작업 카탈로그다(병렬 조사 + opus adversarial 검증으로
> 조사 에이전트의 허위 항목을 걸러냄). Codex는 **§0(이미 완료)** 를 먼저 읽어 헛작업을 피하고, **§A**(즉시 착수)
> → **§B**(문서) → **§C**([HW] 필요) 순으로 진행하라.

---

## 0. ⚠️ 이미 완료 — Codex에 다시 시키지 말 것 (조사 중 나온 허위/오래된 항목)

검증 과정에서 "미구현"으로 잘못 분류됐으나 **실제로는 구현·커밋 완료**된 것들. 재작성하면 낭비/충돌이다.

| 항목 | 실제 상태(근거) |
|---|---|
| "Phase B B-2/B-3/B-4/B-6" | **존재하지 않는 가공 항목.** repo 전체 B-번호는 `B-1`(텔레메트리)·`B-5`(ruamel) 둘뿐(`docs/implementation-plan.md:121-122`). 구현 대상 없음 |
| Phase D 학습 스크립트 | `scripts/train_ppe.py`·`scripts/train_fire_smoke.py`·`scripts/export_to_onnx.py` **모두 존재·완성**(커밋 `66bfb78`). 남은 건 *실행*(§C) |
| onnxruntime 6-way EP 폴백 | `src/detection/yolo_detector.py` `_select_providers()` 완료(TensorRT/CUDA/DirectML/CoreML/QNN/CPU). 테스트 통과 |
| `/api/events` type·camera 필터 | `app.py:144-148` 서버사이드 `event_type`/`camera_id` 이미 지원(남은 건 level/date/pagination — §A2) |
| RTSP 재연결 로직 | `src/capture/rtsp_capture.py` 구현 + `test_capture.py` 단위검증 완료(남은 건 *시뮬 하니스* — §A7) |
| mypy/pyright | `pyproject.toml [tool.mypy]` + `scripts/test.sh` + `requirements-dev.txt`에 이미 설정 |
| `config/prompts/*.yaml` | `general.yaml`·`construction.yaml`·`school.yaml` 존재(+factory/parking/warehouse). PromptSelector 정상 폴백 |
| 이벤트 통계 영구성 | 웹 콘솔 메트릭은 영속 `logs/alerts.log` 파싱 → 재시작 후 유지 |
| nginx/TLS 예시 | 가이드에 nginx/Caddy/Let's Encrypt 예시 이미 존재(남은 건 Streamlit→Next 갱신 — §B) |
| `.onnx` git-LFS | `.onnx`는 `.gitignore`로 **의도적 미추적**(AGPL·repo 비대화 회피). LFS 도입은 작업 아님 |
| YOLO ONNX 모델 파일 | 작업 트리에 존재(gitignored). fresh clone은 `scripts/fetch_safety_models.py`로 받음 — 정상 흐름 |

---

## 0.5 즉시 처리(코딩 아님) — 사람/권한 필요

- **미푸시 커밋 2개**: `master`가 `origin/master`보다 2개 앞(`748a91c` B-1 프론트, `63f4660` starlette CVE).
  `git push origin master` 권장 — **외부 전송이라 소유자 확인 후** 실행(force push 금지).

---

## A. Codex 즉시 착수 가능 (하드웨어 불필요) — 우선순위순

### A1. `middleware.ts` → `proxy.ts` 마이그레이션 (Next.js 16) · P1
- **현황**: `frontend/src/middleware.ts` 존재, `proxy.ts` 없음. Next 16에서 `middleware` 파일 규약 deprecated.
- **scope**: 파일명 변경 + **export 이름도 함께 변경**해야 함 — Next 16은 export 이름으로 핸들러를 탐지한다.
  `proxy.ts`에서는 함수가 `proxy`(named) 또는 default로 export돼야 하며, `middleware` 이름 그대로면 검증 실패.
- **acceptance**: `npm run build` 경고 없이 통과 + 세션 게이트(미인증→/login 리다이렉트)가 기존대로 동작(E2E).
- **gotcha**: 단순 `git mv`로 끝나지 않음(위 export 규약). 미들웨어 matcher 설정도 신규 규약 확인.

### A2. 서버사이드 이벤트 필터/페이지네이션 확장 · P1
- **현황**: `/api/events`는 `limit`·`event_type`·`camera_id`만(`app.py:144-148`, `service.py:list_events`).
- **scope**: `alert_level`(low/medium/high/critical), 기간(`since`/`until` ISO8601), 페이지네이션(`offset` 또는 cursor)
  추가. 프론트 이벤트 페이지의 현재 클라이언트 필터(G6)를 서버 쿼리로 이관.
- **files**: `src/api/app.py`(Query 파라미터), `src/api/service.py`(`list_events` 필터/슬라이스),
  `frontend/src/lib/engine.ts`(`getEvents` 시그니처), `frontend/src/app/events/page.tsx` + 필터 컴포넌트.
- **acceptance**: `tests/test_api.py`에 level/기간/offset 케이스 추가→green; 웹에서 필터 조합 시 서버가 거른 결과만 표시.
- **gotcha**: `alerts.log` 파싱 기반이라 대용량 시 전체 스캔 비용 — 최소한 역순 스트리밍/조기 종료 고려.

### A3. WebRTC 저지연 영상 (현재 HLS) · P2
- **현황**: 게이트웨이는 **이미 WebRTC 프로비저닝됨** — `config/mediamtx.yml:21-23` `webrtc: yes`/`:8889`/`:8189`(MediaMTX 1.19.1).
- **scope**: Next에 WHEP signaling 프록시 라우트(`app/api/cameras/[id]/webrtc`) + 브라우저 `RTCPeerConnection`(WHEP)
  플레이어. HLS는 폴백으로 유지(원격/방화벽). `camera-live.tsx`에 WebRTC 우선·HLS 폴백 토글.
- **files**: `frontend/src/components/camera-live.tsx`, 신규 `app/api/cameras/[id]/webrtc/route.ts`,
  `scripts/run_web.sh`(게이트웨이 8889/8189 포트 노출), `config/mediamtx.yml`(필요 시 인증).
- **acceptance**: 실 브라우저(Chrome/Firefox)에서 cam_001 WebRTC 재생, HLS 대비 지연 감소 확인(스크린샷).
- **gotcha**: WebRTC는 same-origin 프록시가 까다로움(ICE/UDP). LAN 직결 전제, 원격은 HLS 폴백 명시. 헤드리스 Chromium은 H.264 미탑재 → Firefox로 검증.

### A4. Phase C M4 — `stress_test.py --simulate-disconnect` 폴트 주입 하니스 · P2 (HW 불필요)
- **현황**: `scripts/stress_test.py`는 파일 기반 단일 루프라 끊을 capture thread가 없음.
- **scope**: RTSP 끊김/복구를 시뮬레이션하는 fault-injection. 단순 `cap.release()`로 안 됨 — `RTSPCapture`/`CameraManager`
  경유로 재구성하거나 주입 훅 추가 필요.
- **files**: `scripts/stress_test.py`, 필요 시 `src/capture/rtsp_capture.py`(테스트 훅).
- **acceptance**: `--simulate-disconnect`로 끊김→재연결 사이클이 재현되고 reconnect_attempts/복구가 로깅됨.
- **gotcha**: 재연결 *로직 자체는 이미 구현/검증됨*(§0) — 검증 하니스만 만들 것.

### A5. API WebSocket 실시간 프레임/이벤트 스트림 · P2
- **현황**: 이벤트는 SSE(`/api/events/stream`) 존재. 프레임/실시간 푸시 WS는 없음.
- **scope**: 신규 WS 엔드포인트(경로는 신규 — 예 `/ws/events` 또는 프레임). 파이프라인 콜백 연동.
- **gotcha**: 현재 pipeline에 `remove_analysis_callback` 같은 **해제 메서드가 없음** → WS 수명주기 위해 추가 필요(누수 방지).

### A6. 프론트 UX 개선 · P2~P3
- **이벤트 페이지 자동 새로고침 토글/버튼**(현재 수동). `events/page.tsx`.
- **갤러리 가상화/무한스크롤**(이미지 多일 때). `gallery` 페이지.
- **acceptance**: 빈/희박/대량 상태 모두 graceful, 한글 `word-break: keep-all`, 데스크톱+모바일(390px) 확인.

### A7. 테스트 보강 · P2~P3
- **Frontend 단위/통합 테스트 부재** — 현재 프론트 자동 테스트 없음. Vitest/RTL 또는 Playwright E2E 도입.
- **`src/api/demo_app.py` 전용 테스트** — (제어 엔드포인트 자체는 `test_api.py::TestApiControl`이 커버).
- **minimal-env collection 견고화** — cv2 등 옵션 의존 없는 환경에서 일부 모듈 collection 실패 가능(로컬 .venv는 454 정상 수집).
  `rtsp_capture` import·`TestSdNotify`·`TestRegistryMetrics`에 import guard 보강(현 로컬 게이트엔 영향 없음, fresh-clone 견고성).

### A8. 소소한 정합 · P3
- `src/detection/model_registry.py:212` `device` 기본값 `"cuda"` → `"auto"`로 정합(230줄은 이미 `"auto"`). 무해하나 EP auto-선택과 일치시킴.
- `pose_fall_model` 프로파일(`docs/model-management.md '향후 제공'`) — 모델 `.onnx` 확보 시 등록(모델 파일 의존).

---

## B. 문서 동기화 (Codex, 순수 문서 — 코드 변경 없음) · P2

Streamlit 폐기(2026-06-17)·완료 항목들이 문서에 반영 안 됨.

- **`docs/implementation-plan.md`**: stale 아이콘 4줄을 ✅로 — `45`(P4 헤더 🔄), `52`(start/stop ⬜), `67`(P7 헤더 🔄),
  `70`(Streamlit 🔄). **`64`(Windows/PyInstaller ⬜)·`21`(범례)는 손대지 말 것**(실제 미완/범례).
- **`docs/operations.md`**: Streamlit/`run_dashboard.sh`/`:8080` 참조 다수(예 L12/13/110/164/205-211/258/265/270/272/285/318)
  → Next.js 웹 콘솔(`:3000`)+FastAPI 엔진 기준으로 교체. "~탭" 기능 안내도 현 웹 콘솔 메뉴로 정합.
- **`docs/completion-plan.md`·`docs/delivery-checklist.md`·`deployment-guide.md`**: 동일 Streamlit/포트 참조 정리.
- **HTTPS/nginx 가이드**: upstream 2개(웹 3000 + 엔진 API) 리버스프록시 + certbot 발급/갱신 예시 보완.
- **`.ai/review-checklist.md`·`.ai/plan.md`**: Phase 10 Track A/B 체크박스 현실화.
  **gotcha — M-번호 충돌**: `plan.md` Track A의 "M1"(docs 동기화)과 `task-state.md`의 "Phase 10 M1"(탐지 클래스 정합성)은 서로 다름. 편집 시 혼동 금지.

---

## C. [HW]/데이터 필요 — Codex는 코드/문서 준비까지, *실행*은 사람·장비

도구·스크립트는 대부분 완성돼 있다. "실행/검증"이 하드웨어·데이터에 묶인 항목.

| 항목 | Codex가 할 수 있는 것 | 사람/장비가 필요한 것 |
|---|---|---|
| **Phase C — DGX Spark CUDA 검증** | `healthcheck.py --require-gpu`·ARM64 `onnxruntime-gpu` 설치 분기(NGC/소스빌드)·runbook 정비 | DGX Spark(GB10, sm_121, CUDA13)에서 실행·레이턴시 측정 |
| **Phase C — 실 RTSP 30분 안정성** | (A4 시뮬 하니스) | 실 IP 카메라 1대로 30분 가동 |
| **Phase D — 커스텀 학습 실행** | 스크립트 완성됨(§0) — 데이터셋 yaml·하이퍼파라미터 점검만 | GPU + 데이터셋(Kaggle/Roboflow, `.env` 키) + 학습 실행→ONNX export |
| **실 VLM 종단 검증** | `test_e2e_real.py` warm-latency·프롬프트 튜닝 | Ollama Qwen3-VL(GB10에서 구동 중) 환경에서 실행 |
| **P6 Windows 패키징** | PyInstaller spec·NSSM·Inno Setup·PowerShell 스크립트 작성 | Windows x64/ARM64 빌드·설치 테스트 |
| **오프라인 설치 번들** | wheelhouse 스크립트·모델 동봉 절차 작성 | 빌드·검증 |

**전 항목 공통 안전수칙**: `batch=-1` 절대 금지(통합메모리 OOM, 고정 batch 명시) · RTSP 계정/API 키는 `.env`에만(커밋 금지) · 모델 등록은 `config/model_profiles.yaml`+`MultiModelRegistry` 경로(`yolo_detector.py` 직접 수정 금지) · 완료 게이트 `python -m pytest tests/` + 프론트 `npm run build`.

---

## 부록 — 검증된 완료 항목(현 상태 요약)
Phase 0-9 전체 · P1~P1c 엔진 API(읽기/쓰기/제어/SSE+인증) · P2 크로스플랫폼 EP · P3 open-vocab(YOLO-World) ·
P4~P4c 웹 콘솔(+HMAC 세션 인증) · P5 라이브 영상(MediaMTX HLS+hls.js) · P6a Docker 웹 이미지 · P7 Streamlit→Next 완전 이관·폐기 ·
보안 하드닝(ONNX 업로드 sandbox·PII/얼굴 블러·경로주입 차단·감사로그) · 24/7 무인운영(로그로테이션·watchdog·백업/복구) ·
B-1 카메라 텔레메트리 · B-5 YAML 주석 보존 · starlette CVE 픽스. 테스트 **451 passed / 3 skipped**.
