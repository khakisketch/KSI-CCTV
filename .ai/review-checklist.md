# KSI-CCTV 리뷰 체크리스트

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

## 배포 준비 (Phase 7 완료 ✅)

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

- [ ] `.ai/*` 문서 동기화 (M1)
- [ ] Git init + LFS 설정 (M2)
- [ ] DGX Spark CUDA provider 검증 (M3)
- [ ] RTSP 재연결 시뮬레이션 (M4)

## Phase 10 — 커스텀 모델 학습 (Track B)

- [ ] 데이터셋 준비 (다운로드/변환/검증) (M5)
- [ ] PPE 모델 학습 + ONNX export (M6)
- [ ] 화재/연기 모델 학습 + ONNX export (M7)
- [ ] 모델 등록 및 설정 갱신 (M8)

## Phase 10 — 최종 검증 (M9)

- [ ] `python3 -m compileall -q src tests scripts`
- [ ] `./.venv/bin/python -m unittest discover tests -v`
- [ ] `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode`
- [ ] `./.venv/bin/python scripts/stress_test.py --duration 60 --policy-mode`
- [ ] `./.venv/bin/python scripts/profile_pipeline.py --cprofile`
- [ ] `./.venv/bin/python scripts/healthcheck.py --json`
- [ ] 대시보드 UI 수동 확인

## 잔여 위험

- PyYAML safe_dump로 `config/model_profiles.yaml` runtime rewrite 시 comment/format 보존 안 됨
- 실제 RTSP 카메라 또는 사람 영상 기반 수동 E2E 미완료
- ARM64/DGX Spark 환경에서 `onnxruntime-gpu` 지원 제한 가능
- DGX Spark 통합메모리 제한으로 대형 batch 학습 불가
- 커스텀 모델 학습 품질은 데이터셋 품질에 의존
