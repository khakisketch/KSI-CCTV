# 공개 사전학습 모델 소싱 가이드

> KSI-CCTV는 **커스텀 학습이 비목표**다(project-definition). 안전 탐지 능력은 **현장에 맞는 공개
> pretrained 모델을 큐레이션해 꽂는** 방식으로 확보한다. 이 문서는 조사·검증된 후보와 교체(turnkey)
> 절차를 담는다. (조사 시점: 2026-06, HTTP 접근성 실검증 포함.)

## ⚠️ 라이선스 경고 (반드시 먼저 결정)

YOLOv8/v9/v10/v11로 학습된 **거의 모든 공개 가중치는 Ultralytics 기준 AGPL-3.0 파생물**이다.
repo가 README에 "MIT/Apache"라 적어도 그건 *코드*에만 적용되고 **가중치의 AGPL 의무는 무효화하지
못한다**. 상용/폐쇄 배포 시 선택지:
1. **전체 시스템(앱+스크립트+설정)을 AGPL-3.0으로 공개**, 또는
2. Ultralytics **Enterprise License** 구매, 또는
3. CC BY 등 허용 라이선스 데이터로 비-Ultralytics 학습(=별건, 비목표와 충돌).

→ **KSI-CCTV의 배포 형태(폐쇄망 내부 운영 vs 외부 납품)에 따른 AGPL 의무를 법무/상용 정책으로 먼저
확정**해야 한다. 아래 후보는 모두 이 경고가 적용된다(예외: 데이터가 CC BY인 fall 데이터셋).

## 후보 모델 (출고 승인 전 사용 금지)

아래 항목은 sourcing 후보일 뿐 release-qualified capability가 아니다. Schema-v3 bundle은 실제 engine image의
digest/path/model SHA-256, immutable HTTPS source revision, code/weight/dataset license expression, notices section,
외부 approval reference를 `model-inventory.json`에 기록하고 같은 내용을 `license-approvals.json`의 approved
record와 일치시켜야 한다. CI bundle signature는 이 파일과 image/SBOM hashes를 exact payload에 묶는다.
이는 법무 판단을 자동화하지 않으며, 승인된 artifact가 없으면 builder가 출고를 거부한다.

### 1. PPE (helmet + **vest**) — 현재 vest 미지원 공백 해소
- **추천: `Hansung-Cho/yolov8-ppe-detection` (Hugging Face)**
  - 클래스(~10): Person, Hardhat, No-Hardhat, **Safety Vest, No-Safety Vest**, Mask, No-Mask 등
  - mAP@50 **0.744** (P 0.831 / R 0.685), repo MIT(가중치 AGPL*), 접근성 **ungated(검증됨)**
  - 현 `ppe_yolo`(Hardhat-only)를 대체해 **no_vest 이벤트 재활성** 가능

### 2. 화재 + 연기 — 현재 forest-fire 모델(일반 현장 오탐) 대체
- **추천: `Flare Guard` YOLO11n (`sayedgamal99/Real-Time-Smoke-Fire-Detection-YOLO11`, GitHub)**
  - 클래스: Fire, Smoke / 일반 장면(도로·CCTV) / mAP@50 **0.770**(Fire 0.828, Smoke 0.711)
  - **인증 없이 직접 다운로드 가능(검증됨)** — keremberke·TommyNgx 화재 모델은 HF 401(gated)로 재확인됨
  - ⚠️ LICENSE 파일 부재 + AGPL 상속 → **상용 적합성 검토 필요**

### 3. 쓰러짐(fall) — 즉시-적용 모델 없음 → **보류 권장**
- 바로 꽂을 fall 전용 공개 가중치(.pt/.onnx)는 발견되지 않음.
- 선택지: (a) Roboflow `fall-detection-ca3o8`(데이터 CC BY 4.0, mAP 88.6%) **자가 학습**, 또는
  (b) YOLO11-pose + keypoint 기하 판정 로직 구현 — 둘 다 별건 개발(비목표와 충돌하므로 후순위).
- 당분간 현 pose 휴리스틱(`pose_anomaly` temporal) + VLM 확인으로 운용.

## 교체(turnkey) 절차

```bash
pip install huggingface_hub ultralytics

# 1) PPE — Hansung-Cho (ungated)
python3 - <<'PY'
from huggingface_hub import hf_hub_download
from ultralytics import YOLO
p = hf_hub_download(repo_id="Hansung-Cho/yolov8-ppe-detection", filename="best.pt")
print("PPE class names(class_map 확정용):", YOLO(p).names)   # ← verbatim 확인 필수
PY
yolo export model=<위 best.pt 경로> format=onnx opset=18 simplify=True dynamic=False imgsz=640
# 산출 .onnx → models/yolo26_ppe.onnx 로 교체

# 2) 화재/연기 — Flare Guard YOLO11n (직접 다운로드)
curl -L -o models/flareguard_yolo11n.pt \
  https://raw.githubusercontent.com/sayedgamal99/Real-Time-Smoke-Fire-Detection-YOLO11/main/models/best_nano_111.pt
python3 -c "from ultralytics import YOLO; print('Fire/Smoke names:', YOLO('models/flareguard_yolo11n.pt').names)"
yolo export model=models/flareguard_yolo11n.pt format=onnx opset=18 simplify=True dynamic=False imgsz=640
```

그 다음 `config/model_profiles.yaml`에 등록(코드 직접수정 금지 — config+MultiModelRegistry 경로):

```yaml
  ppe_yolo:
    model_path: "./models/yolo26_ppe.onnx"
    source: custom_trained        # 공개 모델 교체 시 갱신
    classes: null                 # ONNX names 메타데이터 자동 인식
    class_map:                    # ⚠️ 위 model.names verbatim으로 키 확정 후 사용
      Person: person
      Hardhat: helmet
      No-Hardhat: no_helmet
      Safety Vest: vest
      No-Safety Vest: no_vest
      # Mask/No-Mask는 KSI 표준 외 → 미매핑(정책 미트리거)
  fire_smoke_yolo:
    model_path: "./models/flareguard_yolo11n.onnx"
    classes: null
    class_map:
      Fire: fire
      Smoke: smoke
```
그리고 `config/event_policies.yaml`에서 `no_vest`를 `enabled: true`로 재활성.

## 적용 후 검증 (필수)
1. `model.names`(.pt)와 ONNX 임베드 `names`가 일치하는지 교차 확인 → class_map 키 정확.
2. `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` → `failed_models: {}`.
3. **실 영상**으로 true/false positive 관찰(`docs/phase-cd-readiness.md` C5) — 공개 모델도 현장
   적합성은 실측해야 한다.

## 미해결/확인 필요
- [ ] **AGPL 상용 정책 결정**(가장 중요 — 배포 형태에 따른 의무).
- [ ] Hansung-Cho `model.names` verbatim(`No-Hardhat` vs `NO-Hardhat` 등) 확정.
- [ ] Flare Guard 클래스 인덱스 순서 확인.
- [ ] 실 현장 영상으로 정확도/오탐 실측.
