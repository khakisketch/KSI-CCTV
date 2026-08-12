# KSI-CCTV 배포 가이드

이 문서는 KSI-CCTV의 **운영 배포**와 개발/랩 도구의 경계를 설명합니다. 운영 배포는 서명된
오프라인 **Ubuntu ARM64 Appliance**(DGX Spark `dgx-spark-arm64` profile)만 지원합니다. Jetson은 향후 별도 qualification 전까지 운영 범위가 아닙니다.

## 운영 배포: Signed Offline Appliance

이 절의 명령은 최초 provisioning을 담당하는 service engineer용입니다. Appliance가 기동된 뒤 현장 설치업자는
HTTPS 웹 콘솔에서 현장 설정, 카메라 등록, commissioning, 진단 자료 생성, 인수 확인을 수행합니다.

운영 납품에는 저장소 clone, `.venv`, `scripts/install.sh`, `scripts/run_web.sh`, 일반 Docker Compose,
평문 HTTP를 사용하지 않습니다. `docs/offline-appliance.md`의 서명 검증 release 절차가 유일한 운영 경로입니다.

1. ARM64 대상 장비에서 `python3 installer/ksi_cctv_appliance.py doctor --profile ...`를 실행합니다.
2. 별도 보관한 distinct release-signing/CI-attestation Ed25519 public keys, external backup recipient RSA public key, 0600 site env를 준비합니다. site env에는 강력하고 서로 다른
   `KSI_CCTV_API_KEY`, `KSI_CCTV_WEB_PASSWORD`, `KSI_CCTV_WEB_SECRET`, `KSI_CCTV_CAPTURE_KEY`, `KSI_CCTV_SITE_ADDRESS`가 필요합니다.
3. signed release media로 `installer/ksi_cctv_appliance.py install ... --confirm`을 실행합니다. 설치기는 서명된 release unit을 `/etc/systemd/system/`에 복사하고 `daemon-reload`, `enable` 후 기동합니다.
4. activation health, Caddy site CA 배포, HTTPS 로그인, 카메라 first-frame readiness를 확인합니다.

Appliance는 Caddy 80/443만 LAN-facing으로 두고 웹, 엔진, MediaMTX control/HLS는 Compose 내부에 유지합니다. 첫 릴리스는 HLS만 지원하며 WebRTC는 별도 검증 전까지 비활성입니다.
자세한 명령, upgrade, rollback, backup은 [Offline Appliance](offline-appliance.md)를 따릅니다.

## Development/Lab Only

이후 절차는 개발 또는 격리된 랩에서만 사용합니다. 운영 배포 방법이 아닙니다.

### 환경 요구사항

- Python 3.10+와 Linux 개발 host
- CUDA GPU는 개발 성능 검증에 선택 사항이며 운영 지원 범위 증명이 아님
- 운영 지원 범위는 Ubuntu ARM64 Appliance이며, 이 절차는 지원되는 운영 설치 방법이 아님
- 8GB+ RAM, 20GB+ 디스크 여유 공간

## 개발/랩 빠른 시작

### 1. 설치

```bash
# 이미 확보한 개발용 저장소 checkout에서 실행
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
- `KSI_CCTV_WEB_PASSWORD` — 웹 콘솔 로그인 비밀번호
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
# 웹 콘솔 직접 실행 (개발/랩, loopback 기본값)
bash scripts/run_web.sh

# 또는 Docker로 한 번에 기동 (개발/랩)
docker compose --profile web up --build

# 또는 개발/랩 host에서만 source-tree systemd 서비스로 등록 (운영 Appliance 금지)
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

1. **비밀번호 설정**: `KSI_CCTV_WEB_PASSWORD`에 강력한 값을 설정
2. **env 파일 권한**: `/etc/ksi-cctv/{ksi-cctv,engine,web}.env`를 역할별로 분리하고 0600으로 설정
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
| 웹 콘솔 접속 불가 | 포트 충돌 | `KSI_CCTV_WEB_PORT` 또는 실행 중인 개발 프로세스 확인 |
| VLM 분석 실패 | Ollama 미실행 | `ollama run qwen3-vl:latest`로 모델 다운로드 및 실행 |

### 로그 확인

```bash
# 웹 콘솔 로그 (로컬 npm dev)
tail -f logs/web.log

# 알람 로그
tail -f logs/alerts.log

# systemd 로그 (Appliance)
sudo journalctl -u ksi-cctv-compose -f
```

### 제거 (서명 rollback 아님)

다음은 Appliance를 제거하는 service-engineer 절차이며 release/data rollback이 아닙니다. 서명된 code-only rollback과 encrypted restore는 [Offline Appliance](offline-appliance.md)를 따릅니다.

```bash
# Appliance 서비스 중지
sudo systemctl stop ksi-cctv-compose

# Appliance 서비스 제거
sudo systemctl disable ksi-cctv-compose
sudo rm /etc/systemd/system/ksi-cctv-compose.service
sudo systemctl daemon-reload
```

## 참고

- [시스템 아키텍처](architecture.md)
- [이벤트 정책 엔진](event-policy-engine.md)
- [모델 관리](model-management.md)
- [커스텀 모델 학습](custom-model-training.md)
