# E2E 스모크 테스트 결과

> **이력 기준선 (2026-05-27)**: 아래 "2026-05-27 기준 이력" 섹션은 Phase 4 이전 32 tests 기준 기록입니다.  
> **현재 기준선 (Phase 10 M1, 2026-06-14)**: 전체 단위/통합 테스트 통과 (`./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider`, 1건 VLM opt-in skip), policy-mode 기본 실행.

---

## 현재 기준선 (2026-06-14, Phase 10 M1)

### 실행한 검증

```bash
# 캐시 클린 단위 테스트
./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider -q

# E2E 스모크 테스트 (policy-mode)
./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode

# 헬스체크
./.venv/bin/python scripts/healthcheck.py --json
```

### 결과 요약

- 단위 테스트: **전체 통과, 1 skipped** (VLM opt-in 테스트 skip). 실제 수는 `./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider -q` 실행으로 확인한다.
- E2E 스모크 테스트 (policy-mode): `ok: true`, `failed_models: {}`
- 헬스체크: `ok: true`, 12 passes, 1 warning (CUDA unavailable — CPU fallback 정상)

### 활성 모델 (policy-mode)

| 모델 | 경로 | 상태 |
|---|---|---|
| `general_yolo` | `models/yolo26.onnx` | 로드 성공 |
| `ppe_yolo` | `models/yolo26_ppe.onnx` | 로드 성공 (Phase 9 M4 다운로드) |
| `fire_smoke_yolo` | `models/yolo26_fire_smoke.onnx` | 로드 성공 (Phase 9 M4 다운로드) |

- ONNX provider: `CPUExecutionProvider` (로컬 환경 기준, DGX Spark에서는 CUDAExecutionProvider)
- `general_yolo` 실제 추론: person 클래스(COCO) 감지. `ppe_yolo`는 Hardhat/NO-Hardhat(2-class) 전용으로 person 클래스 없음 — class_map 적용 후 helmet/no_helmet 이벤트로 변환됨 (config/model_profiles.yaml 참조)

### 실모델·실영상 종단 검증 (sample.mp4 기준)

- sample.mp4 → ONNX detect → policy → 콜백
- `fire`, `no_helmet` 이벤트 실제 발생 확인
- 이미지 메모리 해제 확인 (최근 50건 보존)

---

## 남은 수동 확인 항목

- [ ] 실제 사람 포함 영상 또는 RTSP 카메라에서 YOLO 탐지 박스 표시 확인
- [ ] `bash scripts/run_dashboard.sh` → 브라우저에서 대시보드 UI 수동 확인
- [ ] DGX Spark 환경에서 `CUDAExecutionProvider` 동작 확인
- [ ] 장시간 실행(30분 이상) FPS/dropped frame 확인

---

## 2026-05-27 기준 이력 (Phase 4 이전)

실행일: 2026-05-27

### 실행한 검증

```bash
./.venv/bin/python scripts/create_sample_video.py --output data/sample.mp4
python3 -m compileall -q src tests scripts
./.venv/bin/python -m unittest discover tests -v
./.venv/bin/python scripts/e2e_smoke_test.py
./.venv/bin/python scripts/e2e_smoke_test.py --with-vlm
```

### 결과 요약

- 유닛 테스트: 32 tests OK
- ONNX provider: `CPUExecutionProvider`
- ONNX input shape: `[1, 3, 640, 640]`
- ONNX output shape: `[1, 300, 6]`
- `ok: true`, detection count: 0 (합성 샘플 영상 — person 탐지 없음)
- VLM: `risk_level: low`, 추론 시간 약 52초

### 해석

합성 도형 기반 샘플 영상이므로 COCO `person` 탐지가 발생하지 않음.  
이 기록은 Phase 4 이전 단순 파이프라인(단일 YOLO + hardcoded rules) 기준입니다.  
현재 실행 경로는 `MultiModelRegistry + EventPolicyEngine` policy-mode입니다.
