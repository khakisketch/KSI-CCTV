# 모델 관리 설계

## 목표

KSI-CCTV는 기본 제공 모델뿐 아니라 고객이 직접 준비한 ONNX 모델도 등록해 사용할 수 있어야 합니다. 모델 관리는 **Model Profile**을 중심으로 합니다.

## 모델 유형

### 기본 제공 모델

| 모델 | 역할 | 상태 |
|---|---|---|
| general_yolo | 사람/차량/일반 객체 | 현재 사용 가능 |
| ppe_yolo | 안전모/조끼 | 학습 후 제공 |
| fire_smoke_yolo | 화재/연기 | 학습 후 제공 |
| pose_fall_model | 쓰러짐/자세 이상 | 향후 제공 |
| vlm | 자연어 설명/애매한 이벤트 확인 | 현재 Ollama 사용 가능 |

### 사용자 업로드 모델

초기 버전에서는 ONNX 모델만 지원합니다.

허용:

- `.onnx`
- `classes.yaml`
- `metadata.yaml`

금지:

- `.pt`
- `.pkl`
- Python 코드 포함 모델
- 실행 파일

## Model Profile 필드

```yaml
model_name:
  enabled: true
  type: yolo_onnx
  source: built_in | uploaded
  model_path: ./models/model.onnx
  class_profile: coco | safety_custom | custom
  classes: [person, helmet, vest]
  input_size: [640, 640]
  conf_threshold: 0.25
  iou_threshold: 0.45
  postprocess_mode: auto
  device: cuda
  fp16: true
  description: "..."
```

## 모델 등록 흐름

1. 모델 파일 업로드 또는 기본 모델 선택
2. metadata/classes 입력
3. ONNX 로드 가능 여부 검사
4. input/output shape 검사
5. 샘플 이미지 smoke test
6. 성공 시 model_profiles에 등록
7. 카메라 프로파일에서 모델 선택

## 버전/롤백

사용자 업로드 모델은 다음 구조로 저장합니다.

```text
models/uploaded/{model_id}/
  model.onnx
  metadata.yaml
  smoke_report.json
```

향후 활성/이전 버전을 관리합니다.

```yaml
active_version: v2
versions:
  v1: ...
  v2: ...
```

## 운영 원칙

- 모델 로드 실패는 전체 앱 장애로 이어지면 안 됩니다.
- 업로드 모델은 기본 disabled 상태로 등록하고 smoke test 성공 후 활성화합니다.
- 모델별 latency, detection count, error count를 수집해야 합니다.

## 현재 구현 상태 (Phase 10 기준 — 통합 완료)

- `src/detection/model_registry.py`에 `MultiModelRegistry` 구현 완료
- `config/model_profiles.yaml`의 `enabled: true` + `type: yolo_onnx` 모델만 로드
- 개별 모델 로드 실패는 `failed_models`와 `load_status`에 기록 (전체 앱 장애 없음)
- `detect_all(image)` 및 `detect_model(model_name, image)` — 모두 `class_map` 적용 후 반환
- `get_model_metrics()` — 모델별 latency/error metrics 공개 API
- **ONNX 메타데이터 자동 클래스 인식**: `classes: null`이면 ONNX `names` 메타데이터 우선 사용. config와 모델 클래스 수 불일치 시 메타데이터를 신뢰하고 경고 출력.
- **SafetyPipeline 통합 완료** (Phase 4): `SafetyPipeline(model_registry=..., policy_engine=...)`으로 주입. 대시보드 `start_pipeline()`이 기본으로 policy-mode를 사용 (Phase 10 M0).
- 사용자 업로드 모델: `src/models/upload_validator.py`가 ONNX 전용 업로드·검증·격리 저장·`enabled: false` 등록을 담당. 대시보드 "모델 업로드" 탭에서 관리.
- 활성 사전학습 모델:
  - `general_yolo`: `models/yolo26.onnx` (COCO 80클래스)
  - `ppe_yolo`: `models/yolo26_ppe.onnx` (Hardhat/NO-Hardhat — `scripts/fetch_safety_models.py --all`로 다운로드)
  - `fire_smoke_yolo`: `models/yolo26_fire_smoke.onnx` (fire 클래스 — smoke는 VLM 보조)
