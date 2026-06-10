# E2E 검증 체크리스트

이 체크리스트는 KSI-CCTV의 다음 즉시 개발 마일스톤인 **샘플 영상 End-to-End 검증**을 위한 실행 절차입니다.

## 사전 조건

- Python 가상환경 `.venv/` 존재
- `models/yolo26.onnx` 존재
- Ollama 실행 중
- `qwen3-vl:latest` 모델 설치됨
- 테스트 영상 `data/sample.mp4` 준비

## 1. 환경 설정

샘플 영상이 없다면 먼저 생성합니다.

```bash
python3 scripts/create_sample_video.py --output data/sample.mp4
```

```bash
cp .env.example .env
```

`.env`에서 최소 아래 값을 설정합니다.

```env
CCTV_CAM_001_URL=./data/sample.mp4
KSI_CCTV_DASHBOARD_PASSWORD=change-me
```

주의: 실제 RTSP 계정/비밀번호는 저장소에 커밋하지 않습니다.

## 2. 카메라 설정 확인

`config/cameras.json`에서 `cam_001`이 활성화되어 있어야 합니다.

```json
{
  "id": "cam_001",
  "stream_url": "${CCTV_CAM_001_URL}",
  "enabled": true
}
```

## 3. 실행

먼저 Streamlit을 띄우지 않는 스모크 테스트로 모델/영상 경로를 확인합니다.

```bash
python3 scripts/e2e_smoke_test.py
```

Ollama VLM까지 함께 확인하려면 다음을 실행합니다.

```bash
python3 scripts/e2e_smoke_test.py --with-vlm
```

이후 대시보드를 실행합니다.

```bash
scripts/run_dashboard.sh
```

브라우저에서 다음 주소를 엽니다.

```text
http://localhost:8080
```

## 4. 확인 항목

### 영상/카메라

- [ ] cam_001이 목록에 표시됨
- [ ] 프레임이 실시간으로 표시됨
- [ ] 로컬 영상이 끝난 뒤 반복 재생됨
- [ ] dropped frames가 과도하게 증가하지 않음

### YOLO

- [ ] 사람 또는 COCO 객체가 탐지됨
- [ ] 탐지 박스가 화면에 표시됨
- [ ] 실제 `models/yolo26.onnx` 출력 shape가 로그 또는 디버그 출력으로 확인됨
- [ ] `postprocess_mode`가 실제 모델 출력과 맞음

### VLM

- [ ] Ollama 연결 상태가 정상으로 표시됨
- [ ] VLM 분석 요청이 실행됨
- [ ] VLM description/risk_level/confidence가 UI에 표시됨
- [ ] VLM 타임아웃 또는 JSON 파싱 실패 시 UI/로그에서 확인 가능함

### 알람/저장

- [ ] `logs/alerts.log`가 생성됨
- [ ] 이벤트 JSON이 기록됨
- [ ] `data/captures/`에 캡처 이미지가 저장됨
- [ ] 얼굴 블러링이 적용됨
- [ ] SMTP 설정 시 이메일이 발송됨

## 5. 성공 기준

다음 조건을 모두 만족하면 M1.1을 완료로 봅니다.

- [ ] 대시보드 실행 성공
- [ ] 샘플 영상 프레임 표시 성공
- [ ] YOLO 탐지 결과 표시 성공
- [ ] VLM 분석 결과 표시 또는 실패 사유 명확히 표시
- [ ] 이벤트 로그 생성 성공
- [ ] 캡처 이미지 저장 성공
- [ ] 발견된 문제를 이슈 목록으로 정리

## 5.1 현재 스모크 테스트 기준선

현재 비대화형 스모크 테스트 결과는 `docs/e2e-smoke-report.md`에 기록합니다.

합성 샘플 영상은 파이프라인 경로 검증용이며, COCO `person` 탐지를 보장하지 않습니다.
실제 탐지 박스 검증은 실제 사람 영상 또는 실제 RTSP 카메라로 수행해야 합니다.

## 6. 실패 시 기록할 정보

- 실행 명령
- 오류 로그 전문
- OS/아키텍처
- Python 버전
- ONNX Runtime provider 목록
- `models/yolo26.onnx` 출력 shape
- Ollama `/api/tags` 응답의 모델 목록
