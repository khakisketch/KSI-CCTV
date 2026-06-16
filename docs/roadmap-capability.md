> **상태: 로드맵 초안(설계 단계 — 구현 전)** · 작성 2026-06-16 · 근거: multi-agent 리서치(실 코드 read + 웹 검증)
> 관련 문서: [패키징 로드맵](roadmap-packaging.md) · [타깃 아키텍처](architecture-target.md)
> ⚠️ 이 문서는 방향·후보·단계를 정리한 것으로, 표기된 FPS·라이선스·EP 가용성 등은 구현 착수 시 "사실 검증 플래그"의 [검증필요] 항목을 실측·확정해야 한다.

# 능력 로드맵 — open-vocabulary 탐지 + VLM 상황 이해

> 목표: **학습 없이 텍스트로 아무 객체·상황을 즉시 탐지**(Superb AI류 open-vocab) + 이미 보유한 **Qwen3-VL 상황 질의**를 결합해, 현 `MultiModelRegistry → EventPolicyEngine → VLM Dispatcher` 구조에 **최소 변경**으로 끼운다.
> 원칙: 직접 빌드보다 **기존 프레임워크/공개 모델 적극 활용**, 학습 파이프라인은 NON-GOAL.

## 0. 현 코드 기준선 (Read로 확인)

| 컴포넌트 | 파일 | open-vocab 도입 관점 핵심 |
|---|---|---|
| 탐지기 | `src/detection/yolo_detector.py` | `YOLODetector`는 **closed-set ONNX**(이미지 1-input). `classes` dict가 ONNX `names` 메타데이터로 고정됨. open-vocab은 **다른 detector 클래스** 필요(2-input). |
| 레지스트리 | `src/detection/model_registry.py` | `SUPPORTED_TYPES = {"yolo_onnx"}`. `_create_yolo_detector`만 분기 → 여기에 **`open_vocab_onnx` 타입 추가**가 진입점. `detect_all()`이 `{model_name: detections}` 반환 계약은 그대로 재사용. |
| 클래스 어댑터 | `src/detection/class_adapter.py` | 공개 모델 클래스명→KSI 표준명 변환. open-vocab은 **프롬프트 클래스명 자체가 표준명**이므로 class_map=None(identity)로 우회. |
| 정책 엔진 | `src/policy/event_policy_engine.py` | `trigger.{models,classes,min_confidence}` + `rule.type` 평가. **클래스명 문자열 기반**이라 임의 프롬프트 클래스명을 그대로 넣으면 무수정 동작. |
| 룰 | `src/policy/rule_evaluators.py` | `direct_detection / count_threshold / zone_intrusion / proximity / pose_anomaly` 등. 임의 객체엔 대부분 `direct_detection`·`count_threshold`·`zone_intrusion`가 바로 쓰임. |
| VLM | `src/vlm/*` | `OllamaProvider`(qwen3-vl), `VLMProvider` 추상화, `PromptSelector`(도메인별 yaml). **provider 추상화가 이미 있어** grounding 호출 추가가 깔끔. |
| 설정 | `config/model_profiles.yaml`, `config/event_policies.yaml` | 모델/이벤트가 모두 YAML SSOT. open-vocab 모델·정책도 **YAML 엔트리 추가**로 끝남. |

핵심: **EventPolicyEngine·룰·VLM dispatch는 거의 무수정**. 변경 집중점은 `(1) OpenVocabDetector 신규` + `(2) MultiModelRegistry 타입 분기` + `(3) 프롬프트→YAML 자동 생성 UX` 세 곳.

---

## 1. open-vocab / zero-shot 탐지 후보 비교

> FPS/AP는 공개 벤치(T4/A100/Orin 기준) 인용 — DGX Spark 실측은 §검증필요. 라이선스는 **설치형 상용 납품** 관점에서 가장 중요.

| 모델 | 클래스 변경 방식 | ONNX export | 실시간성(공개 기준) | 정확도 특성 | 라이선스 | 설치형 적합도 |
|---|---|---|---|---|---|---|
| **YOLO-World** (Tencent, CVPR'24) | **런타임 임베딩 교체(.npz)** — 재export 불필요(개수만 고정) | ◎ 이미지+텍스트임베딩 2-input ONNX, 검증된 구현 존재 | ◎ YOLO급(closed보단 느림) | 흔한 객체 양호, 소형/추상 약함 | **GPLv3**(가중치 Tencent, 상용 별도문의) | **1순위(동적)** |
| **YOLOE** (THU-MIG, ICCV'25) | export 시 프롬프트 **folded → 고정** (변경=재export) | ◎ YOLO11과 동일 포맷, 외부 인코더 불필요 | ◎ YOLO-Worldv2 대비 **1.4x 빠름, +3.5 AP** | open-vocab 중 최고 수준 | **AGPL-3.0**(상용=Enterprise) | **2순위(고정 vocab 트랙)** |
| **Grounding DINO 1.5** | 텍스트 프롬프트(transformer) | △ export 가능하나 무겁고 복잡 | △ A100 TRT 75FPS / Orin 10+FPS — YOLO보다 무거움 | zero-shot 정확도 강함 | 변형별 상이(NVIDIA TAO 상용본 존재) | 정밀 트랙 후보(1차 부적합) |
| **OWLv2** (Google) | 텍스트 프롬프트 | △ | △ transformer 계열, 느림 | open-vocab 정확도 양호 | **Apache-2.0(추정·검증필요)** | 라이선스 깨끗 → **상용 대안 후보** |
| **Florence-2** (MS) | 태스크 프롬프트(VLM형) | △ encoder-decoder | △ 탐지 전용 아님 | 멀티태스크(캡션·grounding) | MIT | VLM 보조 후보 |
| **Qwen3-VL** (보유) | 자연어 질의 grounding | n/a(Ollama) | ✕ 실시간 1차 부적합(초 단위) | **임의 객체·상황 최고 유연** | Qwen 라이선스 | **2차 grounding 폴백** |

### 1순위 추천: **YOLO-World(동적) + Qwen3-VL(폴백) 하이브리드**

- **이유:** "텍스트로 **즉시** 탐지"는 **런타임 클래스 변경**이 전제. 후보 중 ONNX에서 이를 만족하는 건 YOLO-World 뿐(이미지+사전계산 텍스트임베딩 2-input, 재export 불필요 — [검증완료]). YOLOE는 정확도·속도가 더 좋지만 **export 시 클래스 고정**이라 "사용자가 방금 입력한 단어 즉시 적용"이 안 됨.
- **YOLOE의 자리:** 현장 초기 셋업에서 **자주 쓰는 프롬프트 셋을 굽어둔 고정-vocab 모델**(빠르고 정확). "즉시성"이 아니라 "정확·고속"이 필요한 표준 이벤트(person/helmet/vehicle 등)에 배치하는 **2트랙**.
- **라이선스 리스크:** YOLO-World=GPLv3, YOLOE=AGPL. **설치형 상용 납품엔 둘 다 검토 대상**(현 `model_profiles.yaml`의 ppe/fire 모델도 이미 AGPL 경고 부착). 깨끗한 대안이 필요하면 **OWLv2(Apache-2.0 추정)** 트랙을 병행 검토 → §open_questions.

---

## 2. 2단계 파이프라인 설계 (1차 open-vocab + 2차 VLM)

```text
RTSP 프레임
  └─ MultiModelRegistry.detect_all()
       ├─ general_yolo (closed-set, 고속 baseline)        ← 기존 유지
       ├─ open_vocab_yolo (YOLO-World, 사용자 프롬프트 클래스)  ← 신규 타입
       └─ (옵션) yoloe_fixed (자주 쓰는 프롬프트 굽기)        ← 2트랙
  └─ EventPolicyEngine.evaluate()                          ← 거의 무수정
       └─ trigger.classes = [사용자 프롬프트 클래스명]
       └─ rule = direct/count/zone/proximity 템플릿
       └─ vlm = on_ambiguous (신규 프롬프트 기본)            ← 오탐 방지 핵심
  └─ VLM Dispatcher (qwen3-vl)
       ├─ A. 상황 질의(기존): "이 장면 위험한가?" 설명/risk_level
       └─ B. grounding 폴백(신규): open-vocab이 못 잡는 희귀/추상 객체를
             "X가 보이면 bbox JSON" 으로 저빈도 위임
```

### 2-1. 최소 변경 매핑

| 변경점 | 위치 | 작업 |
|---|---|---|
| OpenVocabDetector 신규 | `src/detection/open_vocab_detector.py`(신규) | `YOLODetector`와 동일한 `detect(image)->list[Detection]` 계약. 내부에서 이미지+클래스임베딩 2-input ONNX 실행. `set_classes(list[str])`로 임베딩 교체 API 노출. |
| 레지스트리 타입 분기 | `model_registry.py` `SUPPORTED_TYPES`, `_load_enabled_models` | `"open_vocab_onnx"` 추가 + `_create_open_vocab_detector()` 분기. detect 경로(`detect_all`)는 그대로(동일 계약). |
| 클래스 어댑터 우회 | `class_adapter.py` | open-vocab 프로파일은 `class_map=None` → identity 통과(프롬프트명=표준명). |
| CLIP 텍스트 인코더 | `src/detection/text_encoder.py`(신규) | 클래스 등록 시 1회 텍스트→임베딩(.npz 캐시). 런타임 추론 경로엔 미개입. |
| VLM grounding 메서드 | `src/vlm/vlm_inference.py` `VLMProvider` | `ground(image, target:str)->list[bbox]` 추가(추상화에 한 줄). `OllamaProvider`에 grounding 프롬프트 + bbox JSON 파서. |
| 좌표계 정합 | VLM grounding bbox | Qwen3-VL은 **relative coords** 출력 → 원본 해상도로 환산 후 Detection으로 래핑(letterbox 무관, 원본 직접). |

### 2-2. 역할 분담(언제 무엇을)

- **closed-set general_yolo:** person/vehicle 등 상시 고빈도 — 가장 빠름, 항상 ON.
- **open_vocab YOLO-World:** 사용자가 텍스트로 등록한 임의 클래스 — 실시간 1차.
- **qwen3-vl 상황 질의(A):** 정책상 `vlm: always/on_ambiguous` 이벤트 — 설명·risk 보강(기존 그대로).
- **qwen3-vl grounding(B):** open-vocab도 못 잡는 희귀·추상("쓰러진 지게차", "넘친 물") — **프레임 전수 아닌 저빈도/이벤트 트리거 시에만**(초 단위라 실시간 1차 불가).

---

## 3. 프롬프트 → 탐지/정책/알람 자동 배선 UX (학습 없이 즉시)

현 `event_policies.yaml` 스키마가 **클래스명 문자열 기반**이라, 프롬프트를 받아 YAML 엔트리를 생성하면 엔진 무수정으로 동작한다.

**입력 폼(대시보드 "탐지 추가"):**
1. **무엇을 탐지?** → 자유 텍스트(예: `소화기`, `안전고깔`, `넘어진 사람`) → open-vocab 클래스명.
2. **어떻게 판단?** → 룰 템플릿 선택(드롭다운):
   - `direct_detection`(있으면 알림) · `count_threshold`(N개 이상) · `zone_intrusion`(구역 침입) · `proximity`(근접).
3. **얼마나 위험?** → `alert_level`(low/high/critical) + `cooldown_sec`.
4. **2차 확인?** → `vlm` 모드(**신규 프롬프트는 기본 `on_ambiguous`** — 보정 전 임의 클래스의 오탐을 VLM이 거름).

**자동 생성되는 엔트리(예):**
```yaml
profiles:
  general:
    policies:
      extinguisher_present:        # 프롬프트에서 slug 생성
        enabled: true
        trigger:
          models: [open_vocab_yolo]
          classes: ["소화기"]       # = OpenVocabDetector.set_classes 입력
          min_confidence: 0.35
        rule: { type: direct_detection }
        vlm: on_ambiguous          # 신규 프롬프트 기본
        alert_level: low
        cooldown_sec: 60
        description: "소화기 탐지(사용자 정의)"
```
동시에 `OpenVocabDetector.set_classes(["소화기", ...])`로 임베딩 갱신 → **저장 즉시 다음 프레임부터 탐지**(재export·재학습 0).

**가드:**
- 신규 클래스는 **항상 2차 VLM 확인 기본** + 초기 며칠 "후보(미확정)" 라벨로 알림 강도 하향 → 오탐 폭주 방지(green≠작동의 탐지판).
- 동의어 정규화 레이어 여부는 결정 필요(§open_questions).

---

## 4. 정직한 한계 (과장 금지 — Superb 마케팅 vs 현실)

| 축 | 현실 |
|---|---|
| **임의 객체 정확도** | "**아무거나** 정확히"는 마케팅. 실제로는 흔한 객체(사람·차량·일반 사물)는 양호, **희귀·추상·소형·도메인 특수물**(특정 장비, "넘친 기름")은 오탐·미탐 多. open-vocab AP(LVIS)는 closed-set 대비 낮음. |
| **실시간성** | open-vocab은 closed YOLO26보다 느림(텍스트 임베딩 매칭 오버헤드). DGX Spark에선 실시간 가능 예상이나 **저사양/Windows ARM64(DirectML)** 설치형은 FPS 저하 → `skip_frames` 상향·해상도 하향 불가피. |
| **저사양 디바이스** | YOLO-World-S/N 경량본 + CPU/DirectML EP는 동작하나 동시 카메라 수·FPS 타협 필요. transformer 계열(GDINO/OWL)은 저사양 1차 부적합. |
| **오탐** | 텍스트 프롬프트가 모호하면("위험한 것") 곧장 오탐. **명사·구체 객체 프롬프트**가 잘 동작, **상황·관계**는 VLM 2차에 맡겨야 함. |
| **VLM grounding 속도** | qwen3-vl bbox는 초 단위 → **실시간 1차 불가**, 이벤트 트리거 시 저빈도 폴백만. |
| **라이선스** | YOLO-World(GPLv3)/YOLOE(AGPL) 가중치를 상용 설치형에 동봉 시 **법적 블로커 가능** — MVP 전 결정. |

---

## 5. 단계적 도입 (MVP → 고도화)

### Phase O0 — 라이선스·실측 게이트(코드 전)
- YOLO-World/YOLOE/OWLv2 가중치 **상용 납품 가능 라이선스 확정**(법무/사업).
- DGX Spark에서 YOLO-World ONNX **실측 FPS·동시 카메라** 벤치 → skip_frames 정책 근거.
- CLIP 텍스트 인코더 동봉 방식 결정(런타임 동봉 vs 빌드타임 사전계산).

### Phase O1 — open-vocab 1트랙 MVP
- `OpenVocabDetector` + `MultiModelRegistry` `open_vocab_onnx` 타입.
- 클래스 임베딩 빌드타임 사전계산(.npz) — **고정 vocab으로 먼저 종단 검증**(real ONNX→정책→알람 한 바퀴, 픽스처 우회 금지).
- `config/model_profiles.yaml`에 `open_vocab_yolo` 엔트리 + identity class_map.

### Phase O2 — "텍스트로 즉시" 동적 vocab
- CLIP 텍스트 인코더 동봉 → 대시보드 "탐지 추가" 폼 → `set_classes()` 런타임 갱신.
- 프롬프트→`event_policies.yaml` 자동 생성 + `vlm: on_ambiguous` 기본.
- 신규 클래스 "후보(미확정)" 알림 강도 하향.

### Phase O3 — VLM grounding 폴백
- `VLMProvider.ground()` + qwen3-vl bbox JSON 파서 + relative→absolute 좌표 환산.
- open-vocab 미탐 케이스를 저빈도 VLM grounding으로 위임(이벤트 트리거 한정).

### Phase O4 — 2트랙·고도화
- YOLOE 고정-vocab 트랙(표준 이벤트 고속·고정밀) 병행.
- 동의어 정규화, 프롬프트별 임계값 자동 튜닝, 오탐 피드백 루프(사용자 "오탐" 클릭→min_confidence 상향 제안).
- 저사양/ARM64 EP 폴백 성능 프로파일.

---

## 참고(웹 검증 출처)
- YOLO-World ONNX 런타임 클래스 교체: github.com/ibaiGorordo/ONNX-YOLO-World-Open-Vocabulary-Object-Detection
- YOLOE(ICCV'25): github.com/THU-MIG/yoloe, docs.ultralytics.com/models/yoloe, arxiv 2503.07465
- 라이선스: AILab-CVC/YOLO-World(GPLv3), Ultralytics/YOLOE(AGPL-3.0)
- Qwen3-VL grounding(relative coords, bbox_2d JSON): qwen.ai blog, DeepWiki QwenLM/Qwen3-VL
- onnxruntime EP(Win ARM64 DirectML, macOS CoreML): onnxruntime.ai/docs/execution-providers
- Grounding DINO 1.5 Edge FPS: digitalocean/emergentmind 자료

---

## 핵심 권장 요약
- 1순위 open-vocab 엔진은 YOLO-World(ONNX, 이미지+텍스트임베딩 2-input) — 유일하게 '재export 없이 런타임 클래스 변경'이 가능해 '텍스트로 즉시 탐지' UX의 핵심. CLIP 텍스트 인코더는 클래스 등록 시 1회만 돌려 .npz 임베딩 캐시(검증: ibaiGorordo ONNX-YOLO-World 구현이 이 구조를 직접 지원).
- YOLOE는 정확도/속도는 우위(+3.5 AP LVIS, 1.4x 빠름)지만 ONNX export 시 클래스가 그래프에 folded되어 동적 변경 불가 → '미리 정해진 프롬프트 셋'을 굽는 2순위 고정-vocab 트랙으로만. 라이선스도 AGPL-3.0(상용 시 Ultralytics Enterprise 필요).
- qwen3-vl(이미 보유)을 grounding 폴백으로 재활용 — Qwen3-VL은 bbox_2d JSON 출력 지원. open-vocab 모델이 못 잡는 희귀/추상 객체는 저빈도로 VLM grounding에 위임(실시간 1차는 YOLO-World, 정밀/임의는 VLM 2차). 새 의존성 0.
- 현 아키텍처에 끼우는 최소 변경 경로: YOLODetector 옆에 OpenVocabDetector를 두고 MultiModelRegistry.SUPPORTED_TYPES에 'open_vocab_onnx' 추가. class_adapter는 open-vocab엔 identity(프롬프트 클래스명=표준명)로 우회. EventPolicyEngine은 무수정 — trigger.classes에 프롬프트 클래스명을 그대로 넣으면 동작.
- 라이선스 게이트를 로드맵 1단계에 포함 — YOLO-World는 GPLv3, 가중치는 Tencent. AGPL/GPL 가중치를 설치형 상용 납품에 쓰면 배포 블로커. Apache/MIT 대안(OWLv2=Apache-2.0) 또는 상용 라이선스 확보를 MVP 전 결정해야 함(이미 ppe/fire 모델도 AGPL 경고가 model_profiles.yaml에 박혀있음).
- 정직한 실시간성 한계 명시 — open-vocab은 closed-set YOLO26보다 느리고(텍스트 임베딩 매칭 오버헤드), DGX Spark(고사양)에선 실시간 가능하나 저사양/Windows ARM64(DirectML EP) 설치형에선 FPS 저하·skip_frames 상향 불가피. '아무거나 정확히'는 마케팅, 현실은 '흔한 객체는 잘, 희귀/추상/소형은 오탐·미탐' 구간 존재.
- 프롬프트→정책 자동 배선 UX는 '클래스명 + 룰 템플릿(direct/count/zone/proximity) + alert_level + vlm 모드' 폼으로 학습 없이 즉시 event_policies.yaml 엔트리를 생성하되, 신규 프롬프트 클래스는 항상 vlm: on_ambiguous 기본값으로 굽어 초기 오탐을 VLM이 거르게 한다(green≠작동: 임의 클래스는 신뢰도 보정 전이라 2차 확인 필수).

## 구현 전 결정·검증 필요(open questions)
- 상용 납품 라이선스: YOLO-World(GPLv3)/YOLOE(AGPL) 가중치를 설치형 제품에 동봉 가능한가, 아니면 OWLv2(Apache-2.0)·상용계약·자체 export본으로 가야 하는가 — 법무/사업 결정 필요(현 ppe/fire 모델 AGPL 경고와 동일 사안).
- DGX Spark(GB10/CUDA13)에서 onnxruntime-gpu가 YOLO-World 2-input(이미지+텍스트임베딩) 그래프를 CUDA EP로 실제 몇 FPS 내는지 미측정 — 640x640, 동시 N카메라 기준 벤치 필요(skip_frames 정책 결정 근거).
- CLIP 텍스트 인코더를 설치형에 어떻게 동봉할지 — onnxruntime용 CLIP text ONNX(~수백MB) 추가 동봉 vs 클래스 임베딩을 빌드타임에 사전계산해 제품엔 .npz만 포함(런타임 임의 신규 프롬프트 불가) 중 택. '진짜 임의 텍스트 즉시'를 원하면 CLIP 동봉 필수.
- Windows ARM64/macOS 설치형의 EP 폴백 정확도 — DirectML/CoreML EP에서 YOLO-World ONNX가 CUDA 대비 수치 동일성·속도 손실이 어느 정도인지(특히 ARM64 DirectML은 최근 수정된 경로라 검증 필요).
- open-vocab 결과를 EventPolicyEngine에 넣을 때 클래스명 정규화 규칙 — 사용자가 'fire extinguisher'와 'extinguisher'를 따로 입력하면 별 클래스로 잡힘. 동의어/정규화 레이어를 둘지, 프롬프트를 그대로 SSOT로 둘지 결정.
- qwen3-vl grounding 좌표가 relative(0~1) coords로 바뀐 점(Qwen3-VL) — 현 pipeline의 bbox 역보정(letterbox) 경로와 정합되는지, VLM grounding bbox를 EventPolicyEngine rule(zone/proximity)에 먹일 때 좌표계 변환 필요.

## 사실 검증 플래그(근거·출처)
- [웹확인] YOLO-World ONNX(ibaiGorordo 구현)는 이미지+사전계산 텍스트임베딩(.npz)을 ONNX 입력으로 받아 '재export 없이 런타임 클래스 변경 가능'. 단 export 시 '클래스 개수'는 고정(개수 내에서 임베딩만 교체) — 출처: github.com/ibaiGorordo/ONNX-YOLO-World-Open-Vocabulary-Object-Detection.
- [웹확인] YOLOE(THU-MIG, ICCV2025)는 text-prompted ONNX export 시 프롬프트가 그래프에 folded되어 배포본은 외부 텍스트 인코더 불필요하나 클래스가 고정됨(동적 변경=재export). LVIS +3.5AP vs YOLO-Worldv2, 1.4x 빠름. 라이선스 AGPL-3.0.
- [웹확인] YOLO-World 라이선스 GPLv3(Tencent AILab-CVC), 상용 문의 yixiaoge@tencent.com. YOLOE/Ultralytics 계열은 AGPL-3.0(상용=Enterprise License). 출처: AILab-CVC/YOLO-World LICENSE, Ultralytics docs.
- [웹확인] Qwen3-VL은 2D grounding을 relative coords로 출력, bbox_2d/label JSON 구조, 수백개 박스까지 생성 가능, 3D bbox도 지원. 출처: qwen.ai blog, DeepWiki QwenLM/Qwen3-VL, roboflow blog.
- [웹확인] onnxruntime EP: Windows ARM64 DirectML 최근 수정/지원, macOS CoreML(10.15+, Neural Engine 권장) 지원 — 크로스플랫폼 설치형 가능성 뒷받침. 출처: onnxruntime.ai/docs/execution-providers.
- [검증필요] DGX Spark(sm_121/CUDA13)에서 YOLO-World ONNX 실측 FPS·동시 카메라 처리량 — 현 레포에 벤치 없음.
- [검증필요] OWLv2 정확한 라이선스(Apache-2.0로 추정/Google)와 ONNX export 실시간성 — 이번 검색에서 OWLv2는 self-training 스케일 언급만 확보, FPS/export 세부 미확인.
- [지식기반] Grounding DINO 1.5 Edge는 A100 TensorRT FP32 75.2FPS, Orin NX 10+FPS(640)로 transformer 계열 중 비교적 빠르나 YOLO 계열보다 무겁고 설치형 동봉 복잡 — CCTV 1차 필터엔 부적합, 정밀 트랙 후보.
