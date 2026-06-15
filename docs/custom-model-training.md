# KSI-CCTV 커스텀 모델 학습 가이드

이 문서는 KSI-CCTV를 위해 커스텀 안전 모델(PPE, 화재/연기 등)을 학습하고 ONNX로 변환하는 방법을 설명합니다.

## 학습 파이프라인 개요

```
데이터셋 다운로드 → 형식 변환 → 검증 → 학습 → ONNX 변환 → 등록
```

## 1. 데이터셋 준비

### 1.1. 데이터셋 다운로드

```bash
# PPE(안전모/조끼) 데이터셋
python scripts/download_datasets.py --dataset ppe

# 화재/연기 데이터셋
python scripts/download_datasets.py --dataset fire_smoke

# 사용 가능한 데이터셋 목록 확인
python scripts/download_datasets.py --list
```

### 1.2. 형식 변환

```bash
# 다운로드된 데이터를 YOLO 형식으로 변환
python scripts/convert_to_yolo.py \
    --input-dir data/ppe/hard_hat_workers \
    --format auto \
    --output-dir data/ppe/yolo

# COCO 형식인 경우
python scripts/convert_to_yolo.py \
    --input-dir data/fire_smoke/fire_detection \
    --format coco \
    --output-dir data/fire_smoke/yolo
```

지원 형식: `voc`, `coco`, `csv`, `labelme`, `yolo`

### 1.3. 데이터셋 검증

```bash
# 데이터셋 무결성 검증
python scripts/validate_dataset.py \
    --dataset-dir data/ppe/yolo \
    --num-classes 3 \
    --report

# 상세 리포트 생성
python scripts/validate_dataset.py \
    --dataset-dir data/fire_smoke/yolo \
    --report --output data/fire_smoke/validation_report.md
```

검증 항목:
- 이미지/라벨 파일 쌍 일치 여부
- 라벨 형식 (class_id, x_center, y_center, width, height)
- 정규화 값 범위 [0, 1]
- 손상된 이미지 감지
- 클래스 분포 통계

### 1.4. 학습 설정 생성

```bash
# Ultralytics YOLO 학습용 dataset.yaml 생성
# 클래스명·순서는 KSI 표준(docs/phase-cd-readiness.md D0 SSOT)을 그대로 따른다.
# PPE는 5-class: person / helmet / no_helmet / vest / no_vest
# (train_ppe.py도 5-class를 전제. 표준명으로 학습하면 ONNX names 메타데이터로 정책에 바로 연결됨.)
python scripts/generate_dataset_yaml.py \
    --dataset-dir data/ppe/yolo \
    --classes person helmet no_helmet vest no_vest

# 출력: data/ppe/yolo/dataset.yaml
```

> **표준 클래스 SSOT 및 학습→배포 전 과정의 정량 합격 기준은 `docs/phase-cd-readiness.md`(Phase D)를 따른다.**
> 학습한 모델은 표준 클래스명으로 export하면 `class_map` 없이 `config/event_policies.yaml`의
> `no_helmet`·`no_vest`·`fire`·`smoke`·`fall` 이벤트에 그대로 연결된다(no_vest는 학습 후 `enabled: true` 재활성).

## 2. 모델 학습

학습 스크립트는 `scripts/train_ppe.py`와 `scripts/train_fire_smoke.py`로 작성되어 있습니다.  
**실행을 위해서는 YOLO 포맷 데이터셋과 GPU(CUDA)가 필요합니다.**  
데이터셋 없이는 실행되지 않습니다 (스크립트 상단 주석에 명시됨).

### 2.1. PPE 모델 학습

```bash
# scripts/train_ppe.py — 완전한 학습 스크립트 (254 lines)
# 필수: PPE 데이터셋(person/helmet/no_helmet/vest/no_vest YOLO 포맷), GPU
./.venv/bin/python scripts/train_ppe.py \
    --data /data/ppe_dataset/data.yaml \
    --model yolo11n.pt \
    --epochs 50 \
    --batch 8     # DGX Spark 통합메모리 제한 — batch=-1 절대 금지
```

### 2.2. 화재/연기 모델 학습

```bash
# scripts/train_fire_smoke.py — 완전한 학습 스크립트 (266 lines)
# 필수: fire/smoke 데이터셋, GPU
./.venv/bin/python scripts/train_fire_smoke.py \
    --data /data/fire_smoke_dataset/data.yaml \
    --model yolo11n.pt \
    --epochs 50 \
    --batch 8
```

### 2.3. 학습 모니터링

```bash
# TensorBoard로 학습 진행 상황 확인
tensorboard --logdir models/training

# 또는 Ultralytics CLI
yolo task=detect mode=train model=yolo11n.pt data=data/ppe/yolo/dataset.yaml epochs=50
```

## 3. ONNX 변환

### 3.1. 학습된 모델 내보내기

```bash
# scripts/export_to_onnx.py — 완전한 변환 스크립트 (272 lines)
./.venv/bin/python scripts/export_to_onnx.py \
    --model models/training/ppe_yolo/weights/best.pt \
    --output models/yolo26_ppe.onnx
```

### 3.2. 변환 검증

```bash
# ONNX 모델 검증
python -c "
import onnxruntime as ort
import numpy as np

sess = ort.InferenceSession('models/training/ppe_yolo/weights/best.onnx')
inputs = sess.get_inputs()
outputs = sess.get_outputs()

print('Inputs:', [(i.name, i.type, i.shape) for i in inputs])
print('Outputs:', [(o.name, o.type, o.shape) for o in outputs])

# 추론 테스트
dummy = np.random.randn(1, 3, 640, 640).astype(np.float16)
results = sess.run(None, {inputs[0].name: dummy})
print('Output shapes:', [r.shape for r in results])
"
```

## 4. 모델 등록

### 4.1. 파일 이동

```bash
# ONNX 파일을 models/ 디렉토리로 이동
cp models/training/ppe_yolo/weights/best.onnx models/yolo26_ppe.onnx
cp models/training/fire_smoke_yolo/weights/best.onnx models/yolo26_fire_smoke.onnx
```

### 4.2. 설정 파일 갱신

`config/model_profiles.yaml`에서 모델을 활성화합니다:

```yaml
ppe_yolo:
  enabled: true
  type: yolo_onnx
  model_path: "./models/yolo26_ppe.onnx"
  class_profile: safety_custom
  classes: [person, helmet, vest]
  # ...

fire_smoke_yolo:
  enabled: true
  type: yolo_onnx
  model_path: "./models/yolo26_fire_smoke.onnx"
  classes: [fire, smoke]
  # ...
```

### 4.3. 상태 확인

```bash
# healthcheck 실행
python scripts/healthcheck.py --json

# 대시보드에서 모델 상태 확인
# http://localhost:8080 → "시스템 상태" 탭
```

## 학습 팁

### 데이터셋 품질

- **이미지 다양성**: 다양한 각도, 조명, 배경의 이미지 포함
- **클래스 균형**: 각 클래스의 이미지 수가 비슷하도록
- **라벨 정확도**: bbox가 객체를 정확히 포함하는지 확인
- **최소 이미지 수**: 클래스당 최소 100~200장 권장

### 하이퍼파라미터

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| epochs | 100 | 학습 반복 횟수 |
| imgsz | 640 | 입력 이미지 크기 |
| batch | 16 | 배치 크기 (GPU 메모리에 따라 조정) |
| lr0 | 0.01 | 초기 학습률 |
| lrf | 0.01 | 최종 학습률 계수 |
| momentum | 0.937 | SGD 모멘텀 |
| weight_decay | 0.0005 | 가중치 감쇠 |

### GPU 메모리 부족 시

```python
# 배치 크기 감소
model.train(data="dataset.yaml", epochs=100, batch=8, imgsz=640)

# 더 작은 모델 사용
model = YOLO("yolov8n.pt")  # nano (가장 작음)
# model = YOLO("yolov8s.pt")  # small
# model = YOLO("yolov8m.pt")  # medium
```

## 고급: 전이학습

### 사전학습된 모델 파인튜닝

```bash
# 공개 모델 다운로드
python scripts/fetch_safety_models.py --model ppe_yolo

# 해당 모델을 기반으로 파인튜닝
python -c "
from ultralytics import YOLO

model = YOLO('models/yolo26_ppe.onnx')  # ONNX 모델은 직접 로드 불가
# 대신 PyTorch .pt 파일이 필요함
"
```

## 참고

- [Ultralytics YOLO 문서](https://docs.ultralytics.com/)
- [ONNX Runtime 문서](https://onnxruntime.ai/docs/)
- [배포 가이드](deployment-guide.md)
