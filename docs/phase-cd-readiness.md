# Phase C/D 실행 준비 (Readiness Runbook)

> 장비(DGX Spark·실 RTSP 카메라)와 데이터(커스텀 학습 데이터셋)·Ollama 런타임이 준비되면
> **이 문서 그대로** Phase C(실환경 검증)·Phase D(커스텀 모델)를 실행한다.
>
> **현재 상태:** 코드/도구는 준비 완료(아래 "이미 준비된 것" 참조). 남은 것은 *실행*뿐이며,
> 각 단계에 **정량 합격 기준(acceptance)** 을 정의해 "통과/실패"를 객관적으로 판정한다.

## 범용 CCTV 기본 셋업 (커스텀 학습 없이 바로 사용)

> **Phase D(커스텀 학습)는 선택**이다. 기본 전략은 "공개 pretrained 모델 + 범용 프로파일 +
> 보수적 임계값"으로 어느 현장에나 바로 운영하는 것. 학습은 특정 클래스 정확도가 현장 요구에
> 미달할 때만 진행한다.

- **기본 도메인 = `general`** (`config/camera_profiles.yaml`의 cam_001). 사람·쓰러짐·화재·연기·혼잡을
  탐지하고, 건설 특화 PPE(안전모/조끼)는 제외해 일반 현장 오탐을 줄인다. 사용 모델은
  general_yolo(COCO) + fire_smoke_yolo. 현장별로 domain을 `construction/factory/parking/school`로
  바꾸면 해당 프로파일이 적용된다(전부 `config/event_policies.yaml`에 정의됨 — dead 도메인 없음).
- **결정된 기본 임계값**(다양한 CCTV 화각에서 false alarm 최소화 목적, 현장 데이터로 미세조정):
  사람/혼잡/쓰러짐 게이트 `0.35`, 화재 `0.45`(critical 오탐 억제), 연기 `0.40`(안개/수증기 혼동 억제).
  운영 로그는 INFO·debug off(`config/default.yaml`).
- **VLM(Ollama)은 선택**: 미가동이어도 1차 YOLO 탐지로 동작하며, 정책상 VLM 필요 이벤트는
  "VLM 미확인" 표식으로 노출된다(false-green 방지). 가동 시 fire/fall 등이 2차 확인으로 enrich.

## 이미 준비된 것 (코드/도구 — 추가 개발 불필요)

| 영역 | 자산 | 비고 |
|---|---|---|
| GPU 사용 | `YOLODetector`가 `device∈{cuda,auto}`+가용 시 `CUDAExecutionProvider` 자동 요청, CPU 폴백 | `config/default.yaml: device.primary` |
| 무인 실행 | `src/run.py` 헤드리스 데몬(파이프라인 auto-start + pipeline-aware `/health`) | systemd/Docker ExecStart |
| 운영 지표 | `pipeline.get_status()` → 카메라별 fps/dropped_frames/last_frame_time/is_stale/reconnect_attempts, vlm_queue, 모델 metrics | `/health`에도 반영 |
| 헬스/CUDA 점검 | `scripts/healthcheck.py --require-gpu --check-vlm --check-cameras --json` | CUDAExecutionProvider 확인 |
| 부하/지연 | `scripts/stress_test.py`(장시간·정책·VLM), `scripts/profile_pipeline.py`(latency+tracemalloc 메모리) | `--json-out` 리포트 |
| 학습/데이터 | `scripts/{download_datasets,convert_to_yolo,validate_dataset,generate_dataset_yaml,train_ppe,train_fire_smoke,export_to_onnx}.py` | `batch=-1` 거부 가드 포함 |

---

# Phase C — 실환경 검증

## C0. 사전 준비 체크리스트
- [ ] DGX Spark에 프로젝트 설치(`bash scripts/install.sh`) — GPU 환경이면 install.sh가 onnxruntime-gpu로 교체
- [ ] `.env`에 실 RTSP URL·대시보드 비밀번호 설정(`chmod 600 .env`)
- [ ] 기본 모델 배치(`models/yolo26.onnx` 등 — `docs/delivery-checklist.md` A4)
- [ ] (VLM 검증 시) `ollama serve` + `ollama pull qwen3-vl:latest`
- [ ] **현장 영상 샘플 확보** — 사람/안전모/화재가 실제로 담긴 영상(현 `data/sample.mp4`는 합성 도형이라 정확도 검증 불가)

## C1. DGX CUDA 검증
```bash
./.venv/bin/python scripts/healthcheck.py --require-gpu --json
./.venv/bin/python -c "import onnxruntime as ort; print(ort.get_available_providers())"
# 데몬/대시보드 기동 로그에서 'Providers: [...CUDAExecutionProvider...]' 확인
```
**합격:** `onnxruntime_providers`에 `CUDAExecutionProvider` 활성 + 모델 로드 시 detector 로그가 CUDA provider 사용 + GPU 추론 latency가 CPU 대비 유의하게 낮음.

## C2. 실 RTSP 1대 안정성 (completion-plan M1.2)
```bash
# .env에 CCTV_CAM_001_URL=rtsp://... 설정 후
KSI_CCTV_HEALTH_PORT=8081 ./.venv/bin/python src/run.py     # 무인 데몬 기동
# 별도 터미널에서 30분 이상 주기적으로:
curl -s http://localhost:8081/health | python3 -m json.tool   # cameras/모델/VLM 상태
```
점검 중 카메라 전원을 잠시 끊었다 복구해 **재연결**을 확인한다.
**합격(정량):**
- [ ] 30분 이상 크래시 없이 프레임 처리(`/health` status=healthy 유지)
- [ ] `cameras.<id>.fps` ≥ 목표치(예: 카메라 설정 fps의 70% 이상)
- [ ] `dropped_frames` 비율 < 5% (frame_count 대비)
- [ ] 카메라 차단 후 복구 시 `reconnect_attempts` 증가 후 `connected=true`·`is_stale=false`로 회복
- [ ] 메모리 안정(아래 C3 profile로 RSS 증가가 선형 누수가 아님)
- [ ] `/health`가 카메라 끊김을 **unhealthy로 정확히 반영**(false-green 아님)

## C3. 부하/메모리/지연 측정
```bash
./.venv/bin/python scripts/stress_test.py --video <실영상> --policy-mode --with-vlm \
    --duration 1800 --cameras 1 --json-out reports/phase_c_stress.json
./.venv/bin/python scripts/profile_pipeline.py --video <실영상> --frames 200 --iterations 10 \
    --json-out reports/phase_c_profile.json   # --video 생략 시 합성 프레임으로 측정
```
**합격(정량, 결정값):**
- [ ] GPU 추론 평균 latency **< 150ms/frame**(skip_frames=5·30fps → 처리 6fps의 166ms 예산 내). CPU-only면 best-effort.
- [ ] p95 latency < 250ms/frame
- [ ] 30분 stress 동안 RSS 증가가 선형 누수가 아님(시작 대비 **< 15%** 증가, 평탄화)
- [ ] dropped_frames < 5%(C2와 동일 기준)

## C4. VLM 2차 분석 종단
```bash
ollama serve &   # qwen3-vl 사전 pull 필요
KSI_CCTV_TEST_VLM=1 ./.venv/bin/python -m pytest tests/test_e2e_real.py::TestRealVlmJourney -v
```
**합격:** 실 VLM 호출 성공(`success_count≥1`, `raw_response` 비어있지 않음), risk_level 유효. 데몬 운영 시
정책 `vlm: always` 이벤트가 VLM 확인 후 enrich되어 알림됨(미실행 시 "VLM 미확인" 표식 확인).

## C5. 탐지 정확도(실영상)
실 사람/안전/화재 영상으로 true/false positive를 관찰한다(현 공개 pretrained 모델은 일반 현장에서
false positive 위험 — `docs/operations.md` 모델 한계 참조). 필요 시 `config/event_policies.yaml`의
`min_confidence`·`cooldown_sec`을 현장 데이터로 보정. **근본 해결은 Phase D 커스텀 모델.**

---

# Phase D — 커스텀 Safety 모델

## D0. 표준 클래스 SSOT (데이터 라벨링 → 학습 → 추론 → 정책이 모두 따른다)
커스텀 모델은 아래 **KSI 표준 클래스명 그대로** 라벨링/학습해야 한다. Ultralytics ONNX export는
클래스명을 `names` 메타데이터에 넣고, detector가 이를 1차 출처로 사용하므로(메타데이터-우선),
표준명으로 학습하면 별도 `class_map` 없이 곧바로 정책과 연결된다.

| 표준 클래스 | 활성화되는 이벤트(event_policies) | 비고 |
|---|---|---|
| `person` | person_detected, crowding, fall(보조), person_proximity | COCO에도 있음 |
| `helmet` | (no_helmet 판정 보조) | |
| `no_helmet` | **no_helmet** | 현재 공개 ppe 모델로 직접 탐지 중 |
| `vest` | (no_vest 판정 보조) | |
| `no_vest` | **no_vest** (현재 `enabled:false`) | 커스텀 모델 도입 후 재활성 |
| `fire` | **fire** (critical) | |
| `smoke` | **smoke** | |
| `fall` | **fall** | 현재 pose 휴리스틱 → 전용 모델 권장 |

- PPE 모델: `person, helmet, no_helmet, vest, no_vest` (5-class).
- Fire/Smoke 모델: `fire, smoke`.
- (선택) Fall: 전용 pose/fall 모델.

## D1. 데이터셋 준비
```bash
python scripts/download_datasets.py --list                 # 후보/라이선스 확인
python scripts/download_datasets.py --dataset ppe
python scripts/convert_to_yolo.py --input-dir <원본> --output-dir data/ppe/yolo --format <voc|coco|csv>
python scripts/validate_dataset.py --dataset-dir data/ppe/yolo --num-classes 5 --report
# dataset.yaml 생성 — 표준 클래스 순서를 정확히:
python scripts/generate_dataset_yaml.py --dataset-dir data/ppe/yolo \
    --classes person helmet no_helmet vest no_vest
```

## D2. 학습 (GPU 필요 · batch 고정 — `batch=-1` 금지: 통합메모리 OOM)
```bash
./.venv/bin/python scripts/train_ppe.py --data data/ppe/yolo/dataset.yaml --batch 8 --epochs 100
./.venv/bin/python scripts/train_fire_smoke.py --data data/fire_smoke/yolo/dataset.yaml --batch 8
```

## D3. ONNX Export (names 메타데이터 포함 · imgsz 640)
```bash
./.venv/bin/python scripts/export_to_onnx.py --weights runs/.../best.pt --imgsz 640 \
    --output models/yolo26_ppe.onnx
```

## D4. 모델 교체(등록) 절차
1. export한 `.onnx`를 `models/`에 배치.
2. `config/model_profiles.yaml`에서 해당 모델:
   - `model_path`를 새 파일로,
   - `source: pre_trained` → `source: custom_trained`,
   - `classes: null`(ONNX `names` 메타데이터 자동 인식 — 표준명으로 학습했다면 `class_map` 불필요).
3. `config/event_policies.yaml`에서 `no_vest`를 `enabled: true`로 재활성(vest/no_vest 학습 완료 시).
4. 재검증: `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` → `failed_models: {}` +
   `KSI_CCTV_TEST_VLM`/실영상으로 해당 이벤트 실제 발생 확인.

## D5. 합격 기준 (결정값 — 현장 요구로 조정 가능)
- [ ] 검증셋 mAP@0.5: **PPE ≥ 0.70**, **fire/smoke ≥ 0.60**, fall(전용 모델 시) ≥ 0.60
- [ ] 클래스별 **precision ≥ 0.80**(특히 fire — critical 알람의 오탐은 신뢰도를 크게 떨어뜨림), recall ≥ 0.70
- [ ] 공개 pretrained 베이스라인 대비 현장 영상에서 **오탐률이 개선**(학습의 명분 = 베이스라인 초과)
- [ ] 교체 후 `e2e_smoke_test --policy-mode` 통과, 실영상에서 해당 안전 이벤트가 정상 탐지·오탐 허용 범위

---

## 통합 검증 게이트(장비/데이터 없이도 항상 통과해야 함)
```bash
bash scripts/test.sh    # ruff(차단)+pytest(차단,캐시클린)+coverage≥50%+mypy(advisory)
```
Phase C/D 작업 중에도 이 게이트는 회귀 없이 통과를 유지한다.
