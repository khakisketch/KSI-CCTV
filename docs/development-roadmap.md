# 개발 로드맵

## Phase 1. 프로젝트 정의/아키텍처/설정 스키마

목표: 다중 모델/이벤트 정책 기반 플랫폼 방향을 repository 문서와 설정 초안으로 고정합니다.

산출물:

- project-definition.md
- architecture.md
- model-management.md
- event-policy-engine.md
- custom-model-upload.md
- ADR
- model_profiles.yaml
- event_policies.yaml
- camera_profiles.yaml

## Phase 2. MultiModelRegistry 구현

목표: 여러 ONNX 모델을 로드하고 모델별 탐지 결과를 반환합니다.

상태: foundation 구현 진행 완료. `src/detection/model_registry.py`와 기본 단위 테스트가 추가되었습니다.

작업:

- [x] `src/detection/model_registry.py`
- [x] 모델 프로파일 로더
- [x] enabled 모델만 로드
- [x] 모델별 오류 격리
- [x] tests/test_model_registry.py
- [ ] 실제 파이프라인 통합
- [ ] 대시보드 모델 상태 표시
- [ ] 모델별 latency/error metrics 수집

## Phase 3. EventPolicyEngine 구현

목표: 이벤트별 trigger/rule/vlm/alert 정책을 평가합니다.

상태: 독립 foundation 구현 완료. `src/policy/` 패키지와 단위 테스트가 추가되었습니다. 실제 `SafetyPipeline` 통합은 Phase 4에서 진행했습니다.

작업:

- [x] `src/policy/rule_evaluators.py`
- [x] `src/policy/event_policy_engine.py`
- [x] direct_detection, safety_gear_overlap, zone_intrusion, count_threshold, proximity, person_proximity, pose_anomaly 룰
- [x] VLM routing 정책(`never`, `always`, `on_ambiguous`, `on_high_confidence`, `on_low_confidence`)
- [x] tests/test_policy.py

## Phase 4. Pipeline 통합

목표: 단일 YOLO 하드코딩 파이프라인을 정책 기반 파이프라인으로 교체합니다.

상태: 구현 완료. `SafetyPipeline`에 `MultiModelRegistry` + `EventPolicyEngine` 주입 지원, 기존 단일 detector 모드 하위 호환 유지.

작업:

- [x] SafetyEvent dataclass 정식 필드 확장 (`alert_level`, `cooldown_sec`, `vlm_needed`, `source_models`, `rule_name`)
- [x] SafetyPipeline에 model_registry/policy_engine 선택적 주입
- [x] `_process_frame_with_policy()` — MultiModelRegistry + EventPolicyEngine 경로
- [x] `_process_frame_legacy()` — 기존 단일 detector 모드 (하위 호환)
- [x] EventPolicyEngine에서 `_attach_metadata()` 제거, SafetyEvent 생성 시 직접 필드 설정
- [x] tests/test_pipeline.py에 정책 기반 통합 테스트 추가 (8개 신규)
- [x] scripts/e2e_smoke_test.py에 `--policy-mode` 옵션 추가
- [x] 기존 52개 테스트 + 신규 8개 = 총 60개 전량 통과

## Phase 5. 대시보드 설정 UI

목표: 비개발자가 카메라별 이벤트/모델을 선택할 수 있게 합니다.

작업:

- 도메인 템플릿 선택
- 이벤트 체크박스
- 모델 선택
- 정책 미리보기

## Phase 6. 사용자 모델 업로드

목표: ONNX 모델 업로드/검증/등록/카메라 할당을 지원합니다.

작업:

- 업로드 디렉토리 격리
- metadata parser
- ONNX smoke test
- 모델 활성화/비활성화
- 감사 로그

## Phase 7. 운영/보안/납품 강화 ✅

상태: 완료. 설치 스크립트, systemd 서비스 템플릿, 상태 확인 스크립트, 배포 가이드, 사이트별 하드닝 적용.

작업:

- [x] install.sh
- [x] systemd service
- [x] healthcheck
- [x] `.env.example` 배포용 템플릿
- [x] 사이트별 하드닝 (read-only healthcheck, least-privilege systemd paths)
- [x] docs/deployment-guide.md

## Phase 8. 커스텀 안전 모델 학습 및 통합 ⏳

상태: 진행 중. 코드 인프라 준비 완료, 실제 학습은 추후 실행.

작업:

### Milestone 1: 사전학습 모델 통합 ✅
- [x] `src/detection/class_adapter.py` — 클래스 매핑 어댑터
- [x] `scripts/fetch_safety_models.py` — Hugging Face 모델 다운로드/ONNX 변환
- [x] `config/model_profiles.yaml` — ppe_yolo, fire_smoke_yolo 활성화
- [x] `config/event_policies.yaml` — smoke 이벤트 fallback 수정

### Milestone 2: 데이터셋 인프라 ✅
- [x] `scripts/download_datasets.py` — Kaggle/Roboflow/URL 데이터셋 다운로드
- [x] `scripts/convert_to_yolo.py` — VOC/COCO/CSV/Labelme → YOLO 변환
- [x] `scripts/validate_dataset.py` — 데이터셋 무결성 검증
- [x] `scripts/generate_dataset_yaml.py` — Ultralytics 학습 설정 생성
- [x] docs/custom-model-training.md

### Milestone 3: 대시보드 개선 및 문서화 ✅
- [x] 감사 로그 뷰어 탭
- [x] 시스템 상태 탭 (CPU/메모리/디스크/ONNX)
- [x] 이벤트 필터 개선 (alert_level, 시간 범위)
- [x] 모델 상세 상태 표시
- [x] docs/deployment-guide.md
- [x] docs/custom-model-training.md

### Milestone 4: 커스텀 학습 (대기) ⏳
- [ ] scripts/train_ppe.py
- [ ] scripts/train_fire_smoke.py
- [ ] scripts/export_to_onnx.py
- [ ] 실제 데이터셋 다운로드 및 학습 실행
- [ ] 학습된 모델로 사전학습 모델 교체
