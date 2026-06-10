# KSI-CCTV 목표 아키텍처

## 개요

KSI-CCTV의 목표 아키텍처는 단일 YOLO 모델에 모든 이벤트를 의존하지 않고, **카메라별 감지 목적**, **다중 모델 레지스트리**, **이벤트 정책 엔진**, **필요 시 VLM 분석**을 조합하는 구조입니다.

```text
CCTV / RTSP
  -> Frame Sampler
  -> Camera Profile
  -> MultiModelRegistry
     - general_yolo
     - ppe_yolo
     - fire_smoke_yolo
     - pose_fall_model
     - customer_uploaded_model
  -> EventPolicyEngine
     - trigger check
     - rule evaluation
     - VLM routing decision
     - alert level decision
  -> VLM Dispatcher only if needed
  -> Alert / Dashboard / Audit Log
```

## 현재 구조

현재 구현은 다음과 같습니다.

```text
RTSPCapture -> YOLODetector(single) -> SafetyPipeline hardcoded rules -> VLM optional -> AlertManager / Dashboard
```

장점:

- 단순하고 빠르게 검증 가능
- COCO YOLO + Qwen3-VL smoke test 완료
- 기본 RTSP/알람/대시보드 흐름 존재

한계:

- 카메라별 감지 목적 설정이 약함
- 모델별 역할 분리가 없음
- 이벤트별 VLM 필요 여부가 하드코딩에 가까움
- 사용자 업로드 모델을 연결할 구조가 없음

## 목표 컴포넌트

### Camera Profile

카메라별 도메인과 활성 이벤트를 정의합니다.

- 도메인: construction, school, factory, parking, custom
- 활성 이벤트: no_helmet, fire, fall 등
- 사용할 모델: general_yolo, ppe_yolo 등
- zone/rule 설정: 제한구역, 카운트 기준 등

### MultiModelRegistry

여러 모델을 등록/로드/추론하는 컴포넌트입니다.

- 기본 제공 모델과 사용자 업로드 모델 모두를 동일 인터페이스로 관리
- enabled 모델만 로드
- 모델별 결과를 `{model_name: detections}` 형태로 반환
- 모델 로드 실패가 전체 파이프라인 장애로 이어지지 않게 격리

### EventPolicyEngine

탐지 결과를 이벤트로 해석하는 정책 엔진입니다.

- 이벤트별 trigger model/classes 확인
- rule evaluator 실행
- VLM 필요 여부 결정
- alert_level/cooldown 결정

### VLM Dispatcher

모든 프레임을 VLM에 보내지 않고, 정책상 필요한 이벤트만 분석합니다.

- never: VLM 미사용
- always: 항상 확인
- on_ambiguous: 애매한 confidence일 때만
- on_high_confidence: 고신뢰 후보만 설명 보강

## 이벤트 판단 원칙

| 이벤트 | 1차 판단 | VLM |
|---|---|---|
| 사람 감지 | general_yolo | never |
| 안전모 미착용 | ppe_yolo + overlap rule | on_ambiguous |
| 조끼 미착용 | ppe_yolo + overlap rule | on_ambiguous |
| 화재 | fire_smoke_yolo | always |
| 연기 | fire_smoke_yolo | on_high_confidence |
| 쓰러짐 | pose/fall rule | always |
| 제한구역 침입 | zone rule | never |
| 혼잡 | count rule | never |

## 확장 방향

1. YAML 기반 설정으로 먼저 구성
2. 이후 Streamlit 설정 UI에서 YAML을 생성/수정
3. 사용자 ONNX 모델 업로드와 smoke test 연결
4. 모델별 성능/오류/latency 대시보드화
