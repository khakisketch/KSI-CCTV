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

상태: 구현 완료. `src/detection/model_registry.py`와 단위 테스트, Pipeline 통합, 대시보드 상태 표시, metrics 수집까지 완료되었습니다.

작업:

- [x] `src/detection/model_registry.py`
- [x] 모델 프로파일 로더
- [x] enabled 모델만 로드
- [x] 모델별 오류 격리
- [x] tests/test_model_registry.py
- [x] 실제 파이프라인 통합 (Phase 4)
- [x] 대시보드 모델 상태 표시 (Phase 8 M3)
- [x] 모델별 latency/error metrics 수집 (`get_model_metrics()`, Phase 10)

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

## Phase 5. 대시보드 설정 UI ✅

목표: 비개발자가 카메라별 이벤트/모델을 선택할 수 있게 합니다.

상태: 완료.

작업:

- [x] 도메인 템플릿 선택
- [x] 이벤트 체크박스
- [x] 모델 선택 및 모델 상태 표시
- [x] 정책 미리보기 (alert_level 아이콘, policy metadata 표시)
- [x] `set_camera_profile()` 파이프라인 연동

## Phase 6. 사용자 모델 업로드 ✅

목표: ONNX 모델 업로드/검증/등록/카메라 할당을 지원합니다.

상태: 완료.

작업:

- [x] 업로드 디렉토리 격리 (`models/uploaded/{model_id}/`)
- [x] metadata parser + ONNX 로드 검증
- [x] ONNX smoke test (입력 shape 검증 + 합성 추론)
- [x] 모델 활성화/비활성화/삭제 (삭제 확인 체크박스 포함)
- [x] 감사 로그 (업로드/활성화/비활성화/삭제 기록)
- [x] `src/models/upload_validator.py` + `tests/test_upload_validator.py`

## Phase 7. 운영/보안/납품 강화 ✅

상태: 완료. 설치 스크립트, systemd 서비스 템플릿, 상태 확인 스크립트, 배포 가이드, 사이트별 하드닝 적용.

작업:

- [x] install.sh
- [x] systemd service
- [x] healthcheck
- [x] `.env.example` 배포용 템플릿
- [x] 사이트별 하드닝 (read-only healthcheck, least-privilege systemd paths)
- [x] docs/deployment-guide.md

## Phase 8. Safety 모델 통합 ✅

> **1차 모델 전략**: 적합 공개 pretrained 모델 큐레이션 + 사용자 업로드 ONNX 모델.
> `MultiModelRegistry` / `class_adapter` / 업로드 검증(`upload_validator`) / ONNX 메타데이터-우선 클래스 인식이 이 전략을 지원한다.
>
> **from-scratch 커스텀 학습은 project-definition.md 명시적 비목표(현재 버전)다.**
> 데이터셋/GPU 인프라가 준비된 경우의 선택적 후속 작업으로 분리해 Phase D(`docs/phase-cd-readiness.md`)에 runbook을 두었다.

상태: 핵심 모델 통합 완료. from-scratch 학습(M4)은 선택/후순위로 분류.

작업:

### Milestone 1: 공개 pretrained 모델 큐레이션 및 통합 ✅

> **현재 운영 모델 전략** — 학습 없이 바로 사용 가능한 공개 pretrained ONNX 모델을 큐레이션해 연동한다.
> 사용자는 동일 경로로 자체 ONNX 모델을 업로드해 대체할 수 있다(Phase 6 사용자 업로드).

- [x] `src/detection/class_adapter.py` — 클래스 매핑 어댑터 (ONNX 메타데이터 우선, config fallback)
- [x] `scripts/fetch_safety_models.py` — Hugging Face 공개 모델 다운로드/ONNX 변환 (ppe_yolo, fire_smoke_yolo)
- [x] `config/model_profiles.yaml` — ppe_yolo(`Hardhat/NO-Hardhat`), fire_smoke_yolo(`fire`) 활성화
- [x] `config/event_policies.yaml` — smoke 이벤트 fallback 수정

### Milestone 2: 데이터셋 인프라 ✅

> 공개 데이터셋 탐색·변환·검증 도구. from-scratch 학습이 필요해지는 경우의 사전 인프라로도 활용 가능.

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

### Milestone 4: from-scratch 커스텀 학습 (선택/후순위) ⏳

> **비고**: `project-definition.md`의 명시적 비목표(자동 모델 학습 파이프라인 — 현재 버전).
> 1차 전략(공개 pretrained + 사용자 업로드)으로 커버되지 않는 현장 정확도 요구가 확인된 경우에만 진행한다.
> 실행 절차는 `docs/phase-cd-readiness.md` Phase D를 따른다.

- [ ] 실제 데이터셋 다운로드 및 학습 실행 (인터넷/GPU 환경 + 라이선스 확인 필요)
- [ ] 검증셋 mAP 측정 (`docs/phase-cd-readiness.md` D5 합격 기준 충족)
- [ ] 학습된 ONNX 모델로 pretrained 모델 교체 및 e2e smoke test 재검증
