# KSI-CCTV 개발 계획

## 전체 로드맵

| Phase | 내용 | 상태 |
|---|---|---|
| 0 | 프로토타입 (단일 YOLO + VLM + 대시보드) | 완료 |
| 1 | 프로젝트 정의/아키텍처/설정 스키마 | 완료 |
| 2 | MultiModelRegistry 구현 | 완료 |
| 3 | EventPolicyEngine 구현 | 완료 |
| 4 | Pipeline 통합 (SafetyPipeline + policy) | 완료 |
| 5 | 대시보드 설정 UI (카메라/도메인/이벤트 선택) | 완료 |
| 6 | 사용자 ONNX 모델 업로드 (검증/격리/disabled 등록) | 완료 |
| 7 | 운영/배포/보안 강화 | 완료 |
| 8 | Custom Safety Model + 대시보드/알림 개선 | 완료 |
| 9 | 개발 개선 (observability, stress test, profiling) | 완료 |
| 10 | **생산 준비 + 커스텀 모델 학습** | **진행 중** |

## Phase 10 — 생산 준비 + 커스텀 모델 학습

### 목표
KSI-CCTV를 실제 배포 가능한 상태로 완성합니다. 두 개의 병행 트랙으로 진행합니다.

- **Track A**: `.ai/*` 문서 갱신 + Git 초기화 + LFS 설정 + CUDA 검증 + RTSP 재연결 시뮬레이션
- **Track B**: 커스텀 안전 모델 학습 (PPE + 화재/연기) → ONNX export → 기존 프로파일 덮어쓰기 등록

### Track A — 생산 준비 (Production Readiness)

#### M1: `.ai/*` 문서 동기화
**목표**: `.ai/*` 계획 문서들이 현재 실제 완료 상태를 반영하도록 갱신

- `plan.md`: Phase 7, 8, 9 완료 상태로 업데이트 (완료)
- `project-brief.md`: 모델 상태 테이블을 ppe_yolo/fire_smoke_yolo active로 갱신
- `review-checklist.md`: Phase 7, 8, 9 완료 항목 체크, Phase 10 항목 추가
- `decisions.md`: Phase 8/9 주요 결정사항(class_adapter, fire 모델 repo 대체, VLM cache, dynamic_skip 등) 기록
- `docs/development-roadmap.md`: Phase 7, 8, 9 완료 상태로 갱신

**수용 기준**:
- 모든 `.ai/*` 파일이 현재 실제 상태와 일치
- `docs/development-roadmap.md`가 최신 상태 반영

**위험 게이트**: 없음 (읽기 전용 검증)

#### M2: Git 초기화 + LFS 설정
**목표**: 프로젝트를 Git 저장소로 초기화하고 모델 파일을 LFS로 관리

- `git init`
- `.gitignore` 작성 (`.env`, `__pycache__`, `logs/`, `data/captures/`, `models/uploaded/`, `reports/`, `.venv/` 등)
- `.gitattributes` 작성 (`.onnx` → LFS, `.pt` → LFS)
- `git lfs install` 및 `git lfs track "*.onnx" "*.pt"`
- 초기 커밋: 소스 코드 + 설정 + 문서 (모델 파일은 LFS로)
- `scripts/healthcheck.py` 실행 확인

**수용 기준**:
- `git status`가 정상
- `.onnx` 파일이 LFS로 트래킹됨
- `.env` 파일이 `.gitignore`에 포함됨
- `python3 -m compileall -q src tests scripts` 통과

**위험 게이트**:
- Git LFS 미설치 시 `apt install git-lfs` 필요
- 초기 커밋에 비밀번호/URL이 포함되지 않도록 주의

#### M3: DGX Spark CUDA provider 검증
**목표**: 실제 GPU 환경에서 ONNX Runtime CUDA provider 동작 확인

- `scripts/healthcheck.py --require-gpu` 실행
- CUDA provider로 추론 시 latency 측정 (CPU vs GPU 비교)
- `scripts/profile_pipeline.py` GPU 모드 실행
- `batch` 크기 제한 검증 (통합메모리 OOM 방지)

**수용 기준**:
- `healthcheck.py --require-gpu`가 `ok: true` 반환
- GPU 추론 latency가 CPU 대비 유의미하게 낮음
- `batch=-1` 사용 시 경고 또는 제한

**위험 게이트**:
- DGX Spark ARM64 + CUDA 13 환경에서 `onnxruntime-gpu` 호환성
- 통합메모리 제한으로 대형 배치 사용 불가

#### M4: RTSP 재연결 시뮬레이션 (Phase 9 M3.5 백로그)
**목표**: RTSP 연결 끊김/재연결 시나리오에 대한 시뮬레이션 테스트 추가

- `scripts/stress_test.py`에 `--simulate-disconnect` 옵션 추가
- 일정 간격으로 연결 끊김/재연결 시뮬레이션
- 재연결 후 파이프라인 정상 복구 확인
- `CameraManager`에 재연결 로직 검증

**수용 기준**:
- 연결 끊김 시뮬레이션에서 `failed_frames`가 0이 아니지만 복구됨
- 재연결 후 정상 프레임 처리 재개
- 로그에 연결 끊김/복구 이벤트 기록

**위험 게이트**:
- 실제 RTSP 장비 없음 → 시뮬레이션 기반
- `CameraManager.reconnect()` 메서드 구현 필요

### Track B — 커스텀 모델 학습 (Custom Model Training)

#### M5: 데이터셋 준비
**목표**: PPE 및 화재/연기 데이터셋을 다운로드하고 YOLO 형식으로 변환/검증

- `python scripts/download_datasets.py --dataset ppe` 실행
- `python scripts/download_datasets.py --dataset fire_smoke` 실행
- `python scripts/convert_to_yolo.py`로 YOLO 형식 변환
- `python scripts/validate_dataset.py`로 무결성 검증
- `python scripts/generate_dataset_yaml.py`로 학습 설정 생성

**수용 기준**:
- `data/ppe/yolo/dataset.yaml` 생성됨
- `data/fire_smoke/yolo/dataset.yaml` 생성됨
- 데이터셋 검증 통과 (손상 이미지 없음, 클래스 분포 정상)

**위험 게이트**:
- Kaggle/Roboflow API 키 필요 (환경 변수로 설정)
- 네트워크 연결 필요
- 디스크 공간 (데이터셋 크기에 따라 1~5GB)

#### M6: PPE 모델 학습 + ONNX export
**목표**: 안전모/조끼 감지 커스텀 YOLO 모델 학습 및 ONNX 변환

- `scripts/train_ppe.py` 작성 (Ultralytics YOLO 학습 스크립트)
- DGX Spark GPU에서 학습 실행 (batch 크기 제한 적용)
- 학습된 `.pt` 모델을 ONNX로 export (`half=True`, `simplify=True`)
- ONNX 모델 검증 (shape, inference smoke test)
- `models/yolo26_ppe.onnx` 덮어쓰기 (기존 사전학습 모델 대체)
- `config/model_profiles.yaml`에서 `ppe_yolo.source`를 `custom_trained`로 변경

**수용 기준**:
- 학습 완료 (mAP@0.5 기준 목표치 달성)
- ONNX export 성공, `onnxruntime` 로드 가능
- `scripts/e2e_smoke_test.py --policy-mode`에서 ppe_yolo 정상 추론
- 기존 class_map(`Hardhat`→`helmet`)이 더 이상 필요 없으면 제거 또는 업데이트

**위험 게이트**:
- DGX Spark 통합메모리 제한 → `batch=8` 또는 `batch=4`로 고정
- `batch=-1` 절대 금지
- 학습 시간 (epochs=100, batch=16 → 수 시간 소요 예상)
- ONNX export 시 `half=True`가 FP16 미지원 모델에서 실패할 수 있음 → `half=False` 폴백

#### M7: 화재/연기 모델 학습 + ONNX export
**목표**: 화재/연기 감지 커스텀 YOLO 모델 학습 및 ONNX 변환

- `scripts/train_fire_smoke.py` 작성
- DGX Spark GPU에서 학습 실행
- ONNX export 및 검증
- `models/yolo26_fire_smoke.onnx` 덮어쓰기
- `config/model_profiles.yaml`에서 `fire_smoke_yolo.source`를 `custom_trained`로 변경
- class_map 업데이트 (커스텀 모델은 smoke 직접 감지 가능)

**수용 기준**:
- 학습 완료
- ONNX export 성공
- smoke 클래스 직접 감지 가능 (VLM fallback 불필요)
- `event_policies.yaml`의 smoke 이벤트 trigger를 `fire_smoke_yolo`로 변경

**위험 게이트**:
- 화재/연기 데이터셋 품질 (실제 화재 영상 부족 가능)
- 클래스 불균형 (fire > smoke)

#### M8: 모델 등록 및 설정 갱신
**목표**: 학습된 커스텀 모델을 시스템에 정식 등록하고 설정 갱신

- `config/model_profiles.yaml`:
  - `ppe_yolo.source`: `pre_trained` → `custom_trained`
  - `fire_smoke_yolo.source`: `pre_trained` → `custom_trained`
  - `fire_smoke_yolo.classes`: `[fire, smoke]` (smoke 직접 감지)
  - 불필요한 class_map 정리
- `config/event_policies.yaml`:
  - smoke 이벤트 trigger를 `fire_smoke_yolo`로 변경 (VLM fallback 제거)
- `docs/custom-model-training.md`에 학습 결과 기록 (mAP, epoch, batch 등)

**수용 기준**:
- `config/model_profiles.yaml`이 커스텀 모델 정보를 정확히 반영
- smoke 이벤트가 `fire_smoke_yolo` 직접 감지로 변경됨
- `docs/custom-model-training.md`에 학습 이력 기록

#### M9: 최종 검증
**목표**: 전체 시스템 통합 검증

- `python3 -m compileall -q src tests scripts`
- `./.venv/bin/python -m unittest discover tests -v`
- `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode`
- `./.venv/bin/python scripts/stress_test.py --duration 60 --policy-mode`
- `./.venv/bin/python scripts/profile_pipeline.py --cprofile`
- `./.venv/bin/python scripts/healthcheck.py --json`
- `bash scripts/run_dashboard.sh` (수동 UI 확인)

**수용 기준**:
- 모든 검증 통과
- `failed_models={}`
- `failed_frames=0`
- 대시보드에서 모든 모델 active 표시

## Phase 10 마일스톤 우선순위

| 순서 | 마일스톤 | 트랙 | 예상 소요 |
|---|---|---|---|
| 1 | M1: `.ai/*` 문서 동기화 | A | 30분 |
| 2 | M2: Git init + LFS | A | 30분 |
| 3 | M3: CUDA 검증 | A | 1시간 |
| 4 | M5: 데이터셋 준비 | B | 1~2시간 |
| 5 | M6: PPE 모델 학습 | B | 2~4시간 |
| 6 | M7: 화재/연기 모델 학습 | B | 2~4시간 |
| 7 | M8: 모델 등록 | B | 30분 |
| 8 | M4: RTSP 재연결 시뮬레이션 | A | 1시간 |
| 9 | M9: 최종 검증 | A+B | 30분 |

## 검증 전략

모든 Phase에서:
1. `python3 -m compileall -q src tests scripts`
2. `./.venv/bin/python -m unittest discover tests -v`
3. `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode`
4. `./.venv/bin/python scripts/healthcheck.py --json`
5. 실제 RTSP 카메라 또는 사람 영상 기반 수동 E2E (가능할 때)

## 계획 소유 아티팩트

- `.ai/project-brief.md` — plan 소유
- `.ai/plan.md` — plan 소유
- `.ai/decisions.md` — plan 소유
- `.ai/review-checklist.md` — plan 소유
- `.ai/task-state.md` — build 소유
- `.ai/handoff.md` — 공유 (phase handoff)
