# KSI-CCTV AI 아키텍처 재설계 — Advanced-AI v1 기준

> **상태:** Advanced-AI v1의 현재 설계 기준. 아래의 2026-07-18 대안 분석은 참고용이며,
> 이 섹션의 범위와 상태가 우선한다.

## 0. 권위 있는 Advanced-AI v1 마일스톤

| 구분 | v1 상태 |
|---|---|
| 대상 | DGX Spark ARM64 offline Appliance, HLS, single-admin |
| 구현됨 | COCO/PPE/fire-smoke 정책 탐지, ByteTrack `line_crossing`/`loitering` tracking |
| 개발 중 | pose + ST-GCN 기반 fall/fight, bounded local VLM verification |
| VLM 안전 계약 | provider/timeout/parser 실패는 후보 이벤트를 `unverified` alert로 보존한다. VLM 실패 또는 불일치는 silent suppression 근거가 될 수 없다. |
| qualification 필요 | ARM64 CUDA artifact, 모델 라이선스, 대표 현장 데이터 정확도, 실제 RTSP/HLS, 복구 및 목표 동시 채널 soak. 16채널은 미입증. |
| v1 제외 | WebRTC, cloud/native app, RBAC, production 기본 open-vocab, LLM reporting, 자동 few-shot/GNN, theft/weapons/smoking |

이후의 선택지 중 v1 제외 항목과 VLM 억제 권한은 승인된 구현 계획이 아니다.

---

## 1. 설계 원칙

1. **정량 보장 우선**: 안전 제품은 precision/recall 수치로 품질을 보증해야 한다. VLM 단독은 정량성 부족.
2. **실시간성 분리**: 모든 프레임 처리는 경량(~30ms), 정밀 판정은 이벤트 트리거 시만(~100ms-5s).
3. **학습 부담 통제**: v1은 자동 few-shot 학습을 수행하지 않으며, 신규 이벤트는 별도 데이터·모델 승인으로만 추가한다.
4. **판정 권한 분리**: 1차 후보, 2차 정밀, 3차 최종 각각 명확히 다른 AI가 담당, 단일 AI에 전권 부여 금지.
5. **실패 안전성**: VLM은 bounded local verification이며 실패·시간초과·파싱 오류로 후보 알림을 억제하지 않는다. 이 경우 `unverified` alert를 남긴다.
6. **온프레미스 기본**: CCTV PII 영상은 외부 SaaS 전송 금지(ADR-0001 준수). 로컬 Ollama/vLLM 우선.
7. **DGX 활용**: DGX Spark(A100/H100) 자원을 효율적으로 분할 사용. 1계층은 GPU 가속, 2-3계층은 별도 스트림.

---

## 2. 최신 AI 대안 분석 (2024-2026)

### 2.1 VLM 계열 진화

| 모델 | 출시 | 크기 | 비디오 | 한계 | KSI 적합성 |
|---|---|---|---|---|---|
| **Qwen2.5-VL** | 2025.01 | 3B/7B/32B/72B | O (T-ROPE 시계열) | 단일 프레임보다 낫지만 지연 큼 | 중 |
| **Qwen3-VL** | 2025.09-11 | 2B-235B MoE | O (Interleaved-MRoPE, 256K ctx, 시간-타임스탬프 정렬) | 235B는 클라우드 전용, 4B-32B는 로컬 가능 | **높음** |
| **LLaVA-Video** | 2024 | 7B-72B | O | 연구용, 배포 미성숙 | 중 |
| **InternVL3** | 2025 | 1B-78B | 부분 | 비디오 약함 | 낮음 |
| **MiniCPM-V 2.6** | 2024 | 8B | O (8프레임) | 엣지 친화, 정확도 중간 | **엣지용 높음** |

**핵심 변화**: Qwen3-VL은 비디오 시계열을 native 지원. 단일 프레임 VLM의 "행동 인식 불가" 한계를 근복적으로 해결. 특히 4B-32B Instruct/Thinking 에디션은 DGX에서 로컬 서빙 가능.

### 2.2 Pose/Skeleton 계열

| 모델 | 접근 | 정확도 | 비용 | KSI 적합성 |
|---|---|---|---|---|
| **YOLOv8-Pose / YOLOv11-Pose** | person + 17 keypoints | mAP@keypoint 0.70+ | 20-50ms (ONNX) | **최적** — 기존 YOLO 인프라 재사용 |
| **RTMPose** | top-down 2단계 | 높음 | 30-80ms | 중 (별도 파이프라인) |
| **ViTPose** | transformer 기반 | 매우 높음 | 100-200ms | 중 (DGX 필요) |
| **DWPose** | whole-body | 높음 | 50ms | 높음 (손/상체 동작 인식 필요 시) |

**선택**: YOLOv11-Pose ONNX export — 기존 `YOLODetector`와 동일 파이프라인 재사용.

### 2.3 Skeleton-based Action Recognition

| 모델 | 입력 | 클래스 | 비용 | KSI 적합성 |
|---|---|---|---|---|
| **ST-GCN** | keypoint 시계열 (T=30) | fall/fight/walk/run | 20-50ms | **높음** — 쓰러짐/싸움 동시 해결 |
| **PoseC3D** | heatmap 시계열 | 다중 액션 | 100-200ms | 중 |
| **ACnet** | keypoint + attention | 폭력 특화 | 50ms | 높음 (싸움 정밀) |

**선택**: ST-GCN (PaddleDetection 제공, ONNX 변환 가능) — YOLOv11-Pose keypoint를 입력으로 받아 시계열 행동 분류.

### 2.4 RGB Video Action Recognition

| 모델 | 입력 | 비용 | KSI 적합성 |
|---|---|---|---|
| **MoViNet** | RGB 클립 | 20-50ms (모바일 최적화) | 중-높음 |
| **SlowFast** | 클립 (slow+fast) | 100-300ms | 높음 (DGX) |
| **VideoMAE V2** | 클립 | 200-500ms | 중 (정밀 but 무거움) |
| **X3D** | 클립 | 20ms | **높음** — 실시간 |

**선택**: 필요 시 MoViNet-X3D (경량·실시간). 다만 Pose+ST-GCN이 CCTV 안전 액션(fall/fight)에 더 적합해 1차 도입은 Pose 경로 권장.

### 2.5 Video-LLM (시계열 VLM)

Qwen3-VL 4B/8B Instruct 에디션이 비디오 입력을 native 지원하며, 30초 클립 단위로 행동을 이해. CCTV 카메라 1대당 1초 클립을 주기적으로 VLM에 전달하면 행동 기반 최종 판정 가능.

**비용 현실 성**: 단일 프레임 VLM(현재) — 2-5s. 1초 비디오 클립 VLM(Qwen3-VL 4B) — 5-15s. 그러나 2계층 Pose+ST-GCN이 1차 필터를 맡으므로 VLM 호출 빈도를 1/100 이하로 줄일 수 있음.

### 2.6 GNN/Tracking 계열

| 방안 | 역할 | 현재 격차 | 도입 난이도 |
|---|---|---|---|
| **ByteTrack / BoT-SORT** | 객체 ID 추적 | ByteTrack `line_crossing`/`loitering` 구현됨 | 중 |
| **Trajectory GNN** | 궤적 이상 행동(배회/절도) | v1 제외 | 높음 (데이터 필요) |
| **ST-GCN + Tracking** | person ID 유지 + 시계열 행동 | pose/ST-GCN 개발 중 | 중 |

**현재 기준**: ByteTrack은 `line_crossing`/`loitering`에 구현됐다. trajectory 기반 theft와 GNN은 v1 범위 밖이다.

### 2.7 LLM-as-a-Judge (향상된 VLM 판정)

- **개념**: 다수 VLM/분류기 결과를 LLM이 최종 집계/판정
- **적용**: 2계층 3프레임 VLM 투표 + LLM 합의 판정
- **이점**: VLM 단일 호출 대비 정확도 상승, 일관성 부족 해소
- **비용**: LLM 호출 추가 (1-3s)
- **모델**: Qwen3 4B (텍스트 전용) 또는 Qwen3-VL Thinking 에디션

---

## 3. 재설계: 4계층 하이브리드 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 카메라 N대 (RTSP) — 병렬 프레임 스트림                                    │
└──────────────────────────┬──────────────────────────────────────────────┘
                           ↓ skip_frames 적용
┌──────────────────────────────────────────────────────────────────────────┐
│ 1계층: 실시간 객체 탐지 (모든 처리 프레임, ~10-30ms)                       │
│                                                                          │
│  [유지]  general_yolo     — COCO person/car/객체                         │
│  [유지]  ppe_yolo         — helmet/vest/PPE                              │
│  [유지]  fire_smoke_yolo — fire/smoke                                   │
│  [新增]  pose_yolo        — YOLOv11-Pose (person + 17 keypoints)          │
│  [激活]  open_vocab_yolo  — YOLO-World (텍스트 프롬프트 즉시 탐지)         │
│  [新增]  카메라별 default_models 적용 (현재 dead 기능 활성)               │
│                                                                          │
│  출력: 객체 bbox + keypoint + 클래스 + 신뢰도                             │
└─────────────────────┬──────────────────┬─────────────────────────────────┘
                      ↓ 객체 트리거    ↓ keypoint 스트림
┌─────────────────────┴───────┐  ┌─────┴──────────────────────────────┐
│ 2A계층: 정책 규칙 엔진 (현재)  │  │ 2B계층: Skeleton Action (新增)     │
│  - direct_detection          │  │  ST-GCN ONNX                       │
│  - count_threshold           │  │  입력: keypoint 시계열(T=30 frames) │
│  - zone_intrusion            │  │  출력: fall/fight/walk/run/standing │
│  - proximity                 │  │  비용: 20-50ms                     │
│  - person_proximity          │  │  ── pose_anomaly 룰 교체            │
│  - safety_gear_overlap       │  │  ── person_proximity 룰 교체        │
│  [폐지] pose_anomaly(휴리스틱)│  └────────────────────────────────────┘
│  [폐지] person_proximity 단독 │
└─────────────┬────────────────┘
              ↓ 정책 트리거
┌─────────────┴─────────────────────────────────────────────────────────┐
│ 3계층: VLM 정밀 판정 (트리거 시만, 5-30s/event)                         │
│                                                                        │
│  [v1 개발] bounded local VLM verification                                │
│    - VLM 성공 결과는 alert에 verification metadata를 더할 수 있음         │
│    - provider/timeout/parser 실패 → `unverified` alert 보존               │
│    - 실패·불일치로 후보 alert를 silent suppression하지 않음               │
│                                                                        │
│  [新增] 3-프레임 투표 모드 (fight/ambiguous 시)                          │
│    - 1초 간 3프레임 캡처 → VLM 3회 호출 → 다수결                         │
│    - 단일 판정 대비 정확도 상승, 오탐 억제                               │
│                                                                        │
│  [新增] 비디오 클립 VLM (Qwen3-VL 4B/8B)                                │
│    - fight/fall 미확정 시 1초 비디오 클립 전달                           │
│    - Qwen3-VL 비디오 시계열 이해 → 행동 판정                            │
│    - 단일 프레임 한계 근본 해소                                         │
│                                                                        │
│  [新增] Few-Shot 게이트                                                 │
│    - 0.80 ↑ 신뢰 → 통과 (4계층으로)                                     │
│    - 0.50-0.80 → 3-프레임 투표 / 비디오 VLM 승격                       │
│    - 0.50 ↓ → 알림 발송 안 함 (1·2계층 오탐)                             │
└─────────────────────┬────────────────────────────────────────────────┘
                      ↓ 최종 이벤트
┌─────────────────────┴─────────────────────────────────────────────────┐
│ 4계층: LLM 보고 자동화 + 학습 파이프라인 (新增)                         │
│                                                                        │
│  A. LLM 통보 메시지 작성                                                │
│     - VLM 출력(description/risk/action) → LLM(Qwen3 4B)로 자연어 통보    │
│     - 카메라별/배치별/시간대별 요약 자동 생성                           │
│                                                                        │
│  B. Few-Shot 라벨링 워크플로우 (scripts/ 이미 존재,激活)                 │
│     - 1·2·3계층이 미확정한 이벤트는 라벨링 큐 적재                       │
│     - 운영자가 50장 라벨링 → 기존 YOLO fine-tune (~30분 GPU)            │
│     - 다음 배포부터 1계층에서 정량 탐지                                  │
│                                                                        │
│  C. 메타 정책 평가                                                      │
│     - 최근 N시간 알람 빈도/오탐 패턴 분석                                │
│     - abnormal alarm burst → 임계값 자동 조정 제안                       │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 이벤트별 AI 매핑 (재설계 후)

| 이벤트 | 1계층 (YOLO) | 2A 규칙 | 2B Skeleton Action | 3계층 (VLM) | 4계층 | 구현 순서 |
|---|---|---|---|---|---|---|
| **화재/연기** | fire_smoke_yolo | direct_detection | — | VLM 확인 (flame 형태) | LLM 통보 | P0 (현 유지) |
| **PPE 미착용** | ppe_yolo | direct_detection | — | VLM on_ambiguous | LLM 통보 | P0 (현 유지) |
| **쓰러짐** | pose_yolo(person+kp) | — | **ST-GCN fall** | VLM 부정 시 억제 | LLM 통보 | **P1 新增** |
| **싸움/폭력** | pose_yolo(person+kp) | — | **ST-GCN fight** | 3-프레임 투표 + 비디오 VLM | LLM 통보 | **P1 新增** |
| **침입/영역** | general_yolo | zone_intrusion | — | VLM 선택 | LLM 통보 | P1 (영역 UI新增) |
| **혼잡** | general_yolo | count_threshold | — | — | — | P0 (임계 보정) |
| **특이 객체**(무기/소화기) | open_vocab_yolo | direct_detection | — | VLM 확인 | LLM 통보 | **P1 激活** |
| **흡연/전화** | open_vocab_yolo + pose | — | ST-GCN action | VLM 행동 확인 | — | P2 (新增) |
| **절도/파괴** | general_yolo + ByteTrack | trajectory 규칙 | ST-GCN + 트랙 | 비디오 VLM | LLM 통보 | P2 (新增) |
| **배회/이상 이동** | general_yolo + ByteTrack | trajectory | — | 비디오 VLM | LLM 통보 | P3 (新增) |
| **유출/오염** | open_vocab_yolo 영역 | — | — | VLM | — | P3 (新增) |
| **무기 소지** | open_vocab + 전용 YOLO | direct_detection | — | VLM 확인 | LLM 긴급 통보 | P2 (新增) |

---

## 5. CCTV 맞춤 핵심 설계 결정

### 5.1 카메라별 모델 선택 (현재 dead 기능 활성)

`config/camera_profiles.yaml`의 `default_models`를 pipeline에 연결. 현재는 `detect_all()`이 모든 모델을 모든 카메라에 强制実行.

```yaml
# 의도: 주차장 카메라는 PPE 필요 없음
cam_parking_001:
  domain: parking
  default_models: [general_yolo, fire_smoke_yolo]  # ppe_yolo 제외
```

pipeline.py에서 `detect_all(image)` → `detect_selected(image, profile.default_models)`로 변경.

### 5.2 VLM 판정 억제 권한 (v1 제외된 역사적 제안)

`event_policies.yaml`에 `vlm_suppress` 정책 추가:

```yaml
fall:
  vlm: always
  vlm_suppress: true          # 新增: VLM이 risk_level: none → 알림 발송 안 함
  vlm_suppress_level: none    # 新增: 억제 판정 기준 (none/low)
  alert_level: high
```

Advanced-AI v1에는 적용하지 않는다. 특히 provider, timeout, parser failure의 alert callback 생략은 금지되며 `unverified` alert가 필요하다.

### 5.3 3-프레임 투표 모드 (fight/ambiguous)

`vlm_voting` 정책 추가:

```yaml
fight:
  vlm: always
  vlm_voting:
    frames: 3                  # 1초 간 3프레임
    quorum: 2                  # 2/3 다수결
    suppress_on_disagree: true # 新增: 2/3 미동의 시 알림 억제
```

### 5.4 Pose → ST-GCN 파이프라인 (新增)

```python
# pipeline.py 새 경로
 detections = model_registry.detect_selected(image, ["pose_yolo"])
 keypoints = detections[0].keypoints  # YOLOv11-Pose 출력
 pose_buffer[cam_id].append(keypoints)  # T=30 frame 버퍼
 if len(pose_buffer[cam_id]) >= 30:
     action = st_gcn.predict(pose_buffer[cam_id])
     if action in ("fall", "fight"):
         emit event
```

### 5.5 Few-Shot 학습 게이트 (新增)

```python
# 2-3계층 미확정 이벤트는 라벨링 큐 적재
if vlm_confidence < 0.50:
    save_to_labeling_queue(event)  # data/labeling_queue/<event_type>/
```

운영자가 50장 라벨링 → `scripts/train_ppe.py` 또는 커스텀 스크립트로 fine-tune → 다음 배포부터 1계층 정량 탐지.

### 5.6 LLM 통보 자동화 (新增)

```python
# VLM 출력을 LLM에 전달해 자연어 통보 작성
vlm_output = {"description": "사람 2명 접근, 손 동작 보임", "risk": "medium"}
llm_prompt = f"다음 CCTV 분석을 관리자 통보용 2문장으로 요약: {vlm_output}"
alert_message = llm.generate(llm_prompt)
# "Cam_001 14:32 — 2명 접근 후 손 동작 감지. 주의 요망."
```

### 5.7 비디오 클립 VLM (新增, DGX 의존)

Qwen3-VL 4B/8B Instruct를 vLLM/Ollama로 서빙하고, fight/fall 미확정 시 1초 비디오 클립 전달:

```yaml
vlm_video:
  enabled: false               # DGX 보유 시 활성
  provider: ollama_vllm
  model_name: "qwen3-vl-4b-instruct"
  clip_duration_sec: 1.0
  fps: 4
  max_calls_per_minute: 10     # 비용 상한
```

---

## 6. 마이그레이션 경로 (현 구조 → 신 구조)

### Phase R1: VLM 판정 억제 권한 강화 (v1 제외된 과거 제안)
- `event_policies.yaml`에 `vlm_suppress` 추가
- `pipeline.py`에서 `vlm_risk_level=none` 시 알림 생략
- 테스트: VLM 오탐 제거율 측정

### Phase R2: Open-Vocab + 카메라별 모델 선택 (open-vocab 기본 활성화는 v1 제외)
- `open_vocab_yolo` enabled: true로 전환
- `model_registry.detect_selected()` 추가, pipeline 연결
- 도메인별 `default_models` 정의

### Phase R3: Pose + ST-GCN 도입 (2-3주, 학습 0-50장)
- YOLOv11-Pose ONNX export 스크립트 (`scripts/export_to_onnx.py` 확장)
- `YOLODetector`에 keypoint 출력 지원
- ST-GCN ONNX 도입 (PaddleDetection pretrained)
- `pose_anomaly` / `person_proximity` 정책을 ST-GCN 경로로 교체
- 테스트: 기존 bbox 휴리스틱 대비 fall/fight 정확도 측정

### Phase R4: 3-프레임 투표 + LLM 통보 (LLM 통보는 v1 제외)
- `vlm_voting` 정책 추가
- 3-프레임 버퍼/캡처 로직
- LLM(Qwen3 4B) 서빙 추가
- 통보 메시지 자동화
- 테스트: 투표 정확도/일관성/LLM 품질 평가

### Phase R5: 비디오 클립 VLM + Few-Shot 라벨링 (자동 few-shot은 v1 제외)
- Qwen3-VL 4B vLLM/Ollama 서빙
- 비디오 클립 캡처/전달 파이프라인
- 라벨링 큐 + 운영자 UI
- Few-Shot fine-tune 워크플로우 활성화
- 테스트: 비디오 VLM 행동 정확도, 라벨링 루프 평가

### Phase R6: Tracking + Trajectory (장기, 1-2분기)
- ByteTrack 범위를 `line_crossing`/`loitering` 밖으로 확장
- trajectory 규칙 엔진
- 절도/배회/이상 이동 이벤트 추가
- 테스트: 실카메라 장기 검증

### Phase R7: GNN Trajectory + GNN 행동 분석 (장기 로드맵)
- trajectory GNN 학습
- 비지도 이상 행동 탐지
- 도메인 적응 fine-tune
- 현장 데이터 확보 후 진행

---

## 7. 기술 스택 추가/변경

### 新增 라이브러리 (requirements.txt)
```
# Phase R3
onnxruntime>=1.20          # 이미 있음
# ultralytics>=8.3.111     # 이미 있음 — YOLOv11-Pose 지원

# Phase R4 — LLM
# transformers>=4.45       # Qwen3-VL HuggingFace serve/quant
# vllm>=0.11               # 권장 비디오 VLM 서빙 (Qwen3-VL 4B+)
# 또는 Ollama 0.5+ — 기존 통합 재사용

# Phase R5 — 비디오 VLM
qwen-vl-utils==0.0.14       # 비디오 처리 유틸
# torchcodec (선택)          # 비디오 디코딩 가속

# Phase R6 — Tracking
# bytetrack (pip install 혹은 로컬)

# Phase R7 — GNN (장기)
# torch-geometric
```

### 모델 자산 추가 (models/)
```
models/
  yolo11n-pose.onnx          # Phase R3
  st_gcn_fall_fight.onnx     # Phase R3 — PaddleDetection pretrained
  qwen3-vl-4b/               # Phase R5 (DGX 로컬, vLLM 서빙)
```

---

## 8. 성능/지연 목표

| 계층 | 목표 지연 | 호출 빈도 | DGX 자원 |
|---|---|---|---|
| 1계층 YOLO (3+pose+openvocab) | < 50ms/frame | 처리 프레임마다 | GPU 공유 |
| 2A 규칙 엔진 | < 1ms/event | 1계층 트리거 시 | CPU |
| 2B ST-GCN | < 50ms/event | 30프레임 버퍼 충족 시 | GPU |
| 3계층 VLM 단일 | 2-5s/event | 2계층 미확정 시 | GPU 전용 스트림 |
| 3계층 VLM 3-프레임 투표 | 6-15s/event | fight/ambiguous | GPU |
| 3계층 비디오 VLM | 5-15s/event | 2계층 미확정 P1+ | GPU |
| 4계층 LLM 통보 | 1-3s/event | 알림 발송 시 | GPU |

카메라 N대 × FPS 처리 시 1+2A 계층은 30fps 유지 가능(목표). 2B는 1fps 수준(30프레임 버퍼). 3-4계층은事件 빈도 따라 스케줄.

---

## 9. Riskabiltiy 평가

| 위험 | 영향 | 완화 |
|---|---|---|
| Pose 모델이 안개/가림에 약함 | 쓰러짐 오탐 | VLM 결과와 실패 상태를 보존하고 대표 현장 데이터로 qualification |
| ST-GCN 학습셋 부족 | fight 부정확 | Few-Shot 라벨링 워크플로우 활용 |
| 비디오 VLM GPU 자원 고갈 | 지연 폭증 | vLLM 동시 세션 상한 + 큐 |
| Qwen3-VL 4B 로컬 미지원 | 비디오 판정 못 함 | 2B ST-GCN + 단일 프레임 VLM 폴백 |
| Few-Shot 라벨링 운영 부담 | 학습 루프 지연 | 운영자 UI 자동 제안 + 50장 최소 |
| 다중 모델 동시 로드 메모리 | OOM | 카메라별 모델 선택 + GPU 분할 |
| 모델 라이선스 (AGPL/상용) | 배포 제약 | 사전학습 모델 모두 라이선스 검토 — MIT/Apache 우선 |

---

## 10. 구현 우선순위 요약

```
P0: pose + ST-GCN fall/fight와 bounded local VLM verification의 측정 가능한 개발/qualification
P1: ARM64 CUDA artifact, 현장 정확도 데이터, 실제 RTSP/HLS, recovery와 채널 soak evidence
후순위/v1 제외: open-vocab 기본 활성화, LLM reporting, 자동 few-shot/GNN, theft/weapons/smoking
```

---

## 11. 결론

**"모든 상황을 학습할 수 없다"** 는 전제를 기반으로 한 최신 AI 하이브리드 구조:

1. **1계층 YOLO** — 객체는 정량 탐지(유지 + pose/keypoint 新增 + open-vocab 激活)
2. **2계층 ST-GCN** — 행동은 keypoint 시계열로 정량 분류(쓰러짐/싸움 격차 해소)
3. **3계층 VLM** — bounded local verification; 실패는 `unverified` alert로 보존
4. **4계층 LLM** — v1 제외

**핵심 차이(vs. 현 구조)**:

| 항목 | 현 구조 | 신 구조 |
|---|---|---|
| 쓰러짐 판정 | bbox 휴리스틱 | ST-GCN keypoint 시계열 |
| 싸움 판정 | person 근접 | ST-GCN + 3-프레임 VLM 투표 |
| VLM 역할 | enrichment only | bounded local verification; failure is `unverified`, not suppression |
| 시계열 행동 | ❌ | O (ST-GCN + 비디오 VLM) |
| 새 이벤트 추가 | 전용 모델 학습 필요 | Open-Vocab 텍스트 프롬프트 |
| 정량 SLA | 어려움 | Few-Shot으로 보장 가능 |
| 운영자 통보 | VLM description | LLM 자연어 통보 |
| 카메라 맞춤 | ❌ 모든 카메라 동일 모델 | 도메인별 모델 세트 |
| 학습 루프 | 운영자 수동 | 4계층 라벨링 큐 자동 제안 |

이 구조로 CCTV 안전 관리 시스템을 "객체 → 행동 → 상황 → 통보"의 완전한 판정 체인을 갖추고, 새 이벤트를 학습 비용 0-50장으로 추가할 수 있는 정량·실시간·포괄적 시스템으로 발전시킬 수 있습니다.
