# 이벤트 정책 엔진 설계

## 목적

EventPolicyEngine은 모델 탐지 결과를 실제 안전 이벤트로 해석합니다. 같은 `person` 탐지라도 카메라/도메인/정책에 따라 무시, 알람, VLM 분석 후보가 될 수 있습니다.

## 핵심 입력

- camera profile: 카메라 도메인/활성 이벤트/zone 설정
- model detections: `{model_name: [Detection]}`
- event policies: 이벤트별 trigger/rule/vlm/alert 설정
- frame image: VLM/캡처용 프레임

## 정책 평가 흐름

```text
for each enabled event policy:
  1. trigger model/classes 확인
  2. confidence threshold 확인
  3. rule evaluator 실행
  4. VLM 필요 여부 결정
  5. SafetyEvent 생성
  6. cooldown/alert level 적용
```

## Rule 유형

### direct_detection

특정 클래스가 탐지되면 이벤트를 생성합니다.

사용 예:

- fire
- smoke
- person_detected

### safety_gear_overlap

person bbox와 helmet/vest bbox의 위치 관계로 착용 여부를 판단합니다.

사용 예:

- no_helmet
- no_vest

### zone_intrusion

person bbox 중심점이 제한구역 polygon 내부에 들어오면 이벤트를 생성합니다.

사용 예:

- restricted_area
- school_intrusion

### count_threshold

특정 클래스 수가 기준 이상이면 이벤트를 생성합니다.

사용 예:

- crowding

### proximity

두 클래스 간 거리가 기준 이하이면 이벤트를 생성합니다.

사용 예:

- equipment_proximity

### pose_anomaly

person bbox 종횡비, 위치 변화, temporal 정보를 이용해 쓰러짐 후보를 생성합니다.

사용 예:

- fall

## VLM 정책

| 값 | 의미 |
|---|---|
| never | VLM 미사용 |
| always | 항상 VLM 분석 |
| on_ambiguous | confidence가 애매할 때만 |
| on_high_confidence | 고신뢰 후보만 분석 |
| on_low_confidence | 저신뢰 후보만 확인 |

## SafetyEvent 확장 필요 필드

- alert_level: low, medium, high, critical
- cooldown_sec: 이벤트별 쿨다운
- vlm_needed: VLM 분석 필요 여부
- source_models: 어떤 모델이 이벤트를 만들었는지
- rule_name: 어떤 rule이 이벤트를 만들었는지

현재 Phase 3 구현에서는 파이프라인 통합 전 독립 검증을 위해 위 필드를 `SafetyEvent` 동적 속성으로 붙입니다. Phase 4에서 `SafetyEvent` dataclass 정식 필드로 반영해야 합니다.

## 구현 상태

- `src/policy/rule_evaluators.py`: rule evaluator registry 구현 완료
- `src/policy/event_policy_engine.py`: profile policy 평가와 `PolicyDecision` 생성 구현 완료
- `tests/test_policy.py`: 독립 단위 테스트 추가 완료
- Pipeline 통합은 아직 미완료이며 Phase 4 범위입니다.

## 구현 순서

1. config/event_policies.yaml 작성 완료
2. RuleEvaluatorRegistry 구현 완료
3. EventPolicyEngine 구현 완료
4. 테스트 작성 완료
5. Pipeline 통합 예정
