# ADR-0001: 다중 모델 + 이벤트 정책 엔진 아키텍처 채택

## Status

Accepted

## Context

초기 KSI-CCTV는 단일 YOLO 모델과 VLM 보조 분석을 기반으로 동작했습니다. 그러나 사업용 지능형 CCTV는 카메라별로 감지 목적이 다르고, 이벤트별로 필요한 모델과 판단 방식이 다릅니다.

예:

- 제한구역 침입은 YOLO + zone rule만으로 충분
- 안전모 미착용은 PPE YOLO + overlap rule 필요
- 화재는 fire/smoke 모델 + VLM 확인 필요
- 쓰러짐/싸움은 VLM 또는 pose 모델 확인 필요

또한 고객이 자체 모델을 업로드해 사용하는 커스터마이징 요구가 예상됩니다.

## Decision

KSI-CCTV는 다음 구조로 확장합니다.

1. Model Profile로 모델 정의
2. Camera Profile로 카메라별 도메인/이벤트/모델 선택
3. Event Policy로 이벤트별 trigger/rule/VLM/alert 정책 정의
4. MultiModelRegistry로 여러 모델 관리
5. EventPolicyEngine으로 이벤트 판단
6. VLM은 필요한 이벤트에만 호출

## Consequences

장점:

- 카메라마다 다른 감지 기능 적용 가능
- 이벤트별로 YOLO-only/VLM-required/rule-based 판단 가능
- 사용자 업로드 모델 확장 가능
- 비개발자용 설정 UI로 확장 쉬움

단점:

- 초기 구조가 복잡해짐
- 설정 검증과 테스트 필요성이 커짐
- 모델 업로드는 보안 검토가 필수

## Alternatives Considered

### 단일 YOLO + 모든 이벤트 VLM 확인

간단하지만 VLM latency가 높고 운영비/실시간성이 나쁩니다.

### 모든 이벤트를 하나의 커스텀 YOLO로 학습

운영은 단순하지만 이벤트별 특성이 달라 정확도와 유지보수성이 떨어집니다.

### 다중 모델 + 정책 엔진

구현은 복잡하지만 사업 확장성과 정확도 측면에서 가장 적합합니다.
