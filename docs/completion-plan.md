# KSI-CCTV 시스템 완성 로드맵

> 역사적 로드맵입니다. 현재 release 기준은 `docs/release-gate.md`, `docs/internal-ci.md`,
> `docs/offline-appliance.md`이며 운영 UI는 Next.js 단일 콘솔입니다. 아래 체크박스의 과거 완료 표시는
> Advanced-AI v1 출고 승인을 뜻하지 않습니다.

이 문서는 현재 구현된 KSI-CCTV를 **납품 가능한 현장 운영 시스템**으로 완성하기 위한 총 작업 계획입니다.

## 현재 기준선

### 권위 있는 현재 마일스톤: Advanced-AI v1

- **대상:** DGX Spark ARM64 offline Appliance, HLS, single-admin.
- **구현됨:** COCO/PPE/화재·연기 정책 탐지와 ByteTrack `line_crossing`/`loitering` tracking.
- **개발 중:** pose/ST-GCN fall/fight, bounded local VLM verification. VLM 실패는 alert를 억제하지 않고
  `unverified`로 기록·전달한다.
- **제외:** WebRTC, cloud/native app, RBAC, production 기본 open-vocab, LLM reporting, 자동 few-shot/GNN,
  theft/weapons/smoking.
- **출고 전 qualification:** ARM64 CUDA artifact, 모델/데이터 라이선스와 현장 정확도, 실제 RTSP/HLS,
  복구와 목표 동시 채널 soak. 16채널은 아직 증명되지 않았다.

자동 소프트웨어 게이트는 CI의 Ruff, Python 테스트, 프론트엔드 lint/test/build를 포함한다.
`scripts/stress_test.py`는 sequential camera ID 기반 정책 상태·임계값 검증이며 동시 1~16채널
처리 용량의 증거가 아니다. 실제 장비 검증과 별도로 RC 환경에서 실행 결과를 보관한다.
카메라, 조도/IR, 네트워크, GPU 열 상태를 포함한 물리 게이트는 아직 수행되지 않았으며 현장 장비에서 별도로 기록한다.

승인된 초기 파일럿 원격접속은 LAN/VPN으로 제한하며, Tracking 이벤트는
`line_crossing`, `loitering`만 포함한다. 기존 detection/health 이벤트는 별도 정책을 따른다.

현재 구현 완료된 핵심 구성은 다음과 같습니다.

- RTSP/로컬 비디오 입력: `src/capture/rtsp_capture.py`
- YOLO26 ONNX 탐지: `src/detection/yolo_detector.py`
- 파이프라인/이벤트 생성: `src/pipeline/pipeline.py`
- Ollama Qwen3-VL 연동: `src/vlm/`
- Next.js 웹 콘솔: `frontend/`
- 알람/로그/캡처 저장/얼굴 블러링/SMTP: `src/alerts/alert_manager.py`
- 기본 설정: `config/default.yaml`, `config/cameras.json`
- 테스트: `tests/`

최근 기준 검증 (Phase 10 M1, 2026-06-14):

- `./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider -q` — **전체 통과, 1 skipped** (VLM opt-in skip; 실제 수는 위 명령 실행으로 확인)
- `scripts/e2e_smoke_test.py --policy-mode` — `ok: true`, `failed_models: {}`
- `scripts/healthcheck.py --json` — `ok: true`, 12 passes, 1 warning (CUDA CPU fallback)

## 완료 정의

시스템 완성 기준은 다음과 같습니다.

1. 실제 RTSP 또는 샘플 영상 기준으로 영상 입력 → YOLO 탐지 → VLM 분석 → 알람 → 웹 콘솔 표시가 end-to-end로 검증된다.
2. 기본 COCO 모델 한계와 커스텀 safety 모델 사용 방식이 명확하다.
3. 운영자가 24시간 실행할 수 있도록 설치, 실행, 모니터링, 재시작, 로그/데이터 관리가 준비된다.
4. CCTV 영상/캡처 이미지/계정정보 등 민감 데이터 보호 기준이 문서화 및 최소 구현된다.
5. 납품/시연/장애 대응에 필요한 문서가 준비된다.

---

## Phase 1. 실제 동작 검증

### M1.1 샘플 영상 End-to-End 검증

목표: 현재 코드가 실제 입력에서 동작하는지 확인한다.

작업:

- [ ] 테스트용 `data/sample.mp4` 준비
- [ ] `.env` 생성 및 `CCTV_CAM_001_URL=./data/sample.mp4` 설정
- [ ] `config/cameras.json`에서 `cam_001.enabled=true` 확인
- [ ] `scripts/run_web.sh` 또는 `docker compose --profile web up --build`로 웹 콘솔 실행
- [ ] 파이프라인 시작 후 프레임 표시 확인
- [ ] YOLO 탐지 박스 오버레이 확인
- [ ] VLM 분석 결과 표시 확인
- [ ] `logs/alerts.log` 기록 확인
- [ ] `data/captures/` 이미지 저장 및 얼굴 블러링 확인
- [ ] 실제 `models/yolo26.onnx` 출력 shape 기록 및 후처리 모드 확정

완료 기준:

- 웹 콘솔에서 실시간 영상과 탐지 결과가 표시된다.
- VLM 분석 성공/실패 상태가 명확히 보인다.
- 알람 로그와 캡처 이미지가 생성된다.
- 실제 ONNX 출력 shape에 맞는 후처리 모드가 확정된다.

### M1.2 실제 RTSP 1대 검증

작업:

- [ ] 실제 CCTV RTSP URL을 `.env`로 주입
- [ ] 연결 실패/재연결 로그 확인
- [ ] 30분 이상 안정 실행
- [ ] FPS, dropped frame, CPU/GPU/RAM 사용량 기록

완료 기준:

- 실제 CCTV 1대 기준으로 30분 이상 중단 없이 프레임 처리된다.

---

## Phase 2. 탐지/판정 기능 완성

### M2.1 COCO 기본 모델 운영 정책 정리

목표: 기본 YOLO26n이 COCO 모델이라는 한계를 제품 동작에 반영한다.

작업:

- [x] `target_classes`와 실제 YOLO 클래스 매핑 불일치 시 경고 표시 (Phase 10 M1 — ONNX 메타데이터 기반 클래스 자동 인식, 불일치 시 경고)
- [x] COCO 모델에서는 safety gear violation 자동 생성이 비활성화됨을 UI/문서에 명시 (웹 콘솔 COCO 한계 경고 배지, `docs/configuration.md`)
- [x] `detection.safety_gear` 설정의 사용 여부 정리 (`no_vest` 비활성, `no_helmet` direct_detection으로 전환)

완료 기준:

- 운영자가 기본 COCO 모델로는 안전모/조끼/화재/연기/쓰러짐 직접 탐지가 제한됨을 명확히 알 수 있다.

### M2.2 정책 사건 기반 VLM 정밀 판정

목표: 일반 사람 관측이 아니라 정책 규칙으로 생성된 사건 후보만 VLM이 정밀 확인하도록 한다.

작업:

- [x] 일반 person 자동 VLM 제출 제거 (`vlm_trigger_classes` 기본 빈 값)
- [x] `fire`/`fall`/`fight` 등 정책에 명시된 사건 후보만 VLM 제출
- [x] 카메라 `detection_type`에 따라 construction/school 프롬프트 자동 선택 (`src/vlm/prompt_selector.py`)
- [x] VLM 결과의 `risk_level`, `detected_events`, `recommended_action`을 이벤트에 반영 (`SafetyEvent` VLM 필드)
- [x] VLM JSON 파싱 실패 시 UI에 원문/오류 표시 (웹 콘솔 "VLM 파싱 실패" 배지 + `raw_response`)
- [x] VLM 분석 큐 길이와 지연시간 모니터링 (`get_vlm_cache_stats()`, 웹 콘솔 시스템 상태 페이지)

완료 기준:

- 정상 사람 통행은 사건·캡처·VLM 호출을 만들지 않는다.
- 정책 규칙이 성립한 사건 후보는 VLM 결과와 실패 상태가 명확히 기록된다.

### M2.3 Safety YOLO 모델 전략 — 공개 pretrained + 사용자 업로드 (1차 전략 완료)

> **1차 모델 전략**: 적합 공개 pretrained ONNX 모델 큐레이션 + 사용자 업로드 모델.
> `MultiModelRegistry` / `class_adapter` / `upload_validator` / ONNX 메타데이터-우선 클래스 인식이 이 전략을 지원한다.
>
> **from-scratch 커스텀 학습은 `project-definition.md`의 명시적 비목표(현재 버전)**이므로
> 1차 전략으로 커버되지 않는 현장 정확도 요구가 확인된 경우에만 진행하는 선택/후순위 작업이다.
> 실행 절차는 `docs/phase-cd-readiness.md` Phase D를 따른다.

목표: 안전모/조끼/화재/연기/쓰러짐을 YOLO에서 직접 탐지할 수 있게 한다.

작업 — 1차 전략(완료):

- [x] 클래스 정의 확정 (Phase 9 M4: ppe_yolo=Hardhat/NO-Hardhat, fire_smoke_yolo=fire) (`docs/phase-cd-readiness.md` D0 SSOT)
- [x] 공개 pretrained 모델 큐레이션: `scripts/fetch_safety_models.py --all`로 Hugging Face 모델 다운로드/ONNX 변환
  - `models/yolo26_ppe.onnx` (Hardhat/NO-Hardhat)
  - `models/yolo26_fire_smoke.onnx` (fire — smoke는 VLM 보조)
- [x] `MultiModelRegistry` 통합: ppe_yolo·fire_smoke_yolo 활성화 (`config/model_profiles.yaml`)
- [x] `class_adapter.py` — ONNX 메타데이터 우선 클래스 인식, config 불일치 시 경고
- [x] 사용자 업로드 경로(`upload_validator.py`): 비개발자도 ONNX 모델을 업로드해 pretrained 모델 대체 가능
- [x] 데이터셋 탐색·변환 도구 (`scripts/download_datasets.py`, `convert_to_yolo.py` 등) — from-scratch 학습 선택 시 재사용 가능

작업 — 선택/후순위 (from-scratch 커스텀 학습):

- [ ] 실제 데이터셋 다운로드 및 학습 실행 (인터넷/GPU + 데이터 라이선스 필요 — `docs/phase-cd-readiness.md` Phase D)
- [ ] 검증셋 mAP 측정: `docs/phase-cd-readiness.md` D5 합격 기준(예: PPE ≥ 0.70, fire/smoke ≥ 0.60)

완료 기준:

- 운영자가 공개 pretrained 모델로 안전모/화재를 탐지할 수 있다.
- 정확도가 부족한 경우 ONNX 모델을 업로드해 즉시 대체할 수 있다.
- from-scratch 학습은 현장 데이터·GPU 확보 후 `docs/phase-cd-readiness.md` Phase D로 진행한다.

---

## Phase 3. 웹 콘솔/알람 제품화

### M3.1 웹 콘솔 운영 기능

작업:

- [x] 카메라별 상태 카드: connected, fps, dropped frames, last frame time (Phase 8 M3)
- [x] 시스템 상태 카드: CPU/RAM/GPU/디스크 사용량 (Phase 8 M3 — psutil 기반)
- [x] 이벤트 검색/필터: 카메라, 이벤트 유형, 위험도, 시간 (Phase 8 M3 + M5.3)
- [x] 이벤트 이미지 갤러리 (Phase 3 기본 구현, 갤러리 탭)
- [x] VLM 결과 상세 패널 (이벤트 카드에 `risk_level`, `description`, `recommended_action`, `raw_response`)
- [x] 이벤트 통계 차트 (Phase 8 M5.3 — 카메라별 비교 차트, 시간대별 알람)

완료 기준:

- 운영자가 웹 콘솔만 보고 시스템 상태와 이벤트 이력을 확인할 수 있다.

### M3.2 알람 정책 완성

작업:

- [x] 위험도별 알람 정책: medium 이상만 이메일 (Phase 8 M4 — `_should_send_email()`)
- [x] 이벤트 중복 제거 및 쿨다운 정책 고도화 (EventPolicyEngine cooldown, VLM TTL cache)
- [x] 이미지 보관 기간 정리 스케줄러 연결 (6시간 throttle — Phase 10 A)
- [x] SMTP 설정 검증/테스트 버튼 추가 (Phase 8 M4 — 웹 콘솔 시스템 상태/설정 페이지)

완료 기준:

- 불필요한 알람 폭주 없이 중요한 이벤트만 전달된다.

---

## Phase 4. 운영 안정화

### M4.1 모니터링/헬스체크

작업:

- [x] FPS, inference latency, VLM latency 수집 (`get_model_metrics()`, `OllamaProvider.get_metrics()`)
- [x] 카메라별 마지막 프레임 수신 시간 기록 (`last_frame_time`, `is_stale`, `get_status()`)
- [x] 디스크 사용량/로그 크기 모니터링 (`src/utils/system_metrics.py`, 헬스체크)
- [x] 헬스체크 CLI 및 HTTP endpoint 추가 (`scripts/healthcheck.py`, `src/utils/health.py` — `/health`, `/healthz`, `/health/live`, `/health/ready`)
- [x] 장애 상태를 웹 콘솔에 표시 (시스템 상태 페이지)

완료 기준:

- 장애 원인이 카메라/모델/VLM/디스크/리소스 중 어디인지 구분 가능하다.

### M4.2 실행/배포 자동화

작업:

- [x] `scripts/install.sh` 작성 (Phase 7)
- [x] `systemd/ksi-cctv.service` 작성 (Phase 7)
- [x] `scripts/check_environment.py` 작성 (Phase 7 — `scripts/check_environment.py`)
- [x] Dockerfile ARM64/GPU 호환성 점검 (Dockerfile, `.dockerignore`, 비root USER)
- [x] docker-compose healthcheck 추가 (`docker-compose.yml`)

완료 기준:

- 새 장비에서 문서대로 설치 후 자동 실행 가능하다.

---

## Phase 5. 테스트/품질

### M5.1 통합 테스트 추가

작업:

- [x] mock video source 기반 pipeline integration test (`tests/test_integration.py`)
- [x] 실제 ONNX shape smoke test (`tests/test_e2e_real.py`)
- [x] VLM mock/real 호출 테스트 분리 (`tests/test_vlm.py` — VLM opt-in, 1 skipped)
- [x] alert image save/cleanup 테스트 (`tests/test_pipeline.py` 포함)
- [x] web console config load smoke test (헬스체크 `--json` 기반)

완료 기준:

- 주요 회귀가 자동 테스트로 잡힌다.

### M5.2 코드 품질 도구

작업:

- [x] `ruff` 설정 (`ruff.toml`)
- [ ] `mypy` 또는 pyright 적용 범위 선정 (현재 미적용 — 추후 `pyproject.toml` 도입 시 추가)
- [x] `requirements-dev.txt` 분리
- [x] CI 및 `scripts/test.sh` 추가 (Ruff, Python 테스트, 프론트엔드 lint/test/build)
- [x] 1~16개 sequential camera ID 정책 상태 스트레스 gate (`scripts/stress_test.py`, latency/RSS/실패 임계값; 동시 채널 용량 시험 제외)

완료 기준:

- 개발자가 동일한 검증 명령으로 품질 확인 가능하다.
- 자동 게이트 통과는 물리 카메라, 야간/IR, 장기 soak 또는 현장 네트워크 검증을 주장하지 않는다.

---

## Phase 6. 보안/개인정보

### M6.1 접근 보호

작업:

- [x] 로그인 실패 rate limit (Phase 8 M5.1 — 60초 내 5회 실패 시 60초 잠금)
- [x] 세션 만료 (Phase 8 M5.1 — 30분 무활동 자동 로그아웃)
- [x] 감사 로그 강화: 로그인/로그아웃/설정 변경/이미지 조회/모델 업로드 (`logs/audit.log`)
- [ ] 운영 환경 HTTPS 가이드 (`docs/operations.md`에 보안 체크리스트 추가됨 — nginx/TLS 상세 가이드는 배포 환경별 별도 작성 필요)

완료 기준:

- 웹 콘솔 접근 기록이 남고 기본적인 무차별 대입 공격을 완화한다.

### M6.2 영상/이미지 보호

작업:

- [x] 캡처 이미지 암호화 저장 옵션 (`encrypt_images: true` + `KSI_CCTV_CAPTURE_KEY` Fernet, `.jpg.enc`)
- [x] 얼굴 블러링 (다중 cascade 블러, `blur_faces: true`)
- [x] 데이터 보관/삭제 정책 문서화 (`docs/operations.md` 데이터 보관 정책 섹션)
- [x] RTSP URL/SMTP 비밀값 로그 노출 점검 (`mask_url()`, `mask_text()` — Phase 8 M5.3)

완료 기준:

- CCTV 캡처 이미지와 계정정보 노출 위험이 최소화된다.

---

## Phase 7. 문서/납품 패키지

작업:

- [x] `docs/architecture.md` (갱신 완료 — policy-mode 기준)
- [x] `docs/configuration.md` (신규 작성)
- [x] `docs/operations.md` (신규 작성)
- [x] `docs/troubleshooting.md` (신규 작성)
- [x] `docs/custom-model-training.md` (Phase 8 M3 작성 — 스크립트 스캐폴드 + 실제 학습은 데이터/GPU 필요)
- [x] 납품용 설치 체크리스트 (`docs/delivery-checklist.md` 파트 A)
- [x] 시연용 체크리스트 (`docs/delivery-checklist.md` 파트 B)

완료 기준:

- 설치자/운영자/개발자가 문서만 보고 운영 가능하다.

---

## 권장 실행 순서

1. M1.1 샘플 영상 E2E 검증
2. M1.2 실제 RTSP 1대 검증
3. M2.1 COCO 운영 정책 정리
4. M2.2 VLM 기반 위험 판정 강화
5. M3.1 웹 콘솔 운영 기능
6. M3.2 알람 정책 완성
7. M4.1 모니터링/헬스체크
8. M4.2 실행/배포 자동화
9. M5.1 통합 테스트
10. M6.1/M6.2 보안/개인정보 강화
11. M7 문서/납품 패키지
12. M2.3 from-scratch 커스텀 학습 (선택/후순위 — `project-definition.md` 비목표)

1차 모델 통합은 구현 상태일 뿐, 제품 데모나 납품 가능 주장에 충분하지 않다. Advanced-AI v1은
`docs/release-gate.md`의 software, hardware, data, model/legal evidence가 모두 있어야 출고할 수 있다.
