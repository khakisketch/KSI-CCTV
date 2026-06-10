# KSI-CCTV: DGX Spark 기반 지능형 CCTV 안전 관리 시스템

건설 현장, 학교 등 여러 발주처의 CCTV 영상을 중앙 컴퓨터(DGX Spark)에서 분석해 불안전 행동, 쓰러짐, 안전장비 미착용, 화재/연기 등 이상 상황을 실시간 감지하고 관리자에게 알람과 자연어 설명을 제공하는 AI 기반 솔루션입니다.

## 시스템 아키텍처

- **중앙집중형 관제**: CCTV → 네트워크 허브 → DGX Spark 중앙 처리
- **1차 고속 필터링**: YOLO26 ONNX로 사람 등 후보 객체 탐지
- **2차 정밀 분석**: Ollama 기반 Qwen3-VL로 후보 프레임을 상황 설명/위험도 판단
- **대시보드**: Streamlit 기반 실시간 상태, 알람 로그, 통계 확인

## 핵심 기능

- RTSP 스트림 수신 및 프레임 큐 관리
- YOLO26/Ultralytics ONNX 추론
- YOLO raw 출력과 YOLO26 NMS-free final 출력 모두 지원
- COCO pretrained 모델과 커스텀 안전 모델 클래스 프로파일 분리
- 커스텀 안전 모델 사용 시 안전장비 미착용 이벤트 생성
- 이벤트 로그 및 캡처 이미지 저장(얼굴 블러링 기본 활성화)
- RTSP와 로컬 비디오 파일(`.mp4`, `.avi` 등) 입력 지원
- 선택적 대시보드 비밀번호 보호
- Docker Compose 기반 실행 준비

## 빠른 시작

```bash
pip install -r requirements.txt
```

YOLO26 ONNX 모델을 준비합니다.

```bash
yolo export model=yolo26n.pt format=onnx imgsz=640 half=True device=0
mkdir -p models
mv yolo26n.onnx models/yolo26.onnx
```

또는 준비 스크립트를 사용할 수 있습니다.

```bash
python3 scripts/prepare_yolo26.py --model yolo26n.pt
```

환경변수와 카메라 설정을 수정합니다.

```bash
cp .env.example .env
# .env에서 CCTV_CAM_001_URL 등을 실제 RTSP URL 또는 ./data/sample.mp4로 수정
# config/cameras.json에서 사용할 채널의 enabled를 true로 변경
```

실행합니다.

```bash
scripts/run_dashboard.sh
```

## 설정 파일

- `config/default.yaml`: 모델 경로, 임계값, 알람, 디바이스 설정
- `config/cameras.json`: CCTV 채널 설정 샘플 (`${CCTV_CAM_001_URL}` 환경변수 확장 지원)
- `config/model_profiles.yaml`: 다중 모델 프로파일 초안
- `config/event_policies.yaml`: 이벤트별 판단 정책 초안
- `config/camera_profiles.yaml`: 카메라별 도메인/이벤트/모델 선택 초안
- `.env.example`: RTSP URL과 대시보드 비밀번호 예시

> 실제 운영 환경에서는 RTSP 계정/비밀번호를 저장소에 커밋하지 말고 환경변수 또는 현장별 보안 저장소를 사용하세요.

### YOLO 클래스 프로파일

기본 `yolo26n.pt`/`yolo26.onnx`는 COCO 80 클래스 모델로 간주합니다.
따라서 `config/default.yaml`의 `models.yolo.class_profile` 기본값은 `coco`입니다.

- `coco`: 기본 pretrained 모델. `person`, `car` 등 COCO 클래스만 직접 탐지합니다.
- `safety_custom`: `person, helmet, vest, fire, smoke, fall` 순서로 학습된 커스텀 안전 모델.

안전모/조끼/화재/연기/쓰러짐을 YOLO에서 직접 감지하려면 커스텀 안전 데이터셋으로 학습한 모델과 동일한 `classes` 순서를 설정해야 합니다. 기본 COCO 모델에서는 safety gear violation을 자동 생성하지 않아 false alarm을 방지합니다.

## 프로젝트 구조

- `src/capture/`: RTSP 스트림 캡처
- `src/detection/`: YOLO26/ONNX 객체 탐지
- `src/pipeline/`: 캡처 → 탐지 → 이벤트 파이프라인
- `src/alerts/`: 알람/로깅/캡처 저장
- `src/dashboard/`: Streamlit 대시보드
- `tests/`: 유닛 테스트

## 개발 환경

- Python 3.10+
- NVIDIA GPU + CUDA 환경 권장
- DGX Spark 실증 기준
- 개발 보조 모델: Qwen 3.6 27B Dense 권장

## 다음 단계

1. 샘플 영상/RTSP 카메라로 End-to-End 테스트
2. 안전모/조끼/화재/연기/쓰러짐 커스텀 YOLO26 학습 또는 VLM 판정 정책 확정
3. DGX Spark 실장 환경에서 ONNX Runtime GPU/CPU 경로 검증
4. 납품 전 보안·개인정보 최종 리뷰

상세한 시스템 완성 계획은 다음 문서를 참고하세요.

- `docs/project-definition.md`: 제품 정의와 목표 사용자/가치
- `docs/architecture.md`: 다중 모델 + 이벤트 정책 목표 아키텍처
- `docs/model-management.md`: 기본/사용자 업로드 모델 관리 전략
- `docs/event-policy-engine.md`: 이벤트 정책 엔진 설계
- `docs/custom-model-upload.md`: 사용자 ONNX 모델 업로드 보안 설계
- `docs/development-roadmap.md`: 단계별 개발 로드맵
- `docs/adr/0001-multi-model-policy-architecture.md`: 아키텍처 결정 기록
- `docs/completion-plan.md`: 납품 가능한 시스템 완성까지의 전체 작업 계획
- `docs/e2e-validation-checklist.md`: 다음 즉시 실행할 End-to-End 검증 체크리스트

## License

MIT License
