# KSI-CCTV 설정 레퍼런스

이 문서는 KSI-CCTV의 모든 설정 파일과 환경 변수를 설명합니다.

---

## 환경 변수

환경 변수는 `.env` 파일 또는 systemd `EnvironmentFile`(`/etc/ksi-cctv/ksi-cctv.env`)로 주입합니다.  
`.env`는 저장소에 커밋하지 않습니다. 템플릿: `.env.example`.

### 필수 변수

| 변수 | 예시 | 설명 |
|---|---|---|
| `KSI_CCTV_DASHBOARD_PASSWORD` | `change-me` | 대시보드 로그인 비밀번호. 기본값 `change-me`는 반드시 교체 |
| `CCTV_CAM_001_URL` | `rtsp://user:pw@192.168.1.101:554/stream1` | 카메라 1번 RTSP URL 또는 로컬 파일 경로 |

추가 카메라는 `CCTV_CAM_002_URL`, `CCTV_CAM_003_URL` … 순서로 추가합니다.  
`cameras.json`의 `camera_id` 번호와 맞춰야 합니다.

### 선택 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `KSI_CCTV_DASHBOARD_SALT` | (없음) | 비밀번호 해시 salt. 미설정 시 내부 기본값 사용 |
| `KSI_CCTV_CAPTURE_KEY` | (없음) | 캡처 이미지 암호화용 Fernet 키. `encrypt_images: true`일 때 필수 |
| `KSI_CCTV_SMTP_HOST` | (없음) | SMTP 서버 호스트 |
| `KSI_CCTV_SMTP_PORT` | `587` | SMTP 포트 |
| `KSI_CCTV_SMTP_USER` | (없음) | SMTP 인증 사용자 |
| `KSI_CCTV_SMTP_PASSWORD` | (없음) | SMTP 인증 비밀번호 |
| `KSI_CCTV_SMTP_RECIPIENTS` | (없음) | 알람 수신 이메일 목록 (쉼표 구분) |

> **보안 주의**: RTSP URL, SMTP 비밀번호, Fernet 키는 절대 저장소에 커밋하지 않습니다.

---

## 설정 파일 목록

| 파일 | 용도 |
|---|---|
| `config/default.yaml` | 앱 전역 기본값 (포트·VLM·탐지·알람·RTSP) |
| `config/cameras.json` | 카메라 채널 목록 및 활성화 여부 |
| `config/model_profiles.yaml` | 모델 등록·경로·클래스·임계값 |
| `config/event_policies.yaml` | 이벤트별 trigger/rule/VLM/alert 정책 |
| `config/camera_profiles.yaml` | 카메라별 도메인·활성 이벤트·zone 설정 |
| `config/alert_preferences.yaml` | 이메일 알람 임계값·이벤트별 on/off |
| `config/prompts/*.yaml` | VLM 도메인 프롬프트 (general/construction/school/factory/warehouse/parking) |

---

## config/default.yaml 주요 항목

### app

```yaml
app:
  name: "KSI-CCTV"
  debug: true          # 운영 환경에서는 false 권장
  log_level: "DEBUG"   # 운영 환경에서는 "INFO" 권장
  log_dir: "./logs"
  timezone: "Asia/Seoul"
```

### ui

```yaml
ui:
  dashboard_port: 8080
  dashboard_title: "KSI-CCTV 안전 관리 대시보드"
  refresh_interval_ms: 1000
  max_display_alerts: 50
```

### cameras

```yaml
cameras:
  default_fps: 30
  max_channels: 16
  reconnect_interval_sec: 5
  timeout_sec: 10
```

### models (레거시 단일 YOLO 경로용)

```yaml
models:
  yolo:
    model_path: "./models/yolo26.onnx"
    input_size: [640, 640]
    conf_threshold: 0.25
    iou_threshold: 0.45
    postprocess_mode: "auto"   # auto, nms, nms_free
    class_profile: "coco"      # coco 또는 safety_custom
  vlm:
    provider: "ollama"
    model_name: "qwen3-vl:latest"
    base_url: "http://localhost:11434"
    enabled: true
    timeout_sec: 60
    max_retries: 2
    prompt_template: "./config/prompts/construction.yaml"
```

> 운영 기본 실행 경로는 policy-mode(`config/model_profiles.yaml`)입니다.  
> 위 `models.yolo` 섹션은 레거시 fallback 전용입니다.

### detection

```yaml
detection:
  skip_frames: 5           # 추론 간격 (1=모든 프레임)
  alert_cooldown_sec: 30
  target_classes: "person,fire,smoke,fall"
  vlm_trigger_classes: "person"
  vlm_cooldown_sec: 60
```

### alerts

```yaml
alerts:
  enabled: true
  log_file: "./logs/alerts.log"
  console_output: true
  save_images: true
  image_dir: "./data/captures"
  image_retention_days: 30          # 캡처 이미지 보관 기간
  blur_faces: true                   # 저장 전 얼굴 블러링
  face_blur_strength: 35
  encrypt_images: false              # true이면 KSI_CCTV_CAPTURE_KEY 필수
  log_max_bytes: 10485760           # 로그 파일 최대 크기 (10MB)
  log_backup_count: 5               # 로테이션 보관 개수
```

---

## config/model_profiles.yaml

모든 모델은 이 파일에 등록합니다. `MultiModelRegistry`가 `enabled: true`인 모델만 로드합니다.

```yaml
general_yolo:
  enabled: true
  type: yolo_onnx
  source: built_in          # built_in | pre_trained | uploaded | custom_trained
  model_path: "./models/yolo26.onnx"
  class_profile: coco       # coco | safety_custom | custom
  classes: null             # null이면 ONNX 메타데이터에서 자동 인식
  input_size: [640, 640]
  conf_threshold: 0.25
  iou_threshold: 0.45
  postprocess_mode: auto
  device: cpu               # cpu | cuda
  fp16: false
```

> **ONNX 메타데이터 자동 인식**: `classes: null`이면 ONNX 메타데이터의 `names` 필드를 읽습니다.  
> config `classes`와 실제 모델 출력 클래스 수가 다르면 메타데이터를 우선 사용하며 경고를 출력합니다.

사용자 업로드 모델은 `models/uploaded/{model_id}/model.onnx`에 격리 저장되고  
프로파일이 `enabled: false`로 자동 등록됩니다. 대시보드 "모델 업로드" 탭에서 활성화합니다.

---

## config/event_policies.yaml

이벤트별 탐지 정책을 정의합니다.

```yaml
fire:
  enabled: true
  trigger:
    models: [fire_smoke_yolo, general_yolo]
    classes: [fire]
    min_confidence: 0.3
  rule:
    type: direct_detection
  vlm:
    policy: always        # never | always | on_ambiguous | on_high_confidence | on_low_confidence
  alert:
    level: critical
    cooldown_sec: 10
```

지원 rule 유형:

| 유형 | 설명 |
|---|---|
| `direct_detection` | 지정 클래스가 탐지되면 이벤트 생성 |
| `safety_gear_overlap` | person과 helmet/vest bbox 위치 관계로 착용 여부 판단 |
| `zone_intrusion` | person 중심점이 제한구역 polygon 안에 들어오면 이벤트 생성 |
| `count_threshold` | 특정 클래스 수가 기준 이상이면 이벤트 생성 |
| `proximity` | 두 클래스 간 거리가 기준 이하이면 이벤트 생성 |
| `pose_anomaly` | bbox 종횡비·위치 변화·temporal 정보로 쓰러짐 후보 생성 |

---

## config/camera_profiles.yaml

카메라별 도메인과 활성 이벤트를 정의합니다.

```yaml
cam_001:
  domain: general             # general(기본) | construction | school | factory | parking
  enabled_events:             # 범용 기본: 사람·쓰러짐·화재·연기·혼잡
    - person_detected
    - fall
    - fire
    - smoke
    - crowding
  detection_type: general     # cameras.json과 일치시킬 것(VLM 프롬프트 선택에 사용)
  zones: {}                   # zone_intrusion 구역 좌표(restricted_area 활성 시 설정)
```

> 기본 도메인은 **`general`**(범용 CCTV) — 어느 현장에나 바로 동작하며 건설 특화 PPE는 제외한다.
> 현장 특화가 필요하면 `domain`을 construction/factory/parking/school로 바꾼다(모두 `config/event_policies.yaml`에 정의됨).

---

## config/alert_preferences.yaml

대시보드 "알람 설정" 탭에서 저장되는 파일입니다.

```yaml
email_alert_level_threshold: medium   # low | medium | high | critical
per_event_email_enabled:
  fire: true
  fall: true
  no_helmet: false
```

`medium` 이상 이벤트만 이메일이 발송됩니다. `low` 이벤트는 로그에만 기록됩니다.

---

## config/prompts/*.yaml

VLM 도메인별 프롬프트 파일입니다. `detection_type`에 따라 자동 선택됩니다.

| 파일 | 도메인 |
|---|---|
| `construction.yaml` | 건설 현장 |
| `school.yaml` | 학교 |
| `factory.yaml` | 공장 |
| `warehouse.yaml` | 창고 |
| `parking.yaml` | 주차장 |

---

## 헬스체크 HTTP 엔드포인트

`python -m src.utils.health --serve`로 헬스서버를 시작합니다(포트 8081).

| 경로 | 설명 |
|---|---|
| `GET /health` | 전체 헬스 리포트 (JSON) |
| `GET /healthz` | `/health`와 동일 |
| `GET /health/live` | Liveness: 프로세스 생존 여부 |
| `GET /health/ready` | Readiness: disk/memory 임계값 통과 여부 |

정상 시 HTTP 200, 비정상 시 HTTP 503을 반환합니다.

---

## DGX Spark 주의사항

- PyTorch cu130 휠로 설치 (`scripts/install.sh`가 처리).
- ONNX Runtime: 기본 `CPUExecutionProvider`. GPU 추론은 `onnxruntime-gpu` 설치 필요.
- `batch=-1` 절대 금지 — 통합메모리 OOM 발생.
- ARM64 아키텍처: amd64 전용 바이너리 사용 불가.
