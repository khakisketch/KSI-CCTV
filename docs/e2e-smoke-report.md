# E2E 스모크 테스트 결과

실행일: 2026-05-27

## 실행한 검증

```bash
./.venv/bin/python scripts/create_sample_video.py --output data/sample.mp4
python3 -m compileall -q src tests scripts
./.venv/bin/python -m unittest discover tests -v
./.venv/bin/python scripts/e2e_smoke_test.py
./.venv/bin/python scripts/e2e_smoke_test.py --with-vlm
```

## 결과 요약

- Python 컴파일: 성공
- 유닛 테스트: 32 tests OK
- 샘플 영상 생성: 성공 (`data/sample.mp4`)
- 카메라/영상 프레임 읽기: 성공
- YOLO ONNX 세션 생성: 성공
- YOLO 실제 output shape 확인: 성공
- Ollama VLM 연결: 성공
- Qwen3-VL 분석 호출: 성공

## 확인된 모델 메타데이터

- ONNX provider: `CPUExecutionProvider`
- ONNX input:
  - name: `images`
  - type: `tensor(float16)`
  - shape: `[1, 3, 640, 640]`
- ONNX output:
  - shape: `[1, 300, 6]`
  - dtype: `float32`

## 스모크 테스트 출력 요약

비대화형 스모크 테스트 기준:

- `ok`: `true`
- camera: `cam_001`
- source: local file
- frame shape: `[480, 640, 3]`
- detection count: `0`
- safety violations: `0`

VLM 포함 스모크 테스트 기준:

- Ollama available: `true`
- risk_level: `low`
- description: `No safety hazards detected in the synthetic sample frame`
- VLM inference time: 약 `52초`

## 해석

이번 검증은 Streamlit 대시보드를 띄우지 않는 **비대화형 스모크 테스트**입니다.
영상 입력 → 프레임 읽기 → YOLO ONNX 추론 → 후처리 → VLM 호출 경로가 동작함을 확인했습니다.

다만 생성된 샘플 영상은 합성 도형 기반이므로 COCO `person` 탐지가 발생하지 않았습니다.
따라서 다음 수동 검증에서는 실제 사람/현장 영상 또는 실제 RTSP 카메라로 YOLO 탐지 박스와 대시보드 표시를 확인해야 합니다.

## 남은 M1.1 수동 확인

- [ ] 실제 사람 포함 영상 또는 RTSP 1대 준비
- [ ] `scripts/run_dashboard.sh`로 Streamlit 대시보드 실행
- [ ] 브라우저에서 프레임 표시 확인
- [ ] 실제 영상에서 YOLO detection box 표시 확인
- [ ] 알람 로그/캡처 이미지가 운영 UI 흐름에서 생성되는지 확인
- [ ] 장시간 실행 시 FPS/dropped frame 확인

## 후속 개선 반영

- COCO 기본 모델에서 `person` 탐지 프레임을 `vlm_analysis_candidate`로 VLM에 제출하는 경로를 추가했습니다.
- 이 후보 이벤트는 AlertManager로 전달하지 않아 이메일/캡처 저장 알람 폭주를 방지합니다.
