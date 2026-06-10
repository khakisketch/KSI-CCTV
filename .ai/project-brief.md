# KSI-CCTV 프로젝트 개요

## 제품 정의

KSI-CCTV는 **카메라마다 감지 목적을 다르게 설정하고, 기본 제공 ONNX 모델과 사용자 업로드 ONNX 모델을 조합하며, 이벤트 정책 엔진과 필요 시 로컬 VLM 분석을 함께 사용하는 제품형 지능형 CCTV 플랫폼**입니다.

## 목표 사용자

- 건설 현장 안전 관리자
- 학교/병원/공장 등 시설 안전 담당자
- CCTV 통합 관리 운영자
- AI 모델 개발자 (커스텀 ONNX 모델 제공)

## 핵심 가치

1. **유연성** — 카메라마다 다른 감지 목적 설정 가능
2. **확장성** — 기본 모델 + 사용자 ONNX 모델 조합 가능
3. **정확성** — YOLO 고속 필터링 + 필요 시 VLM 정밀 분석
4. **안전성** — 개인정보 보호(얼굴 블러), 보안 격리, 감사 로그
5. **사용성** — 비개발자도 Streamlit 대시보드로 설정 가능

## 제약 조건

- **하드웨어**: DGX Spark 실증 기준, 실제 납품 장비는 추후 가격경쟁력 있게 조정 가능
- **네트워크**: CCTV는 랜선/네트워크 허브를 통해 중앙 컴퓨터에 연결되는 중앙집중형 구성
- **외부 도구**: Slack Webhook, Sentry 등 외부 SaaS 사용하지 않음. SMTP 이메일/로컬 자체 방식 중심
- **VLM**: 로컬 Ollama `qwen3-vl:latest` 사용
- **사용자 모델**: 초기 ONNX만 허용. `.pt`, `.pkl`, `.py`, Python 코드/실행 파일 금지

## 보안/개인정보 원칙

- RTSP URL 마스킹
- 얼굴 블러링
- scrypt 기반 대시보드 비밀번호
- 업로드 모델 격리 저장 및 disabled-by-default 등록
- 업로드/활성화/삭제 감사 로그
- 관리자 인증 필요

## 비목표 (현재 버전)

- 실시간 영상 스트리밍 서비스
- 모바일 앱
- 클라우드 호스팅 서비스
- 자동 모델 학습 파이프라인
- segmentation/pose/custom operator ONNX 모델

## 도메인 컨텍스트

지원 도메인: construction, school, factory, parking, custom

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

## 모델 전략

| 모델 | 역할 | 상태 |
|---|---|---|
| general_yolo | 사람/차량/일반 객체 (COCO) | active |
| ppe_yolo | 안전모/조끼 (사전학습) | active |
| fire_smoke_yolo | 화재/연기 (사전학습, fire only) | active |
| pose_fall_model | 쓰러짐/자세 이상 | disabled (향후 제공) |
| 사용자 업로드 | 고객 커스텀 ONNX | disabled-by-default 등록 |

## 아키텍처

```text
CCTV/RTSP → Frame Sampler → Camera Profile → MultiModelRegistry → EventPolicyEngine → 필요 시 VLM Dispatcher → Alert/Dashboard/Audit
```
