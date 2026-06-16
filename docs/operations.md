# KSI-CCTV 운영 가이드

이 문서는 KSI-CCTV의 24시간 운영, 모니터링, 로그 관리, 재시작, 백업에 관한 절차를 설명합니다.

---

## 시작 및 종료

### 대시보드 직접 실행

```bash
bash scripts/run_dashboard.sh
# http://localhost:8080 에서 Streamlit 대시보드 접속
```

### systemd 서비스

```bash
# 서비스 시작
sudo systemctl start ksi-cctv

# 서비스 자동 시작 등록
sudo systemctl enable ksi-cctv

# 서비스 중지
sudo systemctl stop ksi-cctv

# 서비스 재시작
sudo systemctl restart ksi-cctv

# 상태 확인
sudo systemctl status ksi-cctv
```

서비스 템플릿: `systemd/ksi-cctv.service`. `scripts/install.sh`가 설치 경로/계정을 자동 치환하며,
ExecStart는 **무인 탐지 데몬 `src/run.py`**(대시보드 버튼 없이 파이프라인 자동 기동)다.
설치 경로가 홈 디렉터리(`/home/*`) 하위이면 install.sh가 `ProtectHome=read-only`로 조정한다
(`ProtectHome=true`면 /home 마스킹으로 ExecStart를 못 찾아 crash-loop). 운영은 `/opt/ksi-cctv` 권장.

### Docker 운영 주의

- 기본 `docker compose up`은 **무인 탐지 데몬(ksi-cctv)** 만 실행한다(24h 탐지·알람·헬스).
  운영자 대시보드는 옵션: `docker compose --profile dashboard up`.
- **탐지 데몬과 대시보드의 파이프라인은 분리돼 있다.** 데몬이 탐지를 책임지며, 대시보드에서 "▶️ 시작"을
  누르면 같은 카메라로 **중복 추론**이 발생할 수 있다 — 대시보드는 모니터링/설정 용도로 쓰고, 데몬이 도는
  현장에서는 동일 카메라로 대시보드 파이프라인을 별도 기동하지 않는다.
- **bind-mount 디렉터리 소유권:** 컨테이너는 비root(uid 1000)로 실행되므로, 호스트의 `./logs`,
  `./data`, `./models/uploaded`가 root 소유면 쓰기에 실패한다. 사전에
  `mkdir -p ./logs ./data/captures ./models/uploaded && sudo chown -R 1000:1000 ./logs ./data ./models/uploaded`
  로 권한을 맞추거나 named volume을 사용한다.

---

## 헬스체크

### CLI 헬스체크

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

`docker-compose` healthcheck도 `/health/live` 또는 `/health` 기준으로 동작합니다.

---

## 로그 관리

### 로그 파일 위치

| 파일 | 내용 |
|---|---|
| `logs/alerts.log` | 이벤트 로그 (JSONL 포맷). RotatingFileHandler (10MB × 5개) |
| `logs/audit.log` | 감사 로그 — 로그인/설정변경/이미지조회/모델업로드 |

### 로그 로테이션

`alerts.log`는 10MB에서 자동 로테이션되며 최대 5개(`alerts.log.1` ~ `.5`)를 보관합니다.  
별도 `logrotate` 설정이 필요 없습니다.

### 로그 확인

```bash
# 최신 이벤트 (대시보드 "이벤트 로그" 탭에서도 확인 가능)
tail -f logs/alerts.log

# 감사 로그
tail -f logs/audit.log

# systemd 저널 (서비스 실행 중)
sudo journalctl -u ksi-cctv -f
```

---

## 캡처 이미지 관리

### 저장 위치

캡처 이미지는 `data/captures/`에 저장됩니다.  
`encrypt_images: true`이면 `.jpg.enc` 암호화 파일로 저장됩니다.

### 자동 정리

`image_retention_days`(기본값 30일) 초과 파일은 자동 삭제됩니다.  
정리는 `handle_event()` 내부에서 **6시간 간격 throttle**로 실행됩니다.

### 수동 정리

```bash
# 30일 이상 된 캡처 이미지 삭제 (예시)
find data/captures/ -name "*.jpg" -mtime +30 -delete
find data/captures/ -name "*.jpg.enc" -mtime +30 -delete
```

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

대시보드 "모델 업로드" 탭에서 `.onnx` 파일을 업로드합니다.  
업로드된 모델은 `models/uploaded/{model_id}/model.onnx`에 격리 저장되며  
기본 `enabled: false` 상태로 등록됩니다. 관리자가 활성화해야 추론에 사용됩니다.

### 현재 탐지 모델 한계 (운영자 필독)

> **경고**: 기본 제공 모델은 아래 한계를 가집니다. 실제 현장에서 탐지 결과를 정상으로 오인하지 마십시오.

| 모델 | 탐지 가능 클래스 | 한계 |
|---|---|---|
| `general_yolo` (COCO pretrained) | person, car, truck 등 COCO 80종 | 안전모·조끼·화재·연기·쓰러짐 **직접 탐지 불가**. VLM(Ollama) 사용 시 보조 분석 가능. |
| `ppe_yolo` (공개 pre-trained) | Hardhat, NO-Hardhat → helmet/no_helmet | **조끼(vest) 미지원** — no_vest 이벤트 비활성 상태. 일반 현장에서 false positive 발생 가능. |
| `fire_smoke_yolo` (공개 pre-trained) | fire-smoke, fire, factory-smoke 등 5종 | 산불·특수 연기 모델로 일반 공장·실내에서 false positive 위험. smoke 이벤트는 추가 VLM 확인 권장. |
| `pose_fall_model` | person, fall | 현재 `disabled` 상태. 실제 쓰러짐 탐지에는 커스텀 학습 모델 필요. |

**신뢰성 있는 안전 이벤트 탐지**(안전모/조끼/화재/연기/쓰러짐)를 위해서는  
커스텀 safety 모델 학습(Phase D, `scripts/train_*.py`)이 필요합니다.  
학습 방법: `docs/custom-model-training.md` 참조.

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

### 대시보드 시스템 상태 탭

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
| 감사 로그 (`logs/audit.log`) | 무제한 (수동 관리) | 수동 삭제 또는 logrotate 구성 |

`image_retention_days` 값은 `config/default.yaml`의 `alerts.image_retention_days`로 조정합니다.

---

## 백업 / 복구

설정·업로드모델 백업과 복구는 `scripts/backup.sh`로 자동화한다(시크릿·PII는 자동 제외).

```bash
scripts/backup.sh                  # backups/ksi-cctv-config-<ts>.tar.gz 생성(perms 600)
scripts/backup.sh backup --with-logs   # 로그도 포함(PII 주의)
scripts/backup.sh list             # 백업 목록
scripts/backup.sh restore <tarball>    # 복구(확인 후, 복구 전 현재상태 자동백업) + 환경 재검증
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

대시보드 "시스템 상태" 탭의 "SMTP 테스트" 버튼으로 연결을 검증할 수 있습니다.  
알람 임계값(`email_alert_level_threshold`)은 대시보드 "알람 설정" 탭에서 조정합니다.

---

## 네트워크 보안 (HTTPS/TLS)

> **경고**: `scripts/run_dashboard.sh`는 기본값으로 평문 HTTP, `0.0.0.0:8080`에 바인딩합니다.  
> LAN에서 비밀번호·캡처 이미지 등 PII가 평문으로 노출됩니다. **운영 환경에서는 반드시 아래 조치를 취하십시오.**

### 권장 구성

1. **대시보드 바인딩을 루프백으로 변경** — `scripts/run_dashboard.sh`의 `--server.address=0.0.0.0`을 `--server.address=127.0.0.1`로 교체하고, 리버스 프록시(아래)를 통해서만 접근.
2. **리버스 프록시 + TLS 종단** — nginx 또는 Caddy로 443 포트 TLS 종단 후 `localhost:8080`으로 프록시.
3. **방화벽으로 8080/8081 포트 외부 차단** — 프록시 포트(443)만 외부에 열고 Streamlit/헬스체크 포트는 LAN 노출 금지.

### nginx 예시 (간략)

```nginx
server {
    listen 443 ssl;
    server_name cctv.example.internal;

    ssl_certificate     /etc/ssl/certs/ksi-cctv.crt;
    ssl_certificate_key /etc/ssl/private/ksi-cctv.key;

    location / {
        proxy_pass http://127.0.0.1:8080;
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

### Caddy 예시 (간략)

```
cctv.example.internal {
    reverse_proxy 127.0.0.1:8080
}
```

> Caddy는 Let's Encrypt/ACME를 통해 TLS 인증서를 자동으로 취득·갱신합니다.  
> 내부망에서는 자체 CA 또는 사설 인증서를 사용하십시오.

---

## 운영 보안 체크리스트

- [ ] `KSI_CCTV_DASHBOARD_PASSWORD`를 강력한 비밀번호로 설정함
- [ ] `.env` 또는 `/etc/ksi-cctv/ksi-cctv.env`의 파일 권한이 소유자 읽기 전용(`600`)임
- [ ] RTSP URL, SMTP 비밀번호가 저장소에 커밋되지 않음
- [ ] 대시보드가 공개 네트워크에 직접 노출되지 않음 (리버스 프록시 + TLS 또는 VPN 경유)
- [ ] `scripts/run_dashboard.sh`의 바인딩 주소가 `127.0.0.1`로 설정됨 (운영 시)
- [ ] 방화벽에서 8080/8081 포트를 외부 네트워크에서 차단함
- [ ] `encrypt_images: true` 활성화 시 `KSI_CCTV_CAPTURE_KEY` 백업 완료
- [ ] 감사 로그(`logs/audit.log`) 정기 검토

---

## 재설치 / 업데이트

```bash
# 의존성 재설치 (코드 업데이트 후)
bash scripts/install.sh

# 헬스체크로 정상 여부 확인
./.venv/bin/python scripts/healthcheck.py --json

# 서비스 재시작 (systemd 사용 시)
sudo systemctl restart ksi-cctv
```
