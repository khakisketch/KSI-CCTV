# KSI-CCTV 아키텍처/제품 결정 기록

## 2026-05-30 — ADR-0001: 다중 모델 + 이벤트 정책 엔진 아키텍처 채택

**Status**: Accepted

**Context**: 초기 KSI-CCTV는 단일 YOLO 모델과 VLM 보조 분석 기반이었으나, 사업용 지능형 CCTV는 카메라별 감지 목적이 다르고 이벤트별 판단 방식이 달라야 합니다.

**Decision**: Model Profile → Camera Profile → MultiModelRegistry → EventPolicyEngine → VLM Dispatcher 구조로 확장합니다.

**Consequences**:
- 장점: 카메라별 다른 감지 기능, 이벤트별 YOLO-only/VLM-required/rule-based 판단, 사용자 모델 확장 가능
- 단점: 초기 구조 복잡도 증가, 설정 검증/테스트 필요성 증가

**Alternatives considered**: 단일 YOLO + 모든 이벤트 VLM(실시간성 저하), 모든 이벤트 커스텀 YOLO(정확도/유지보수성 저하)

---

## 2026-05-30 — 모델 역할 분리

**Decision**: 기본 YOLO26n은 COCO 80 클래스 기반이므로 `helmet`, `vest`, `fire`, `smoke`, `fall` 직접 탐지는 별도 커스텀 모델이 필요합니다.

- `general_yolo`: COCO person/vehicle/general object
- `ppe_yolo`: helmet/vest (향후 학습)
- `fire_smoke_yolo`: fire/smoke (향후 학습)
- `pose_fall_model`: fall/abnormal posture (향후 학습)
- VLM: 설명/애매한 이벤트 확인

---

## 2026-05-30 — 이벤트 판단 원칙

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

---

## 2026-05-30 — 사용자 모델 업로드 정책

**Decision**: 초기 ONNX만 허용. `.pt`, `.pkl`, `.py`, Python 코드, 실행 파일 금지. 업로드 모델은 검증 후 `enabled: false`로 등록.

**보안 조건 (Phase 6 review 승인)**:
1. ONNX only
2. 확장자 검증
3. 파일명 sanitize (path traversal 차단)
4. 업로드 디렉토리 격리
5. 파일 크기 제한 (500MB)
6. ONNX load + shape validation
7. inference timeout (30s)
8. 감사 로그
9. 관리자 인증 / disabled-by-default

---

## 2026-05-30 — VLM 모델 선택

**Decision**: Ollama `qwen3-vl:latest` 사용. 로컬 실행, 외부 API 의존성 없음. smoke latency 약 52초 확인 완료.

---

## 2026-05-30 — 외부 SaaS 미사용

**Decision**: Slack Webhook, Sentry 등 외부 SaaS 사용하지 않음. SMTP 이메일, 로컬 로그, Streamlit 대시보드로 자체 알림/모니터링.

---

## 2026-05-30 — 보안 방향

**Decision**:
- RTSP URL 마스킹
- 얼굴 블러링 (AlertManager)
- scrypt 기반 대시보드 비밀번호 해싱
- 감사 로그 (로그인 성공/실패, 모델 업로드/활성화/삭제)
- 업로드 모델 격리 및 disabled-by-default

---

## 2026-05-30 — 중앙집중형 구조

**Decision**: CCTV가 네트워크 허브/랜선 기반으로 중앙 컴퓨터에 모이는 중앙집중형 구성. 현실적 설치 환경(건설현장, 학교, 공장)에 맞춤.

---

## 2026-05-30 — Phase 분리

**Decision**: 구현 복잡도와 검증 범위를 관리하기 위해 8개 Phase로 분리:
1. 문서/설정 기반
2. MultiModelRegistry
3. EventPolicyEngine
4. Pipeline 통합
5. Dashboard Settings UI
6. User Model Upload
7. Operations/Deployment
8. Custom Safety Model

---

## 2026-05-30 — Build/Plan 아티팩트 소유권

**Decision**:
- `plan`이 `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, `.ai/review-checklist.md`를 소유
- `build`가 `.ai/task-state.md`를 소유
- `.ai/handoff.md`는 공유
- `build`는 Plan-owned artifact를 생성/수정하지 않고 `plan`으로 handoff

---

## 2026-06-10 — Phase 8/9 추가 결정

### Class Adapter 도입 (Phase 8 M1)
**Decision**: 사전학습 공개 모델의 출력 클래스명(예: `Hardhat`, `NO-Hardhat`)을 KSI-CCTV 표준 클래스(`helmet`, `no_helmet`)로 변환하는 `class_adapter.py` 도입. `model_profiles.yaml`에 `class_map` 필드로 매핑 정의.

### Fire 모델 repo 대체 (Phase 9 M4)
**Decision**: 원래 계획했던 `keremberke/yolov8n-fire-detection`이 Hugging Face Hub 401 Unauthorized로 접근 불가 → `touati-kamel/yolov8s-forest-fire-detection`으로 대체. 9-class forest fire 모델로 fire 탐지에 초점, smoke는 VLM fallback 유지.

### VLM Cache 도입 (Phase 9 M1)
**Decision**: `(camera_id, event_type)` 키로 TTL 30초 기반 VLM 캐시 도입. critical/high 이벤트는 bypass하여 안전 이벤트 누락 방지. pHash 기반 이미지 캐싱은 의존성 증가와 실사용 패턴 부족으로 채택하지 않음.

### Dynamic Frame Skip (Phase 8 M5.3)
**Decision**: 그레이스케일 absdiff 기반 모션 감지로 정적 장면에서 `skip_frames * static_skip_multiplier` 적용하여 CPU 사용량 절감. 기본값 비활성화(`dynamic_skip=False`).

### AlertManager Batch Processing (Phase 8 M5.2)
**Decision**: `AlertConfig.batch_flush_interval` (기본 0=비활성) 설정 시 이벤트를 버퍼에 모아 일정 시간/크기 도달 시 배치 flush. 배치 이메일은 medium+ 이벤트만 단일 HTML 이메일로 일괄 발송.

### Alert Preferences YAML (Phase 8 M5.2)
**Decision**: `config/alert_preferences.yaml`에 `email_alert_level_threshold` + `per_event_email_enabled` 저장. 대시보드에서 영구 저장/기본값 복구 지원. AlertManager는 초기화 시 로드하므로 변경 후 파이프라인 재시작 필요.

### URL Masking (Phase 8 M5.3)
**Decision**: `mask_url()` / `mask_text()` 유틸리티로 RTSP/HTTP URL의 계정 비밀번호를 로그에서 마스킹. RTSP: `rtsp://***:***@host`, HTTP: `http://***:***@host`.

---

## 2026-06-10 — Phase 10 방향 결정

### Git LFS 도입
**Decision**: ONNX 모델 파일(yolo26.onnx 5.9MB, yolo26_ppe.onnx 5.9MB, yolo26_fire_smoke.onnx 22MB)과 향후 `.pt` 학습 파일을 Git LFS로 관리. `.env`, 로그, 캡처 데이터는 `.gitignore`로 제외.

### 커스텀 모델 학습 전략
**Decision**: 다른 프로젝트 학습 완료 후 바로 커스텀 모델 학습 시작. 사전학습 모델 프로파일(`ppe_yolo`, `fire_smoke_yolo`)을 커스텀 학습 모델로 덮어쓰기. `model_profiles.yaml`의 `source` 필드를 `custom_trained`로 변경.

### Phase 10 병행 트랙
**Decision**: 생산 준비(Track A: 문서 동기화, Git, CUDA 검증, RTSP 시뮬레이션)와 커스텀 모델 학습(Track B: 데이터셋 준비, 학습, ONNX export, 등록)을 병행 진행.
