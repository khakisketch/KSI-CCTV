# KSI-CCTV 시스템 완성 로드맵

이 문서는 현재 구현된 KSI-CCTV를 **납품 가능한 현장 운영 시스템**으로 완성하기 위한 총 작업 계획입니다.

## 현재 기준선

현재 구현 완료된 핵심 구성은 다음과 같습니다.

- RTSP/로컬 비디오 입력: `src/capture/rtsp_capture.py`
- YOLO26 ONNX 탐지: `src/detection/yolo_detector.py`
- 파이프라인/이벤트 생성: `src/pipeline/pipeline.py`
- Ollama Qwen3-VL 연동: `src/vlm/`
- Streamlit 대시보드: `src/dashboard/app.py`
- 알람/로그/캡처 저장/얼굴 블러링/SMTP: `src/alerts/alert_manager.py`
- 기본 설정: `config/default.yaml`, `config/cameras.json`
- 테스트: `tests/`

최근 기준 검증:

- `python3 -m compileall -q src tests scripts` 통과
- `./.venv/bin/python -m unittest discover tests -v` 기준 32 tests OK

## 완료 정의

시스템 완성 기준은 다음과 같습니다.

1. 실제 RTSP 또는 샘플 영상 기준으로 영상 입력 → YOLO 탐지 → VLM 분석 → 알람 → 대시보드 표시가 end-to-end로 검증된다.
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
- [ ] `scripts/run_dashboard.sh`로 대시보드 실행
- [ ] 파이프라인 시작 후 프레임 표시 확인
- [ ] YOLO 탐지 박스 오버레이 확인
- [ ] VLM 분석 결과 표시 확인
- [ ] `logs/alerts.log` 기록 확인
- [ ] `data/captures/` 이미지 저장 및 얼굴 블러링 확인
- [ ] 실제 `models/yolo26.onnx` 출력 shape 기록 및 후처리 모드 확정

완료 기준:

- 대시보드에서 실시간 프레임과 탐지 결과가 표시된다.
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

- [ ] `target_classes`와 실제 YOLO 클래스 매핑 불일치 시 경고 표시
- [ ] COCO 모델에서는 safety gear violation 자동 생성이 비활성화됨을 UI/문서에 명시
- [ ] `detection.safety_gear` 설정의 사용 여부 정리

완료 기준:

- 운영자가 기본 COCO 모델로는 안전모/조끼/화재/연기/쓰러짐 직접 탐지가 제한됨을 명확히 알 수 있다.

### M2.2 VLM 기반 위험 판정 강화

목표: 커스텀 YOLO 모델이 없어도 VLM이 안전 상황을 판단하도록 한다.

작업:

- [x] person 탐지 프레임을 VLM 분석 후보로 제출하는 정책 추가
- [ ] 카메라 `detection_type`에 따라 construction/school 프롬프트 자동 선택
- [ ] VLM 결과의 `risk_level`, `detected_events`, `recommended_action`을 이벤트에 반영
- [ ] VLM JSON 파싱 실패 시 UI에 원문/오류 표시
- [ ] VLM 분석 큐 길이와 지연시간 모니터링

완료 기준:

- YOLO person 탐지만으로도 VLM이 안전모/조끼/쓰러짐/화재 의심 상황을 설명할 수 있다.

### M2.3 커스텀 Safety YOLO26 모델 준비

목표: 안전모/조끼/화재/연기/쓰러짐을 YOLO에서 직접 탐지할 수 있게 한다.

작업:

- [ ] 클래스 정의 확정: `person, helmet, vest, fire, smoke, fall`
- [ ] 데이터셋 후보 수집/라이선스 검토
- [ ] 학습/검증 split 구성
- [ ] YOLO26 학습 스크립트 작성
- [ ] ONNX export 및 `class_profile: safety_custom` 적용
- [ ] 정확도 지표 측정: mAP, precision, recall

완료 기준:

- safety_custom ONNX 모델로 실제 안전 이벤트가 YOLO 단계에서 생성된다.

---

## Phase 3. 대시보드/알람 제품화

### M3.1 대시보드 운영 기능

작업:

- [ ] 카메라별 상태 카드: connected, fps, dropped frames, last frame time
- [ ] 시스템 상태 카드: CPU/RAM/GPU/디스크 사용량
- [ ] 이벤트 검색/필터: 카메라, 이벤트 유형, 위험도, 시간
- [ ] 이벤트 이미지 갤러리
- [ ] VLM 결과 상세 패널
- [ ] 이벤트 통계 차트

완료 기준:

- 운영자가 대시보드만 보고 시스템 상태와 이벤트 이력을 확인할 수 있다.

### M3.2 알람 정책 완성

작업:

- [ ] 위험도별 알람 정책: medium 이상만 이메일 등
- [ ] 이벤트 중복 제거 및 쿨다운 정책 고도화
- [ ] 이미지 보관 기간 정리 스케줄러 연결
- [ ] SMTP 설정 검증/테스트 버튼 추가

완료 기준:

- 불필요한 알람 폭주 없이 중요한 이벤트만 전달된다.

---

## Phase 4. 운영 안정화

### M4.1 모니터링/헬스체크

작업:

- [ ] FPS, inference latency, VLM latency 수집
- [ ] 카메라별 마지막 프레임 수신 시간 기록
- [ ] 디스크 사용량/로그 크기 모니터링
- [ ] 헬스체크 CLI 또는 HTTP endpoint 추가
- [ ] 장애 상태를 대시보드에 표시

완료 기준:

- 장애 원인이 카메라/모델/VLM/디스크/리소스 중 어디인지 구분 가능하다.

### M4.2 실행/배포 자동화

작업:

- [ ] `scripts/install.sh` 작성
- [ ] `systemd/ksi-cctv.service` 작성
- [ ] `scripts/check_environment.py` 작성
- [ ] Dockerfile ARM64/GPU 호환성 점검
- [ ] docker-compose healthcheck 추가

완료 기준:

- 새 장비에서 문서대로 설치 후 자동 실행 가능하다.

---

## Phase 5. 테스트/품질

### M5.1 통합 테스트 추가

작업:

- [ ] mock video source 기반 pipeline integration test
- [ ] 실제 ONNX shape smoke test
- [ ] VLM mock/real 호출 테스트 분리
- [ ] alert image save/cleanup 테스트
- [ ] dashboard config load smoke test

완료 기준:

- 주요 회귀가 자동 테스트로 잡힌다.

### M5.2 코드 품질 도구

작업:

- [ ] `ruff` 설정
- [ ] `mypy` 또는 pyright 적용 범위 선정
- [ ] `requirements-dev.txt` 분리
- [ ] CI 스크립트 또는 `scripts/test.sh` 추가

완료 기준:

- 개발자가 동일한 검증 명령으로 품질 확인 가능하다.

---

## Phase 6. 보안/개인정보

### M6.1 접근 보호

작업:

- [ ] 로그인 실패 rate limit
- [ ] 세션 만료
- [ ] 감사 로그 강화: 로그인/로그아웃/설정 변경/이미지 조회
- [ ] 운영 환경 HTTPS 가이드

완료 기준:

- 대시보드 접근 기록이 남고 기본적인 무차별 대입 공격을 완화한다.

### M6.2 영상/이미지 보호

작업:

- [ ] 캡처 이미지 암호화 저장 옵션
- [ ] 얼굴 블러링 정확도 개선 검토
- [ ] 데이터 보관/삭제 정책 문서화
- [ ] RTSP URL/SMTP 비밀값 로그 노출 점검

완료 기준:

- CCTV 캡처 이미지와 계정정보 노출 위험이 최소화된다.

---

## Phase 7. 문서/납품 패키지

작업:

- [ ] `docs/architecture.md`
- [ ] `docs/configuration.md`
- [ ] `docs/operations.md`
- [ ] `docs/troubleshooting.md`
- [ ] `docs/custom-model.md`
- [ ] 납품용 설치 체크리스트
- [ ] 시연용 체크리스트

완료 기준:

- 설치자/운영자/개발자가 문서만 보고 운영 가능하다.

---

## 권장 실행 순서

1. M1.1 샘플 영상 E2E 검증
2. M1.2 실제 RTSP 1대 검증
3. M2.1 COCO 운영 정책 정리
4. M2.2 VLM 기반 위험 판정 강화
5. M3.1 대시보드 운영 기능
6. M3.2 알람 정책 완성
7. M4.1 모니터링/헬스체크
8. M4.2 실행/배포 자동화
9. M5.1 통합 테스트
10. M6.1/M6.2 보안/개인정보 강화
11. M7 문서/납품 패키지
12. M2.3 커스텀 Safety YOLO26 모델 준비

커스텀 모델은 데이터셋/라이선스/학습 시간이 필요하므로 병행 가능하지만, 제품 데모와 초기 납품 가능성을 위해서는 먼저 E2E/VLM/운영 안정화를 끝내는 것이 좋습니다.
