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
- ⏭ 제어/실시간: control(start/stop) · 이벤트 SSE/WS — **P1b(데몬 통합 시)** 로 분리
- ✅ 테스트 12종(TestClient) + 실 uvicorn 서빙 스모크
- **검증 완료:** ruff·mypy clean(33 files)·pytest 429 passed. 실 uvicorn: live=200(비인증)·models 401→200(키)·정책 5프로파일 노출. 시크릿 마스킹·fail-closed 확인.

### P2. 크로스플랫폼 EP 선택 — 로컬 검증 가능
- ⬜ `yolo_detector._init_session` 다중 provider 선택(auto/cuda/directml/coreml/qnn/tensorrt/cpu) + 폴백
- ⬜ config `device` 옵션 확장 · requirements 플랫폼별 주석
- ⬜ 단위 테스트(provider 선택 로직)
- **수용:** 선택 로직 단위테스트 통과, 현 CPU 환경 회귀 없음 / [HW] 타깃 가속 실측

### P3. open-vocab 탐지(YOLO-World) — CPU 검증 가능
- ⬜ `OpenVocabDetector` + registry `open_vocab_onnx` 타입 + class_adapter identity 경로
- ⬜ YOLO-World ONNX + CLIP text encoder 획득(fetch 스크립트)
- ⬜ "텍스트 프롬프트 → 탐지" 실동작 검증(실 이미지, CPU)
- ⬜ 프롬프트→정책 자동배선(신규 클래스 vlm:on_ambiguous 기본) + 테스트
- **수용:** 임의 텍스트로 실 이미지에서 탐지 1건 이상 실증 / [HW] 실시간 FPS는 DGX 검증

### P4. 프론트엔드(Next.js+Tailwind+shadcn) — Node 있음, 빌드 가능
- ⬜ scaffold + 디자인 토큰(다크/라이트) + 레이아웃(데스크톱 콘솔/모바일 뷰어)
- ⬜ 핵심 화면: 대시보드·이벤트·카메라·모델(프롬프트)·설정 — P1 API 연동
- ⬜ `npm run build` 검증 + 기본 렌더 스모크
- **수용:** 빌드 성공, API mock/실연동 렌더, 다크/라이트·반응형 동작

### P5. 라이브 영상 게이트웨이(MediaMTX/go2rtc) — docker, 부분 검증
- ⬜ compose 서비스(RTSP→WebRTC/HLS) + 프론트 플레이어
- **수용:** 게이트웨이 기동·헬스 / [DATA] 실 RTSP 재생은 카메라 필요

### P6. 설치형 패키징 — 작성(Windows 테스트는 [HW] 보류)
- ⬜ Docker(현존 강화) · Windows Service+인스톨러 · PyInstaller · 오프라인 번들 · EP 패키지 선택
- **수용:** Linux/Docker 검증 / [HW] Windows·macOS 설치는 타깃에서

### P7. 정리·마이그레이션
- ⬜ Streamlit→legacy(프론트 패리티 전까지 유지) · project-definition.md 비목표 SSOT 갱신 · docs 동기화

---

## 진행 로그(커밋)
- (P0) 2026-06-16 cleanup: OUTSIDE_SECRET.bin·임시 스크린샷 스크립트 제거, 본 계획 문서 작성
- (P1) 2026-06-16 엔진 API(FastAPI) 읽기 엔드포인트 + fail-closed 인증 + 시크릿 마스킹 + 테스트 12 + 실서빙 스모크. P1b(제어/SSE)는 데몬 통합 시.
