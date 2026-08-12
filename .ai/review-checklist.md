# KSI-CCTV 리뷰 체크리스트

> 역사적 개발 체크리스트입니다. 현재 release 기준은 `docs/release-gate.md`와
> `docs/internal-ci.md`이며, 운영 배포는 signed Ubuntu ARM64 Appliance만 지원합니다.

## Advanced-AI v1 Current Review Gate

- [x] 구현 기준: COCO/PPE/fire-smoke 정책 탐지와 ByteTrack `line_crossing`/`loitering`; Streamlit은 폐기되고 Next.js/HLS만 사용
- [ ] 개발 기준: pose/ST-GCN fall/fight와 bounded local VLM verification의 테스트 및 측정
- [ ] VLM 실패 기준: provider/timeout/parser failure가 candidate를 silent suppression하지 않고 `unverified` alert를 생성함
- [ ] qualification: DGX Spark ARM64 CUDA artifact, 모델 license/현장 정확도, 실제 RTSP/HLS, recovery 및 목표 동시 채널 soak evidence
- [ ] capacity: 16채널을 포함해 승인하려는 동시 채널 수만 claim; 현재 16채널은 미입증
- [x] v1 제외 확인: WebRTC, cloud/native app, RBAC, production 기본 open-vocab, LLM reporting, 자동 few-shot/GNN, theft/weapons/smoking

## Phase 6 — User Model Upload 보안 조건

- [x] ONNX only — `.onnx` 외 확장자 차단
- [x] 확장자 검증 — `validate_extension()` 구현
- [x] 파일명 sanitize — path traversal 차단, UUID 기반 저장명
- [x] 업로드 디렉토리 격리 — `models/uploaded/{model_id}/model.onnx`
- [x] 파일 크기 제한 — 500MB, in-memory 검사 후 disk write
- [x] ONNX load validation — `validate_onnx_load()`
- [x] ONNX shape validation — `validate_onnx_shape_metadata()`
- [x] Inference timeout — `_run_with_timeout()`, 30s
- [x] 감사 로그 — upload/activate/deactivate/delete
- [x] 관리자 인증 — `is_admin_model_management_allowed()`
- [x] Disabled-by-default 등록
- [x] 삭제 확인 UI — per-model checkbox

## 전체 회귀 검사

- [x] `python3 -m compileall -q src tests scripts`
- [x] `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- [x] `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode`

## 데이터 안전

- [x] 얼굴 블러링 적용 (AlertManager)
- [x] RTSP URL 마스킹 (로깅 시)
- [x] scrypt 비밀번호 해싱 (대시보드)
- [x] 감사 로그 (로그인, 모델 관리)
- [x] 로그 로테이션 (AlertManager)

## 개발/랩 배포 준비 (Phase 7 완료 ✅, 운영 배포 근거 아님)

- [x] `scripts/install.sh`
- [x] `systemd/ksi-cctv.service`
- [x] `scripts/healthcheck.py`
- [x] GPU provider 검증 (CUDA/CPU fallback)
- [x] `.env.example` 배포용 템플릿
- [x] 로그/데이터 보관 정책 문서화

## Custom Safety Model (Phase 8 완료 ✅)

- [x] 사전학습 모델 통합 (class_adapter, fetch_safety_models.py)
- [x] 데이터셋 인프라 (download/convert/validate/generate_yaml)
- [x] 대시보드 개선 (감사 로그, 시스템 상태, 이벤트 필터)
- [x] 문서화 (deployment-guide.md, custom-model-training.md)
- [x] 알림/내보내기/로그 뷰어 (HTML 이메일, CSV/JSON, 이벤트 로그)
- [x] 대시보드 보안 (자동 새로고침, rate limiting, session timeout)
- [x] 알림 설정 UI + 배치 처리 (YAML 영구 저장, batch flush)
- [x] 카메라별 통계 + 동적 프레임 skip + URL 마스킹

## 개발 개선 (Phase 9 완료 ✅)

- [x] 사전학습 모델 다운로드 + 추론 검증 (M4)
- [x] stress_test.py (M3)
- [x] VLM observability + cache (M1)
- [x] profile_pipeline.py (M2)
- [x] VLM cache reset 공개 메서드 (Should-fix)

## Phase 10 — 생산 준비 (Track A)

- [x] `.ai/*` 문서 동기화 (M1) — 2026-06-21
- [x] Git init + LFS 설정 (M2) — 2026-06-10 초기 커밋, `.gitattributes` LFS 설정 (`.onnx`는 의도적 미추적)
- [ ] DGX Spark CUDA provider 검증 (M3) — [HW] 미실행
- [x] RTSP 재연결 시뮬레이션 (M4) — `stress_test.py --simulate-disconnect` 하니스
- [x] Streamlit → Next.js 웹 콘솔 이관·폐기 (P7) — 2026-06-17
- [x] 엔진 API(FastAPI) P1~P1c — 읽기/쓰기/제어/SSE+인증
- [x] 크로스플랫폼 EP 선택 (P2) — auto 기본, 6-way EP 폴백
- [x] open-vocab 탐지 YOLO-World (P3)
- [x] 프론트엔드 Next.js 웹 콘솔 (P4~P4c) — 다크/라이트, 반응형, 인증
- [x] 라이브 영상 게이트웨이 MediaMTX HLS (P5)
- [x] Docker 웹 이미지 패키징 (P6a)
- [ ] Windows/PyInstaller 패키징 (P6 나머지) — [HW] 보류
- [x] 게이트 복구 (lint/mypy/정합) — 2026-06-21
- [x] 문서 동기화 (Streamlit→Next.js) — 2026-06-21
- [x] Next.js 16 proxy.ts 마이그레이션 — 2026-06-21
- [x] 서버사이드 이벤트 필터/페이지네이션 확장 — 2026-06-21
- [x] 프론트엔드 자동화 테스트 기반 도입 (Vitest+RTL, 14 tests) — 2026-06-21
- [x] demo_app.py 테스트 + import guard 견고화 — 2026-06-21

## Phase 10 — 커스텀 모델 학습 (Track B)

- [ ] 데이터셋 준비 (다운로드/변환/검증) (M5) — [DATA] 스크립트 완성, 실행 미검증
- [ ] PPE 모델 학습 + ONNX export (M6) — [HW] 스크립트 완성, 실행 미검증
- [ ] 화재/연기 모델 학습 + ONNX export (M7) — [HW] 스크립트 완성, 실행 미검증
- [ ] 모델 등록 및 설정 갱신 (M8) — 학습 완료 후

## Phase 10 — 최종 검증 (M9)

- [x] `python3 -m pytest tests/ -q -p no:cacheprovider` — 462 passed, 3 skipped
- [x] `ruff check src/ tests/` — All checks passed
- [x] `cd frontend && npm run build` — 성공 (24 라우트)
- [x] `cd frontend && npm run lint` — 에러 0
- [x] `cd frontend && npm run test` (Vitest) — 14 tests passed
- [ ] `scripts/e2e_smoke_test.py --policy-mode` — [HW] 모델 파일 필요
- [ ] `scripts/stress_test.py --duration 60 --policy-mode` — [DATA] sample.mp4 필요
- [ ] `scripts/healthcheck.py --json` — [HW] GPU 검증
- [ ] 웹 콘솔 UI 수동 확인 — [HW] 브라우저

## 잔여 위험

- ✅ 해결: ruamel.yaml round-trip 도입으로 설정 저장 시 주석 보존 (B-5, 2026-06-17)
- 실제 RTSP 카메라 또는 사람 영상 기반 수동 E2E 미완료
- ARM64/DGX Spark 환경에서 `onnxruntime-gpu` 지원 제한 가능
- DGX Spark 통합메모리 제한으로 대형 batch 학습 불가
- 커스텀 모델 학습 품질은 데이터셋 품질에 의존
