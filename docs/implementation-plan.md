# 구현 실행 계획 (설치형 AI CCTV 제품화) — 진행 SSOT

> 작성 2026-06-16 · 이 문서는 **로드맵 3종을 실행 가능한 단계로 쪼갠 진행 트래커**다.
> 근거 설계: [architecture-target](architecture-target.md) · [roadmap-capability](roadmap-capability.md) · [roadmap-packaging](roadmap-packaging.md)
>
> **원칙(절대 준수):** ① "된다"는 전부 **실행 근거**로만 — 안 돌려본 건 "미검증"으로 표기(할루시네이션 금지).
> ② 각 단계는 implement → test → 게이트(ruff/pytest/mypy) → commit. ③ 장비/데이터 없이 검증 불가한 것은
> **[HW]/[DATA] 플래그 + 대체 검증**(CPU·sample.mp4·합성)으로 최대 확인 후 "타깃 검증 필요" 명시.

## 환경 현실(2026-06-16 점검)
- Python venv ✅ · **Node v22 + npm 10.9 ✅(프론트 빌드 가능)** · 네트워크 ✅(github/hf 200, 모델 다운로드 가능)
- Docker ✅ · 디스크 3.3T 여유 · fastapi/uvicorn ❌→설치 필요 · GPU ❌(CPU만, 실시간성은 [HW] DGX 검증)

## 확정 방향(사용자)
설치형(오프라인/로컬) AI CCTV. 헤드리스 엔진(Python 유지) + FastAPI + 웹UI(Next/Tailwind/shadcn, 다크·라이트,
데스크톱 관제/모바일 뷰어) + 영상 게이트웨이. open-vocab "텍스트로 즉시 탐지". 멀티-OS(Win/WinARM64/macOS/Linux).
라이선스는 사용자가 리스크 부담(개발 진행에 비차단). 학습은 NON-GOAL.

---

## 단계(Phase) — 상태: ⬜ 대기 / 🔄 진행 / ✅ 완료(검증) / ⏸ 블록

### P1. 엔진 API (FastAPI) — 키스톤 ✅(읽기 API 완료·검증)
- ✅ FastAPI 앱 골격(`src/api/`: app·service·auth) — 인증(fail-closed X-API-Key) + 시크릿 마스킹
- ✅ 읽기 엔드포인트: events(alerts.log 파싱·마스킹) · config(큐레이션) · cameras(url 마스킹) · models · policies · health
- ✅ **P1b 쓰기 엔드포인트**: POST /api/models/{name}/prompts(open-vocab 프롬프트 설정 — 학습 없이 탐지대상 변경, 정제·검증·model_profiles 안전 갱신) + PUT /api/models/{name}/enabled. 인증 필수. 테스트 8.
- ⏭ 제어/실시간: control(start/stop) · 이벤트 SSE/WS — 데몬 통합 시(엔진 프로세스에 API mount 필요)
- ✅ 테스트 12종(TestClient) + 실 uvicorn 서빙 스모크
- **검증 완료:** ruff·mypy clean(33 files)·pytest 429 passed. 실 uvicorn: live=200(비인증)·models 401→200(키)·정책 5프로파일 노출. 시크릿 마스킹·fail-closed 확인.

### P2. 크로스플랫폼 EP 선택 — ✅(로컬 검증)
- ✅ `_select_providers(device, available)` 순수 함수 + `_init_session` 적용(auto/cuda/tensorrt/directml/coreml/qnn/cpu, 항상 CPU 폴백)
- ✅ config `device` 기본 `auto`로(default.yaml·model_profiles 3종) + 옵션 주석
- ✅ 단위 테스트 10종(선택 로직)
- **검증 완료:** EP 선택 10 테스트 통과·실모델 3종 auto 로드(CPU 폴백) 회귀 없음·439 passed. [HW] DGX/Win/macOS 실가속은 타깃 검증(P6 패키지가 EP별 onnxruntime 휠 설치 담당).

### P3. open-vocab 탐지(YOLO-World) — ✅(핵심 검증 완료)
- ✅ `OpenVocabDetector`(YOLO-World 래퍼, lazy import) + registry `open_vocab` 타입 + class_adapter identity(프롬프트명=표준명, 무수정 pass-through)
- ✅ 가중치 ultralytics 자동 다운로드(yolov8s-world.pt) + clip 자동 설치. (ONNX cross-platform export는 후속 — roadmap-capability)
- ✅ **"텍스트 프롬프트 → 탐지" 실증**: 학습 0, classes=['traffic light','car','person']로 sample.mp4 프레임 탐지 PASS(원시 PoC + 통합 detector 실추론 both)
- ✅ model_profiles `open_vocab_yolo`(비활성, 즉시 enable 가능) + API /models 노출 + 테스트 6(모의)+1(opt-in 실추론)
- ⏭ 프롬프트→정책 자동배선 UX는 P4(프론트)/API 쓰기에서(신규 클래스 vlm:on_ambiguous 기본 규칙은 설계 확정)
- **검증 완료:** ruff·mypy clean(34)·445 passed. CPU 1.49s/frame(실시간은 [HW] GPU 필요 — 연구 예측 일치).

### P4. 프론트엔드(Next.js+Tailwind+shadcn) — 🔄 진행(scaffold+디자인베이스 완료)
- ✅ scaffold(Next 16·React 19·Tailwind 4·TS·App Router, `frontend/`) + shadcn init(11 컴포넌트) + next-themes
- ✅ 디자인 시스템 베이스 + 레이아웃: ThemeProvider(다크 기본/라이트 토글), AppShell(데스크톱 사이드바 + 모바일 하단탭=경량 뷰어), 대시보드(메트릭 카드·이벤트 테이블 빈상태), 스텁 5라우트
- ✅ `npm run build` 성공(6라우트, TS·eslint clean). node_modules gitignore.
- ✅ **P4b 엔진 API 연동(읽기)**: `lib/engine.ts` 서버사이드 페치(API 키 서버 전용·브라우저 미노출, AbortSignal 타임아웃, 미연결 graceful) + 대시보드·이벤트·모델·카메라·설정을 force-dynamic Server Component로 실데이터 렌더 + EngineStatus 배지.
- ✅ **브라우저 렌더 E2E 검증(스크린샷)**: 엔진(uvicorn) + Next 동시 기동 → SSR이 실데이터 렌더 확인. 데스크톱 다크/라이트·모바일(하단탭) 스크린샷 육안 검증 — 메트릭(카메라1·모델3 실값)·모델5카드(open_vocab 포함)·"엔진 연결됨".
- ✅ **P4c 프롬프트 UI + 인증**: 프롬프트 관리(PromptManager + Next route, 키 서버보관, E2E POST→config 실갱신·비-openvocab 400). **프론트 인증**: HMAC 서명 세션쿠키(lib/auth, Web Crypto) + middleware fail-closed 게이트 + /login + 로그아웃. E2E: 미인증/→307·틀린비번 401·맞는비번 200+쿠키·인증후 200·보호 API proxy 307·로그아웃 후 307. frontend/.env.example 추가.
- ⬜ **남음: start/stop 제어** — 엔진 API를 데몬 프로세스에 mount해 라이브 파이프라인 제어(P1의 보류 control/SSE와 동일 — 별도 통합 단계).
- ⚠️ **알려진 한계(P1b 쓰기):** set_model_prompts가 yaml.safe_dump로 model_profiles.yaml을 재작성하며 **주석을 제거**한다(라이선스 경고 등). 후속: ruamel.yaml round-trip 또는 경고를 docs로 이전.
- **검증 완료:** build·lint clean·SSR 실데이터 + 쓰기 E2E·다크/라이트·반응형. start/stop·영상·인증은 후속.

### P5. 라이브 영상 게이트웨이(MediaMTX) — ✅ 완료(브라우저 실재생 검증)
- ✅ **P5a 게이트웨이**: `config/mediamtx.yml`(RTSP 입력 → HLS mpegts/WebRTC 출력, RTMP/SRT 비활성, API auth 기본 default-deny=보안) + compose `ksi-cctv-gateway`(MediaMTX 1.19.1 핀, `--profile video`/`web`, 포트 8554/8888/8889/8189udp, scratch라 healthcheck 없음). 엔진은 영상 전송 안 함(architecture-target §2 준수). 카메라 RTSP는 .env SSOT 공유 + on-demand 풀 경로 템플릿(주석).
- ✅ **P5b 프론트 플레이어 + same-origin HLS 프록시**: `/api/cameras/{id}/stream`(same-origin 프록시 URL 발급) + **`/api/cameras/{id}/hls/[...seg]` 프록시**(브라우저→Next→게이트웨이; MediaMTX의 cookieCheck(Secure 쿠키) CSRF 핸드셰이크를 서버에서 처리해 평문 HTTP·교차출처에서 막히는 문제 우회 + 게이트웨이 주소·RTSP 자격증명 브라우저 미노출). 카메라 페이지 hls.js `<video>` 플레이어(on-demand 토글, Safari 네이티브 HLS 폴백). 세션 게이트(middleware) 뒤. 의존성 hls.js 1.6(npm audit high+ 0).
- **검증 완료(카메라 없이, 브라우저 종단):** ffmpeg로 sample.mp4를 합성 RTSP(cam_001) 발행 → 풀스택(엔진 API+게이트웨이+next) 기동 → **Firefox(시스템 H.264)로 로그인→카메라 페이지→라이브 토글→hls.js가 프록시 경유 HLS 재생: VIDEO_STATE readyState=4·currentTime 진행·640x480·paused=false, 데스크톱+모바일(390px) 스크린샷 실프레임 렌더 확인**. 프록시 체인 curl도 index/main_stream/segment(534KB) 200. (헤드리스 Chromium은 H.264 코덱 미탑재라 재생 불가 — 테스트환경 한계이지 제품 결함 아님; 실 Chrome/Edge/Safari/Firefox는 H.264 지원.) [DATA] 실 카메라 RTSP·원격 WebRTC(webrtcAdditionalHosts/TURN)는 타깃.

### P6. 설치형 패키징 — 🔄 진행(Docker 웹 컨테이너 검증 완료, Windows/PyInstaller [HW] 보류)
- ✅ **P6a 웹 UI Docker 이미지**: `frontend/Dockerfile`(멀티스테이지 standalone, 비root nextjs:1001) + `.dockerignore`. next.config.ts `output:"standalone"` + `outputFileTracingRoot`/`turbopack.root`를 frontend/로 고정(상위 lockfile 오추론으로 standalone이 절대경로 미러로 중첩되는 버그 수정 — 평면 server.js 확인).
- ✅ **P6a compose 와이어링**: `ksi-cctv-web` 서비스(`--profile web`) — 엔진(ksi-cctv:8081)을 서비스명으로 서버사이드 호출(API 키 브라우저 미노출), depends_on healthy, node 헬스프로브(alpine curl 부재). 엔진 `KSI_CCTV_HEALTH_HOST` 기본 0.0.0.0(호스트 미매핑 → compose 네트워크 내부만 도달) + `KSI_CCTV_API_KEY`를 common-env로 승격(엔진/웹 동일 키 공유). obsolete `version` 키 제거.
- ⬜ Windows Service+인스톨러 · PyInstaller · 오프라인 번들 · EP별 onnxruntime 휠 선택(P2 연계)
- **검증 완료:** `docker build ./frontend` 성공 → 컨테이너 실기동(✓ Ready)·`/login` 200·미인증 `/`→307 로그인 리다이렉트(middleware fail-closed 컨테이너에서 동작). 엔진 Dockerfile `docker build --check` 무경고. compose config(daemon/web 양 프로파일) 통과. pytest 462 passed. [HW] Windows·macOS 설치 + 엔진 GPU 이미지 풀빌드는 타깃 검증.

### P7. 정리·마이그레이션 — 🔄 진행(SSOT 갱신·docs 동기화 완료, Streamlit 폐기는 패리티 후)
- ✅ **project-definition.md §비목표 SSOT 갱신**: "실시간 영상 스트리밍" 비목표 제거(P5로 승격·구현), "모바일 앱"→"네이티브 모바일 앱(반응형 웹 뷰어는 제공)", 클라우드=설치형 1차의 옵션, 학습=NON-GOAL 명시. architecture-target가 지적한 SSOT 모순 해소.
- ✅ **docs 동기화**: README 아키텍처에 엔진 API·Next.js 웹 콘솔·MediaMTX 게이트웨이 추가 + Streamlit "레거시(점진 이관)" 표기 + 웹 콘솔 Docker 기동법 + 구조(src/api·frontend) + 테스트수(462)·다음 단계(학습→패키징/이관) 갱신. project-definition 현재 상태에 제품화 진척 반영.
- 🔄 **Streamlit 잔여 탭 웹 이관(단계 3)**:
  - ✅ **P7-2 모델 업로드 API**: `POST /api/models/upload`(멀티파트) — 기존 하드닝 `upload_validator.upload_model`(external-data 차단→load→shape→smoke를 단일 rlimit sandbox) 재사용, 인증 필수, 메모리 OOM 가드(read 상한), disabled 등록, 서버경로 미노출. **실 ONNX(yolo26) 업로드 E2E: 샌드박스 검증 통과→models/uploaded/<id> 격리저장→uploaded_<id> 등록(disabled)→/api/models 노출 확인**(config 백업/복원). 단위테스트 4(plumbing·인증·크기가드). **SCA**: python-multipart 신규 추가→pip-audit가 CVE-2026-53538/9/40 탐지→0.0.31+로 픽스(재감사 클린).
  - ✅ **P7-3 모델 업로드 웹 UI**: `ModelUpload` 폼(파일·이름·클래스·입력크기) + Next 멀티파트 프록시(`/api/models/upload` — 본문 스트리밍·키 서버부착·키 미노출) + 모델 페이지 통합. **브라우저 E2E: 실 ONNX(yolo26) UI 업로드→샌드박스 검증→성공 토스트→router.refresh로 uploaded_<id> 카드 등장(비활성)·콘솔오류 0** 스크린샷 확인. 모델 업로드 Streamlit→웹 이관 완료.
  - ✅ **P7-4 갤러리(API+웹)**: 엔진 `GET /api/captures`(트리) + `GET /api/captures/{date}/{camera}/{name}`(image/jpeg, 경로주입 방지·`.enc` 서버복호화·PII 인증). 웹: Next 이미지 프록시(`<img>`는 헤더 못 보내므로 키 서버부착) + `/gallery` 페이지(날짜 선택·카메라 그룹·이미지 그리드·암호화 잠금 fallback) + 나브 항목. **브라우저 E2E: 실 JPEG 캡처→갤러리 페이지에서 날짜버튼·카메라그룹·이미지 실렌더(naturalWidth>0)·콘솔오류 0** 스크린샷 확인. 단위/엔드포인트 테스트 5. 갤러리 Streamlit→웹 이관 완료.
  - ⬜ 감사 로그 뷰
- ⬜ **Streamlit 폐기(단계 4)**: 잔여 탭이 모두 웹 콘솔에 도달하면 제거. 그 전까지 무중단 병존(strangler-fig).
- **검증:** 467 passed·ruff clean·pip-audit(multipart 클린). [HW] 실 카메라 라이브는 타깃. (참고: torch CVE-2025-3000은 기존 의존성·cu130/DGX 제약으로 별도 트랙.)

---

### P1c. 데몬+API 통합(단일 서비스) — ✅ 완료(검증)
- ✅ **P1c-1 API 추가분**: 제어(/api/control/{status,start,stop}) + compat 헬스(/health·/health/live·/health/ready·/healthz). 테스트 9.
- ✅ **P1c-2 run.py 와이어링**: http.server → **uvicorn(create_app(config,pipeline,checker_factory))** 데몬스레드 서빙(포트 8081 유지, compat /health* 호환, uvicorn 시그널핸들러 비-메인스레드 skip로 데몬 SIGTERM 보존, graceful should_exit). **탐지+API+제어 1프로세스 달성**.
- **검증 완료:** 데몬 smoke(opt-in) — 기동→/health/live 200·/health 응답·**/api/control/status connected=True(라이브 파이프라인 API 연결)**·SIGTERM exit 0. 462 passed·ruff·mypy clean.
- ✅ **P1c-3 SSE 실시간 이벤트**: `/api/events/stream`(엔진, alerts.log tail→신규 이벤트 push, list_events와 동일 파싱·마스킹 재사용, 연결 즉시 flush + heartbeat) + 프론트 same-origin SSE 프록시(`/api/events/stream` — EventSource는 헤더 못 보내므로 서버가 키 부착) + `EventsLive`(SSR 초기목록 + EventSource로 실시간 prepend, "실시간 수신 중" 표시). **브라우저 E2E: 로그 append→새로고침 없이 이벤트 행 라이브 추가·유형/등급 정확·RTSP 마스킹·라이브 인디케이터 ON 확인**. SSE 단위테스트 추가(463 passed).

## 진행 로그(커밋)
- (P0) 2026-06-16 cleanup: OUTSIDE_SECRET.bin·임시 스크린샷 스크립트 제거, 본 계획 문서 작성
- (P1) 2026-06-16 엔진 API(FastAPI) 읽기 엔드포인트 + fail-closed 인증 + 시크릿 마스킹 + 테스트 12 + 실서빙 스모크. P1b(제어/SSE)는 데몬 통합 시.
- (P2) 2026-06-16 크로스플랫폼 EP 선택(_select_providers, auto 기본) + 테스트 10. 실모델 auto 로드 회귀 없음.
- (P3) 2026-06-16 open-vocab(YOLO-World) OpenVocabDetector + registry open_vocab 타입 + model_profiles 엔트리. "텍스트로 학습없이 탐지" 실추론 PASS. 테스트 7.
- (P4) 2026-06-17 프론트 scaffold(Next16/React19/Tailwind4/shadcn) + 다크라이트 테마 + 반응형 셸(데스크톱 사이드바/모바일 하단탭) + 대시보드/스텁. npm build 성공.
- (P4b) 2026-06-17 엔진 API 연동(읽기) — lib/engine.ts 서버사이드(키 미노출) + 5화면 실데이터 Server Component. 엔진+Next E2E SSR 실데이터 렌더 + 다크/라이트/모바일 스크린샷 검증. P4c=쓰기·인증.
- (P1b) 2026-06-17 엔진 API 쓰기 — POST /api/models/{name}/prompts(open-vocab 프롬프트, 정제·검증·안전 갱신) + PUT enabled. 인증·테스트 8. P4c(프롬프트 UI)의 백엔드 전제 완료.
- (P4c-1) 2026-06-17 프론트 프롬프트 관리 UI — PromptManager + Next route handler(키 서버보관). E2E: UI→Next→엔진 POST로 config 실갱신·비-openvocab 400 확인. 남음=프론트 인증·start/stop·yaml 주석보존.
- (P4c-2) 2026-06-17 프론트 인증 — HMAC 세션쿠키(Web Crypto)+middleware fail-closed 게이트+/login+로그아웃. 전 플로우 E2E 검증(307/401/200/쿠키/proxy차단/로그아웃). .env.example. 남음=start/stop(데몬 통합).
- (P1c-1) 2026-06-17 엔진 API 제어 엔드포인트(/api/control/{status,start,stop}) + compat /health*(http.server 대체). 테스트 9. 462 passed. P1c-2(run.py 와이어링) 다음.
- (P1c-2) 2026-06-17 run.py가 uvicorn(FastAPI)로 단일 서빙 — 탐지+API+제어 1프로세스. 데몬 smoke로 /api/control/status connected=True·SIGTERM exit 0 검증. http.server 제거.
- (P6a) 2026-06-17 설치형 패키징(Docker 웹) — frontend Dockerfile(standalone·비root) + compose `ksi-cctv-web`(--profile web, 엔진 서비스명 호출·API키 공유). next standalone 중첩버그 수정(tracingRoot 고정). 실빌드→컨테이너 기동→/login 200·미인증 307 리다이렉트 E2E 검증. obsolete version 키 제거. 남음=Windows/PyInstaller/오프라인번들[HW].
- (P5a) 2026-06-17 영상 게이트웨이 — MediaMTX 1.19.1 compose 서비스(--profile video/web) + config/mediamtx.yml(RTSP→HLS/WebRTC). ffmpeg 합성 RTSP(sample.mp4)→HLS 체인을 실HLS클라이언트로 30프레임 디코드 검증(카메라 불요). compose 마운트·4포트 확인. 남음=P5b 프론트 hls.js 플레이어 + /api/cameras/{id}/stream.
- (P5b) 2026-06-17 프론트 라이브 플레이어 + same-origin HLS 프록시 — /api/cameras/{id}/stream(프록시 URL) + /hls/[...seg] 프록시(MediaMTX cookieCheck Secure쿠키 핸드셰이크를 서버처리→평문HTTP/교차출처 우회 + 게이트웨이주소·RTSP크리덴셜 미노출) + 카메라페이지 hls.js 플레이어(on-demand 토글). Firefox 종단 재생 검증(readyState4·currentTime진행·640x480·데스크톱+모바일 실프레임). hls.js 추가(audit high+ 0). P5 완료.
- (P7-1) 2026-06-17 정리·마이그레이션(문서) — project-definition §비목표 SSOT 갱신(실시간영상 승격·모바일=네이티브한정·학습 NON-GOAL) + README 동기화(엔진API·웹콘솔·게이트웨이·Streamlit 레거시표기·웹 Docker 기동법·구조·테스트수 462·다음단계). Streamlit 폐기는 잔여탭 패리티 후(병존 유지). pytest 462 passed.
- (P1c-3) 2026-06-17 SSE 실시간 이벤트 — /api/events/stream(엔진 tail+push·즉시 flush·heartbeat) + 프론트 same-origin SSE 프록시(키 서버부착) + EventsLive(EventSource 실시간 prepend·"실시간 수신 중"). 브라우저 E2E: 로그 append→무새로고침 라이브 추가·마스킹·인디케이터 ON. 단위테스트 추가, 463 passed·ruff clean. 웹 콘솔=실시간 관제 UI 완성.
- (P7-2) 2026-06-17 모델 업로드 API(Streamlit 이관 1/3) — POST /api/models/upload(멀티파트→하드닝 upload_validator 샌드박스 재사용·인증·OOM가드·disabled 등록·서버경로 미노출). 실 ONNX(yolo26) 업로드 E2E 통과(격리저장·등록·노출, config 백업복원). 단위테스트 4. python-multipart 추가→pip-audit CVE 3건 탐지→0.0.31+ 픽스. 467 passed. 웹 업로드 UI는 다음.
- (P7-3) 2026-06-17 모델 업로드 웹 UI — ModelUpload 폼 + Next 멀티파트 프록시(스트리밍·키 부착·미노출) + 모델 페이지 통합. 브라우저 E2E: 실 ONNX UI 업로드→샌드박스 검증→성공 토스트→uploaded_<id> 카드 등장(비활성)·콘솔오류 0. 모델 업로드 Streamlit→웹 이관 완료. (다음: 갤러리·감사 로그)
- (P7-4) 2026-06-17 갤러리 API — GET /api/captures(트리) + GET /api/captures/{date}/{camera}/{name}(image/jpeg). path-safety(YYYYMMDD·영숫자세그먼트·..차단·resolve확인)·.enc 서버복호화·PII 인증. 테스트 5(엔드포인트 serve/auth/404 포함, TestClient 실 ASGI). 472 passed. 웹 갤러리 뷰는 다음.
- (P7-4b) 2026-06-17 갤러리 웹 뷰 — Next 이미지 프록시(키 서버부착) + /gallery(날짜·카메라·그리드·암호화 잠금 fallback) + 나브. 브라우저 E2E: 실 JPEG 캡처→날짜버튼·카메라그룹·이미지 실렌더(naturalWidth>0)·콘솔오류 0. 갤러리 Streamlit→웹 이관 완료. (다음: 감사 로그)
