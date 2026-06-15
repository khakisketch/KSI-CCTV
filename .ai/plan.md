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
| 10 | **생산 준비 + 커스텀 모델 학습** | **진행 중 (M0+M1 완료, 전체 단위 테스트 green)** |

## Phase 10 — 생산 준비 + 커스텀 모델 학습

### 목표
KSI-CCTV를 실제 배포 가능한 상태로 완성합니다. 두 개의 병행 트랙으로 진행합니다.

- **Track A**: `.ai/*` 문서 갱신 + Git 초기화 + LFS 설정 + CUDA 검증 + RTSP 재연결 시뮬레이션
- **Track B**: 커스텀 안전 모델 학습 (PPE + 화재/연기) → ONNX export → 기존 프로파일 덮어쓰기 등록

### 2026-06-10 재정렬 — 현재 코드 기준 우선순위

최근 코드 감사 결과, Phase 10의 CUDA/데이터셋/학습보다 먼저 해결해야 할 **제품 실행 경로 drift**가 확인되었습니다. 대시보드가 실제로는 legacy 단일 YOLO 경로로 `SafetyPipeline`을 시작하고 있어, 이미 구현된 `MultiModelRegistry` + `EventPolicyEngine` + camera profile 선택 UI가 사용자 실행 경로에 연결되지 않습니다. 따라서 Phase 10의 첫 build milestone은 아래 M0로 재정렬합니다.

#### M0: Policy-mode 실행 경로 복구 + 통합 정확성 정리 (현재 build milestone)

**목표**: 실제 대시보드/파이프라인 실행 경로가 목표 아키텍처(`MultiModelRegistry → EventPolicyEngine → VLM 필요 시 분석`)와 일치하도록 만들고, 감사에서 확인된 통합 정확성 결함을 한 번에 닫습니다.

**범위**:
- `src/dashboard/app.py`
  - `start_pipeline()`에서 기본 실행 경로를 policy-mode로 전환합니다.
  - `MultiModelRegistry.from_config()`와 `EventPolicyEngine.from_config()`를 생성해 `SafetyPipeline(model_registry=..., policy_engine=...)`에 주입합니다.
  - legacy single-detector 경로는 명시적 fallback 또는 오류 시 fallback으로만 유지합니다.
  - 시작 시 카메라별 `domain`, `enabled_events`, `zones`, `detection_type` profile을 `pipeline.set_camera_profile()`로 적용합니다.
  - policy-mode의 최신 탐지 결과가 실시간 스트림/오버레이에 표시되도록 `latest_detections` key 불일치(`model_name` vs `camera_id`)를 정리합니다.
- `src/detection/model_registry.py`
  - `detect_all()` 결과에도 `class_map`을 적용합니다. 현재는 `detect_model()`만 `adapt_detections()`를 적용합니다.
- `src/pipeline/pipeline.py`
  - VLM TTL cache가 실제로 hit/miss/bypass를 판단하도록 연결합니다.
  - `critical/high`는 cache bypass, 그 외 동일 `(camera_id, event_type)` TTL 내 이벤트는 중복 VLM 제출을 피합니다.
- `src/alerts/alert_manager.py` 및/또는 `src/dashboard/app.py`
  - `EVENT | {json}` 로그 라인의 JSON 파싱을 실제 formatter 출력(`timestamp | level | EVENT | {json}`)에 맞게 수정합니다.
  - 가능하면 파서 helper를 분리해 dashboard와 `AlertManager.get_event_log()`가 같은 규칙을 사용하게 합니다.
- `.gitignore` / `.gitattributes`
  - `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `htmlcov/`, `.coverage*` 등 개발 산출물이 git status에 잡히지 않게 정리합니다.
  - 모델 파일은 ignore하지 않고 `.gitattributes`의 LFS tracking 대상임을 유지합니다.

**비범위**:
- CUDA provider 문제 해결, `onnxruntime-gpu` 소스 빌드, TensorRT 변환
- 데이터셋 다운로드/변환/학습 실행
- RTSP 재연결 시뮬레이션 구현
- systemd/Docker 구조 변경
- 실제 커밋/푸시

**수용 기준**:
- 대시보드 시작 시 policy-mode가 기본으로 사용되고 `pipeline.use_policy_mode == True`가 확인됩니다.
- 활성 모델 로드 실패는 사용자에게 보이되, 의도하지 않은 legacy 경로로 조용히 숨어들지 않습니다.
- `detect_all()`이 `class_map`을 적용해 PPE/fire 모델의 공개 클래스명이 정책 엔진 표준 클래스명으로 변환됩니다.
- VLM cache stats의 `hits`, `misses`, `bypasses`가 실제 제출 판단에 따라 증가합니다.
- 이벤트 로그 뷰어와 `AlertManager.get_event_log()`가 실제 `alerts.log` 라인을 파싱합니다.
- `git status --short`에서 `.venv/` 같은 개발 산출물이 더 이상 noise로 보이지 않습니다. 단, `git lfs` 미설치 자체는 build가 설치하지 말고 blocker로 보고합니다.
- 아래 검증이 통과하거나, 외부 환경 문제는 명확히 non-blocking으로 기록됩니다.

**검증**:
1. `python3 -m compileall -q src tests scripts`
2. `./.venv/bin/python -m unittest discover tests -v` 또는 현재 프로젝트 표준 pytest 명령
3. `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode`
4. 가능한 경우 `./.venv/bin/python scripts/stress_test.py --duration 10 --policy-mode`
5. `./.venv/bin/python scripts/healthcheck.py --json`
6. `git status --short`로 개발 산출물 ignore 확인
7. `git lfs ls-files`는 실행해 보고, `git-lfs` 미설치 시 설치하지 말고 blocker로 기록

**리뷰 게이트**: 이 milestone은 대시보드 실행 경로와 다중 모델 정책 아키텍처를 바꾸는 사용자-visible/architecture change입니다. build 완료 후 review required.

#### M0 이후 재개 순서
1. M1: `.ai/*` 및 문서 상태 동기화 — M0 결과를 반영해 계획/체크리스트 업데이트
2. M2: Git/LFS 최종 정리 — `git-lfs` 설치 가능 여부 확인 후 모델 파일 tracking 검증
3. M3: CUDA provider 검증 — ARM64/CUDA 13 제약을 명시하고 CPU fallback 기준 확정
4. M5~M8: 데이터셋 준비/학습/모델 등록
5. M4: RTSP 재연결 시뮬레이션
6. M9: 최종 통합 검증

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

## Phase 10 기존 마일스톤 우선순위 (M0 완료 전에는 보류)

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

## 진행 메모 (2026-06-14)

- M0 이후: **탐지 클래스 정합성**(ONNX 메타데이터 기반 자동 인식, 근본 수정) + **Phase A 잔여 결함**(정책 enabled_events·크로스모델 스코핑 · 파이프라인 thread-safety·이미지 메모리 · alert cleanup · docker 볼륨 · dashboard dead code · ruff) 완료. 116 tests green(캐시클린), 실모델·실영상 종단 검증.
- 다음 우선순위: **Phase B**(실 ONNX+VLM 종단 자동검증 게이트, 장비 불필요) → 장비 확보 시 **Phase C**(DGX CUDA·실 RTSP 검증) → **Phase D**(커스텀 모델 학습, Track B).
