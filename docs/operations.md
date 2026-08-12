# KSI-CCTV 운영 가이드

이 문서는 서명된 Offline Appliance의 24시간 운영, 모니터링, 로그 관리, 재시작, 백업 절차를 설명합니다.
**운영 경로는 Appliance뿐입니다.** `scripts/install.sh`, `scripts/run_web.sh`, 저장소 Compose는 개발/랩 도구이며
운영 시스템에서 사용하지 않습니다. 설치, upgrade, rollback은 [Offline Appliance](offline-appliance.md)를 따릅니다.

역할을 혼동하지 않습니다.

- **현장 설치업자:** HTTPS 웹 콘솔의 `현장 설정`, `카메라`, `설치 진행`, `진단`, `인수인계`만 사용합니다.
  진단 자료는 `진단 > 지원 자료 내려받기`에서 생성하며 shell 접근이 필요하지 않습니다.
- **권한 있는 Appliance service engineer:** signed release 설치·upgrade·rollback·host backup/restore와 서비스 복구를
  수행합니다. 아래 CLI 절은 이 역할 전용이며 현장 설치업자 절차가 아닙니다.

---

## 시작 및 종료

### 개발/랩 웹 콘솔 직접 실행

```bash
# 개발/랩 전용: 엔진(:8181) + Next.js 웹 콘솔(:3100) 한 번에 실행
bash scripts/run_web.sh
# http://localhost:3100 에서 웹 콘솔 접속
```

### Appliance 서비스 (service engineer 전용)

```bash
# 서비스 시작
python3 installer/ksi_cctv_appliance.py start --confirm

# 서비스 자동 시작 등록
python3 installer/ksi_cctv_appliance.py status

# 서비스 중지
python3 installer/ksi_cctv_appliance.py stop --confirm

# 서비스 재시작
python3 installer/ksi_cctv_appliance.py restart --confirm

# 상태 확인
python3 installer/ksi_cctv_appliance.py status
```

Appliance systemd unit은 signed release의 Compose stack을 관리하며, 부팅과 활성화 후 signed health check를 실행합니다.
일반 `systemd/ksi-cctv.service`와 `scripts/install.sh`는 개발/랩 전용입니다.

### 개발/랩 Docker 주의

- 기본 `docker compose up`은 **무인 탐지 데몬(ksi-cctv)과 내부 MediaMTX 게이트웨이**를 실행한다(개발/랩 검증용).
  운영자 웹 콘솔은 옵션: `docker compose --profile web up`.
- **탐지 데몬과 웹 콘솔의 파이프라인은 통합돼 있다.** `src/run.py`는 탐지 데몬 스레드 위에 FastAPI 엔진(포트 8081)을
  동일 프로세스로 mount하며, 웹 콘솔의 "▶️ 시작"은 데몬 파이프라인을 제어한다. 중복 추론 없이
  단일 파이프라인을 웹에서 조회·제어한다.
- **bind-mount 디렉터리 소유권:** 컨테이너는 비root(uid 1000)로 실행되므로, 호스트의 `./logs`,
  `./data`, `./models/uploaded`가 root 소유면 쓰기에 실패한다. 사전에
  `mkdir -p ./logs ./data/captures ./models/uploaded && sudo chown -R 1000:1000 ./logs ./data ./models/uploaded`
  로 권한을 맞추거나 named volume을 사용한다.

---

## 헬스체크

### CLI 헬스체크 (개발/서비스 엔지니어 전용)

```bash
# 기본 헬스체크 (JSON 출력)
./.venv/bin/python scripts/healthcheck.py --json

# GPU provider 요구
./.venv/bin/python scripts/healthcheck.py --require-gpu --json

# VLM(Ollama) 연결 확인 포함
./.venv/bin/python scripts/healthcheck.py --check-vlm --json

# 카메라 URL 해석 확인 포함
./.venv/bin/python scripts/healthcheck.py --check-cameras --json

# 런타임 디렉터리 생성/복구 (최초 설치 또는 복구 시)
./.venv/bin/python scripts/healthcheck.py --create-dirs
```

### HTTP 헬스 서버

```bash
# 헬스 서버 시작 (포트 8081)
./.venv/bin/python -m src.utils.health --serve
```

| 엔드포인트 | 설명 |
|---|---|
| `GET /health` | 전체 헬스 리포트 (JSON). 비정상 시 HTTP 503 |
| `GET /healthz` | `/health`와 동일 |
| `GET /health/live` | Liveness (항상 200) |
| `GET /health/ready` | Readiness — disk/memory 임계값 통과 시 200 |

개발/랩 Compose는 기본 CPU 경로입니다. NVIDIA GPU를 사용하는 경우에만
`docker compose -f docker-compose.yml -f docker-compose.gpu.yml ...` overlay를 추가합니다.
이 overlay는 `ENABLE_GPU=1`, `ONNXRUNTIME_GPU_WHEEL_URL`,
`ONNXRUNTIME_GPU_WHEEL_SHA256` build argument와 NVIDIA device
reservation을 함께 적용합니다. Wheel URL은 credential/query/fragment가 없는 HTTPS URL이어야
하며 redirect도 HTTPS로 제한됩니다. URL에 credential을 넣지 마십시오.
Appliance 운영 Compose는 별도 signed image bundle로 GPU를 필수 요구합니다.

---

## 로그 관리

### 로그 파일 위치

| 파일 | 내용 |
|---|---|
| `logs/alerts.log` | 이벤트 로그 (JSONL 포맷). RotatingFileHandler (10MB × 5개) |
| `logs/dashboard_audit.jsonl` | 감사 로그 — 로그인/설정변경/이미지조회/모델업로드 (Ed25519 서명) |

### 로그 로테이션

`alerts.log`는 10MB에서 자동 로테이션되며 최대 5개(`alerts.log.1` ~ `.5`)를 보관합니다.  
별도 `logrotate` 설정이 필요 없습니다.

### 로그 확인

```bash
# 최신 이벤트 (웹 콘솔 "이벤트" 페이지에서도 확인 가능)
tail -f logs/alerts.log

# 감사 로그
tail -f logs/dashboard_audit.jsonl

# systemd 저널 (Appliance 서비스 실행 중)
sudo journalctl -u ksi-cctv-compose -f
```

---

## 캡처 이미지 관리

### 저장 위치

캡처 이미지는 `data/captures/`에 저장됩니다.  
`encrypt_images: true`이면 `.jpg.enc` 암호화 파일로 저장됩니다.
기본 설치는 암호화를 사용하며 `KSI_CCTV_CAPTURE_KEY`가 없으면 데몬이 시작하지 않습니다.

### 자동 정리

`image_retention_days`(기본값 30일) 초과 파일은 자동 삭제됩니다.  
정리는 `handle_event()` 내부에서 **6시간 간격 throttle**로 실행됩니다.

### 수동 정리

```bash
# 30일 이상 된 캡처 이미지 삭제 (예시)
find data/captures/ -name "*.jpg" -mtime +30 -delete
find data/captures/ -name "*.jpg.enc" -mtime +30 -delete
```

### MediaMTX 진단 포트

기본 Compose 스택은 RTSP, HLS, MediaMTX API를 Docker 내부 네트워크에만 둡니다. 첫 Appliance 릴리스에서는 WebRTC를 비활성으로 유지합니다.
로컬 RTSP 진단이 꼭 필요한 경우에만 loopback 포트를 추가합니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.diagnostics.yml up -d ksi-cctv-gateway
```

이 overlay를 운영 배포에 사용하거나 `KSI_CCTV_GW_RTSP_BIND`를 LAN 주소로 설정하지 않습니다.

---

## 모델 관리

### 활성 모델 확인

```bash
./.venv/bin/python scripts/healthcheck.py --json
# "model_registry" 섹션에서 loaded_models, failed_models 확인
```

### 사전학습 안전 모델 다운로드

```bash
# 인터넷 연결 필요. huggingface_hub, ultralytics 패키지 필요
./.venv/bin/python scripts/fetch_safety_models.py --all
```

다운로드 후 `models/yolo26_ppe.onnx`, `models/yolo26_fire_smoke.onnx`가 생성됩니다.

### 사용자 ONNX 모델 업로드

웹 콘솔 "모델" 페이지에서 `.onnx` 파일을 업로드합니다.
업로드된 모델은 `models/uploaded/{model_id}/model.onnx`에 격리 저장되며  
기본 `enabled: false` 상태로 등록됩니다. 관리자가 활성화해야 추론에 사용됩니다.

### 현재 탐지 모델 한계 (운영자 필독)

> **경고**: 기본 제공 모델은 아래 한계를 가집니다. 실제 현장에서 탐지 결과를 정상으로 오인하지 마십시오.

| 모델 | 탐지 가능 클래스 | 한계 |
|---|---|---|
| `general_yolo` (COCO pretrained) | person, car, truck 등 COCO 80종 | 안전모·조끼·화재·연기·쓰러짐 **직접 탐지 불가**. VLM(Ollama) 사용 시 보조 분석 가능. |
| `ppe_yolo` (공개 pre-trained) | Hardhat/NO-Hardhat/Safety Vest/NO-Safety Vest → helmet/no_helmet/vest/no_vest | 일반 현장 정확도와 상용 라이선스는 아직 승인되지 않았다. |
| `fire_smoke_yolo` (공개 pre-trained) | Fire/Smoke → fire/smoke | 현장 오탐/미탐과 상용 라이선스는 아직 승인되지 않았다. 1차 Appliance는 VLM 없이 direct model 결과를 후보로 기록한다. |
| `pose_fall_model` | person, fall | 현재 `disabled` 상태. 실제 쓰러짐 탐지에는 커스텀 학습 모델 필요. |

PPE/fire/smoke pretrained model은 연결되어 있으나, 고객 현장 safety claim 전에는 승인된 field video로 precision/recall/false alarm을 측정하고 라이선스를 확인해야 합니다. fall은 전용 모델과 field validation 전까지 safety-grade capability가 아닙니다.

---

## 성능 모니터링

### 스트레스 테스트

```bash
# 30초 샘플 영상 기반 안정성 테스트
./.venv/bin/python scripts/stress_test.py \
    --duration 30 --policy-mode \
    --json-out reports/stress_$(date +%Y%m%d).json
```

### 파이프라인 프로파일링

```bash
# 모델별 레이턴시 + 메모리 측정
./.venv/bin/python scripts/profile_pipeline.py \
    --frames 10 --iterations 3 \
    --json-out reports/profile_$(date +%Y%m%d).json
```

### 웹 콘솔 시스템 상태 페이지

- CPU/메모리/디스크 사용률 (psutil 기반)
- ONNX Runtime provider 정보
- VLM 호출 메트릭 (총 호출/성공/실패/평균 레이턴시/최대 레이턴시)
- VLM 캐시 통계 (hits/misses/bypasses/hit_rate)
- SMTP 연결 테스트 버튼

---

## 데이터 보관 정책

| 데이터 | 기본 보관 기간 | 삭제 방식 |
|---|---|---|
| 캡처 이미지 (`data/captures/`) | 30일 (`image_retention_days`) | 자동 (6시간 throttle) |
| 이벤트 로그 (`logs/alerts.log`) | 50MB (10MB × 5개 로테이션) | RotatingFileHandler 자동 |
| 감사 로그 (`logs/dashboard_audit.jsonl`) | active 5MiB + 최대 8개 서명 chain segment | write lock 내 자동 segment rotation |

`image_retention_days` 값은 `config/default.yaml`의 `alerts.image_retention_days`로 조정합니다.
감사 segment가 제거된 뒤에도 retained oldest entry의 `previous_hash`를 anchor로 support bundle에 포함합니다.

---

## 백업 / 복구

운영 Appliance backup/restore는 external RSA recipient key로 암호화하는 `installer/ksi_cctv_appliance.py`만 사용합니다. 자세한 명령은 [offline-appliance.md](offline-appliance.md)를 따릅니다.

`scripts/backup.sh`는 개발/랩 설정 백업 전용이며 Appliance 운영 상태, SQLite event store, site secrets, encrypted captures를 복구하지 못합니다. 운영 절차나 customer recovery evidence에 사용하지 마십시오.

```bash
scripts/backup.sh                  # 개발/랩 config snapshot only
```

- **포함**: `config/`(default·model_profiles·event_policies·camera_profiles·alert_preferences·cameras.json)·`models/uploaded/`.
- **자동 제외(의도적)**: `.env`(시크릿)·`data/captures`(PII 이미지)·`cameras.local.json`. 기본 모델 `.onnx`는
  `scripts/fetch_safety_models.py`로 재현 가능하므로 백업 대상 아님(용량·AGPL).
- **시크릿**: `.env`, `KSI_CCTV_CAPTURE_KEY`(encrypt_images 사용 시)는 별도 비밀 관리 시스템으로 백업한다.

---

## SMTP 알람 설정

환경 변수로 SMTP를 설정하면 `medium` 이상 이벤트 발생 시 이메일이 발송됩니다.

```bash
# SMTP 환경 변수 예시
KSI_CCTV_SMTP_HOST=smtp.gmail.com
KSI_CCTV_SMTP_PORT=587
KSI_CCTV_SMTP_USER=alerts@example.com
KSI_CCTV_SMTP_PASSWORD=your-app-password
KSI_CCTV_SMTP_RECIPIENTS=admin@example.com,security@example.com
```

웹 콘솔 "설정" 페이지의 "SMTP 테스트" 버튼으로 연결을 검증할 수 있습니다.
알람 임계값(`email_alert_level_threshold`)은 웹 콘솔 "설정" 페이지에서 조정합니다.

---

## 네트워크 보안 (HTTPS/TLS)

Appliance는 Caddy HTTPS endpoint만 LAN/VPN에 공개하고, 웹, 엔진 API, MediaMTX HLS/control은 Compose 내부에 둡니다. 첫 릴리스는 HLS만 지원합니다.
`scripts/run_web.sh`는 개발/랩 전용 평문 HTTP 도구로, 기본값은 loopback입니다. 비루프백 웹 바인드는
`KSI_CCTV_INSECURE_REMOTE_DEV=1`의 명시적 acknowledgement 없이는 거부되며, 엔진과 MediaMTX host ports는 loopback-only입니다.

### 권장 구성

1. **Appliance Caddy 사용** — Caddy site CA를 운영자 기기에 배포하고 `https://<KSI_CCTV_SITE_ADDRESS>`로만 접속합니다.
2. **내부 서비스 유지** — 엔진 API와 MediaMTX를 브라우저나 LAN에 직접 프록시하지 않습니다.
3. **개발/랩 격리** — run_web의 비루프백 HTTP는 운영자 계정, 실제 캡처, 공유 네트워크에 사용하지 않습니다.

### 개발/랩 TLS 프록시 예시 (운영 사용 금지)

이 예시는 Appliance를 대체하지 않는 격리된 개발/랩 환경용입니다. 운영은 Appliance에 포함된 Caddy만 사용합니다.

```nginx
server {
    listen 443 ssl;
    server_name cctv.example.internal;

    ssl_certificate     /etc/ssl/certs/ksi-cctv.crt;
    ssl_certificate_key /etc/ssl/private/ksi-cctv.key;

    location / {
        proxy_pass http://127.0.0.1:3100;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }

}
server {
    listen 80;
    server_name cctv.example.internal;
    return 301 https://$host$request_uri;
}
```

### 개발/랩 Caddy 예시 (운영 사용 금지)

```
cctv.example.internal {
    reverse_proxy 127.0.0.1:3100
}
```

> Caddy는 Let's Encrypt/ACME를 통해 TLS 인증서를 자동으로 취득·갱신합니다.  
> 내부망에서는 자체 CA 또는 사설 인증서를 사용하십시오.

---

## 운영 보안 체크리스트

- [ ] `KSI_CCTV_WEB_PASSWORD`를 강력한 비밀번호로 설정함
- [ ] `/etc/ksi-cctv/{ksi-cctv,engine,web}.env`가 역할별 최소 변수만 포함하고 권한이 `600`임
- [ ] RTSP URL, SMTP 비밀번호가 저장소에 커밋되지 않음
- [ ] 웹 콘솔이 공개 네트워크에 직접 노출되지 않음 (리버스 프록시 + TLS 또는 VPN 경유)
- [ ] Appliance Caddy HTTPS endpoint만 운영자 VLAN/VPN에 공개됨
- [ ] 엔진 API와 MediaMTX host port가 LAN/인터넷에 직접 공개되지 않음
- [ ] `encrypt_images: true` 활성화 시 `KSI_CCTV_CAPTURE_KEY` 백업 완료
- [ ] 감사 로그(`logs/dashboard_audit.jsonl`) 정기 검토
- [ ] `bash scripts/verify_release_candidate.sh --strict` internal runner 로그와 Playwright report 보관
- [ ] signed bundle은 `bash scripts/verify_appliance_bundle.sh ...` preflight 후 ARM64 staging Appliance에서 검증

---

## 재설치 / 업데이트

```bash
# signed release로 업그레이드 (상세 절차는 offline-appliance.md)
python3 installer/ksi_cctv_appliance.py upgrade \
  --release-dir /media/usb/release \
  --profile dgx-spark-arm64 \
  --public-key /etc/ksi-cctv/trust-roots/release-signing.pub \
  --attestation-public-key /etc/ksi-cctv/trust-roots/ci-attestation.pub \
  --confirm
```
