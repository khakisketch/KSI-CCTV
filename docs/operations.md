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

서비스 템플릿: `systemd/ksi-cctv.service`.  
배포 전 `User`, `Group`, `WorkingDirectory`를 현장 환경에 맞게 수정해야 합니다.

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

## 백업

운영 환경에서 정기적으로 백업할 항목:

```bash
# 설정 파일 (비밀 제외)
config/default.yaml
config/model_profiles.yaml
config/event_policies.yaml
config/camera_profiles.yaml
config/alert_preferences.yaml

# 모델 파일
models/*.onnx
models/uploaded/

# 로그 (장기 보관 필요 시)
logs/alerts.log
logs/audit.log
```

`.env`, `/etc/ksi-cctv/ksi-cctv.env`는 별도 비밀 관리 시스템으로 백업합니다.

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

## 운영 보안 체크리스트

- [ ] `KSI_CCTV_DASHBOARD_PASSWORD`를 강력한 비밀번호로 설정함
- [ ] `.env` 또는 `/etc/ksi-cctv/ksi-cctv.env`의 파일 권한이 소유자 읽기 전용(`600`)임
- [ ] RTSP URL, SMTP 비밀번호가 저장소에 커밋되지 않음
- [ ] 대시보드가 공개 네트워크에 직접 노출되지 않음 (리버스 프록시 또는 VPN 경유 권장)
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
