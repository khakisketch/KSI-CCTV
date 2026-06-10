# KSI-CCTV 배포 가이드

이 문서는 KSI-CCTV를 DGX Spark 또는 일반 Linux 서버에 배포하는 방법을 설명합니다.

## 환경 요구사항

- Python 3.10+
- CUDA 지원 GPU (DGX Spark 권장)
- Ubuntu 22.04 LTS
- 8GB+ RAM, 20GB+ 디스크 여유 공간

## 빠른 시작

### 1. 설치

```bash
# 저장소 클론
git clone https://github.com/your-org/ksi-cctv.git
cd ksi-cctv

# 설치 스크립트 실행 (가상환경, 의존성, 디렉토리 생성)
bash scripts/install.sh
```

### 2. 환경 설정

```bash
# .env 파일 생성
cp .env.example .env

# 필수 항목 수정
nano .env
```

필수 환경 변수:
- `KSI_CCTV_DASHBOARD_PASSWORD` — 대시보드 로그인 비밀번호 (기본값 `change-me`는 보안 취약)
- `CCTV_CAM_001_URL` — 카메라 RTSP URL 또는 로컬 비디오 파일 경로

### 3. 모델 준비

```bash
# 기본 YOLO 모델 다운로드 (없는 경우)
python scripts/prepare_yolo26.py --model yolo26n.pt

# 사전학습 안전 모델 다운로드 (선택)
python scripts/fetch_safety_models.py --all
```

### 4. 상태 확인

```bash
python scripts/healthcheck.py --json
```

### 5. 실행

```bash
# 대시보드 직접 실행
bash scripts/run_dashboard.sh

# 또는 systemd 서비스로 등록
sudo install -D -m 0644 systemd/ksi-cctv.service /etc/systemd/system/
sudo systemctl enable --now ksi-cctv
```

## 상세 배포 절차

### 디렉토리 구조

```
/opt/ksi-cctv/
├── src/              # 소스 코드
├── config/           # 설정 파일
├── models/           # ONNX 모델
├── data/             # 데이터/캡처 이미지
├── logs/             # 로그 파일
├── scripts/          # 유틸리티 스크립트
├── systemd/          # systemd 템플릿
└── .venv/            # Python 가상환경
```

### systemd 서비스 설정

`systemd/ksi-cctv.service`를 참고하여 site-specific 값을 조정하세요:

- `User`, `Group`: 서비스 실행 계정
- `WorkingDirectory`: 설치 경로
- `EnvironmentFile`: 환경 변수 파일 경로

### 보안 설정

1. **비밀번호 변경**: `KSI_CCTV_DASHBOARD_PASSWORD`를 `change-me`에서 변경
2. **env 파일 권한**: `/etc/ksi-cctv/ksi-cctv.env`를 0600으로 설정
3. **RTSP URL 보호**: RTSP URL에 사용자/비밀번호가 포함된 경우 env 파일로 관리

### GPU 설정 (DGX Spark)

```bash
# CUDA provider 확인
python -c "import onnxruntime as ort; print(ort.get_available_providers())"

# CPU fallback 강제 (GPU 문제 시)
# config/default.yaml에서 device.primary를 "cpu"로 변경
```

## 문제 해결

### 자주 발생하는 문제

| 증상 | 원인 | 해결책 |
|---|---|---|
| 모델 로드 실패 | ONNX 파일 부재 | `scripts/fetch_safety_models.py` 실행 또는 모델 파일 확인 |
| 카메라 연결 실패 | RTSP URL 오류 | `.env`의 URL 확인, 네트워크 연결 확인 |
| 대시보드 접속 불가 | 포트 충돌 | `config/default.yaml`의 dashboard_port 변경 |
| VLM 분석 실패 | Ollama 미실행 | `ollama run qwen3-vl:latest`로 모델 다운로드 및 실행 |

### 로그 확인

```bash
# 대시보드 로그
tail -f logs/dashboard.log

# 알람 로그
tail -f logs/alerts.log

# systemd 로그
sudo journalctl -u ksi-cctv -f
```

### 롤백

```bash
# 서비스 중지
sudo systemctl stop ksi-cctv

# 서비스 제거
sudo systemctl disable ksi-cctv
sudo rm /etc/systemd/system/ksi-cctv.service
sudo systemctl daemon-reload
```

## 참고

- [시스템 아키텍처](architecture.md)
- [이벤트 정책 엔진](event-policy-engine.md)
- [모델 관리](model-management.md)
- [커스텀 모델 학습](custom-model-training.md)
