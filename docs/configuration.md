# KSI-CCTV 설정 레퍼런스

이 문서는 KSI-CCTV의 모든 설정 파일과 환경 변수를 설명합니다.

---

## 환경 변수

환경 변수는 `.env` 파일 또는 systemd `EnvironmentFile`(`/etc/ksi-cctv/ksi-cctv.env`)로 주입합니다.  
`.env`는 저장소에 커밋하지 않습니다. 템플릿: `.env.example`.

### 필수 및 선택 변수

| 변수 | 예시 | 설명 |
|---|---|---|
| `KSI_CCTV_WEB_PASSWORD` | (없음) | 선택 사항. 설정하면 환경변수 값이 우선하며 UI에서 변경할 수 없음. 비우면 콘솔은 공개 상태로 시작함 |
| `CCTV_CAM_001_URL` | `rtsp://user:pw@192.168.1.101:554/stream1` | 카메라 1번 RTSP URL 또는 로컬 파일 경로 |

추가 카메라는 `CCTV_CAM_002_URL`, `CCTV_CAM_003_URL` … 순서로 추가합니다.  
`cameras.json`의 `camera_id` 번호와 맞춰야 합니다.

### 선택 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `KSI_CCTV_WEB_SECRET` | (없음) | `KSI_CCTV_WEB_PASSWORD`를 설정할 때 필수인 32자 이상 독립 session signing secret. 없거나 placeholder면 환경변수 인증은 fail-closed |
| `KSI_CCTV_WEB_SETUP_TOKEN` | (없음) | 환경 비밀번호가 없을 때 Settings의 최초 보호 설정에 필요한 32자 이상 bootstrap token. 원격 관리자 선점을 막기 위해 현장별로 생성하고 설정 완료 후 제거 |
| `KSI_CCTV_WEB_AUTH_DIR` | `/var/lib/ksi-cctv-web` | 로컬 UI 관리 인증 상태와 서버 세션 레코드 디렉터리. Docker web profile은 owner-only named volume으로 제공 |
| `KSI_CCTV_WEB_SESSION_TTL_SEC` | `43200` | 웹 세션 수명. 300초~2592000초(30일); 범위 밖 값은 기본값 사용 |
| `KSI_CCTV_DEV_AUTH_BYPASS` | `0` | 값이 정확히 `1`일 때만 지원 launcher의 loopback `next dev`에서 일회용 브라우저 capability로 로그인 생략. build/start/Docker/외부 bind에서는 거부 |
| `KSI_CCTV_WEB_DEV` | `0` | `run_web.sh`에서 Next 개발 서버를 선택. 인증 생략에는 정확히 `1`이 필요 |
| `KSI_CCTV_WEB_BIND_HOST` | (실행 경로 결정) | Next bind host 기록. 인증 생략 시 지원 launcher가 loopback socket을 직접 강제 |
| `KSI_CCTV_DEV_AUTH_BROWSER` | `xdg-open` | Ubuntu/Linux 개발용 일회용 인증 URL browser launcher. 지원 운영 Appliance에서는 미설정 |
| `KSI_CCTV_CAPTURE_KEY` | (없음) | 캡처 이미지 암호화용 Fernet 키. 기본 설정이 `save_images: true`, `encrypt_images: true`이므로 운영 배포에는 필수 |
| `KSI_CCTV_SMTP_HOST` | (없음) | SMTP 서버 호스트 |
| `KSI_CCTV_SMTP_PORT` | `587` | SMTP 포트 |
| `KSI_CCTV_SMTP_USER` | (없음) | SMTP 인증 사용자 |
| `KSI_CCTV_SMTP_PASSWORD` | (없음) | SMTP 인증 비밀번호 |
| `KSI_CCTV_SMTP_RECIPIENTS` | (없음) | 알람 수신 이메일 목록 (쉼표 구분) |

> **보안 주의**: RTSP URL, SMTP 비밀번호, Fernet 키는 절대 저장소에 커밋하지 않습니다.

### 웹 콘솔 선택 인증

`KSI_CCTV_WEB_PASSWORD`를 설정하면 환경변수 비밀번호는 불변이며 UI 관리 보호는 사용할 수 없습니다. 설정하지 않은
초기 상태의 콘솔은 열려 있습니다. 열린 콘솔은 Tailnet 또는 HTTPS로 보호된 운영자 전용 네트워크에서만 제공하십시오.
이 상태에서 UI 관리 보호를 처음 설정하려면 서버에 별도로 주입한 `KSI_CCTV_WEB_SETUP_TOKEN`을 입력해야 합니다.
토큰이 없거나 일치하지 않거나 32자 미만이면 설정은 거부되며, 설정 완료 후 배포 환경에서 토큰을 제거합니다.

Docker web profile은 `/var/lib/ksi-cctv-web`을 owner-only named volume으로 유지합니다. 로컬 UI 관리 인증과 모든
서버 세션을 지우려면 실행 중인 웹 컨테이너에서 다음 명령을 명시적으로 확인하여 실행합니다.

```bash
docker compose --profile web exec ksi-cctv-web node /usr/local/bin/ksi-cctv-reset-local-auth --confirm
```

이 도구는 고정된 auth 디렉터리의 `auth.json`과 hash 이름의 세션 레코드만 지우며 임의 경로를 거부하고 비밀 값을
출력하지 않습니다. 비밀번호 설정·변경·해제가 실행 중이면 reset을 거부하므로 작업이 끝난 뒤 다시 실행합니다. 환경 비밀번호가 설정되지 않은 경우 초기화 후 콘솔은 다시 열린 상태가 됩니다.

개발용 로그인 생략은 다음 지원 경로로만 사용합니다. launcher가 실제 loopback socket을 검증하고 기본
브라우저에서 일회용 256-bit capability를 교환한 뒤 HttpOnly session cookie를 발급하며, 화면에 경고 배너를
계속 표시합니다. `next dev`를 직접 실행하면 인증이 생략되지 않습니다.

```bash
KSI_CCTV_WEB_DEV=1 KSI_CCTV_DEV_AUTH_BYPASS=1 bash scripts/run_web.sh
```

브라우저 자동 실행에 실패하면 launcher가 출력한 mode `0600` fallback 파일의 일회용 URL을 같은 호스트의
브라우저에서 엽니다. 이 파일, URL, 발급 cookie를 공유하거나 reverse proxy/Tailscale Serve/SSH tunnel로
게시하지 않습니다. proxy를 통한 요청은 browser capability가 없으므로 인증을 생략하지 않습니다.

`0.0.0.0`, LAN, VPN, Tailscale처럼 loopback 밖에서 확인할 때는 bypass를 사용하지 말고 개발용 비밀번호와
필요한 session TTL을 설정합니다. Appliance에서는 bypass 변수를 설정하지 않습니다.

---

## 설정 파일 목록

| 파일 | 용도 |
|---|---|
| `config/default.yaml` | 앱 전역 기본값 (포트·VLM·탐지·알람·RTSP) |
| `config/cameras.json` | 카메라 채널 목록 및 활성화 여부 — **deprecated**(신규 등록은 managed `config/cameras/` 사용, 아래 마이그레이션 참고) |
| `config/cameras/` | managed 카메라 저장소(`cameras.json` 0600 + 자격증명 분리 보관) |
| `config/model_profiles.yaml` | 모델 등록·경로·클래스·임계값 |
| `config/event_policies.yaml` | 이벤트별 trigger/rule/VLM/alert 정책 |
| `config/camera_profiles.yaml` | 카메라별 도메인·활성 이벤트·zone 설정 |
| `config/alert_preferences.yaml` | 이메일 알람 임계값·이벤트별 on/off |
| `config/prompts/*.yaml` | VLM 도메인 프롬프트 (general/construction/school/factory/warehouse/parking) |

---

## 레거시 cameras.json 마이그레이션 (deprecated → managed)

레거시 `config/cameras.json`(평면 리스트)은 deprecated이며, 신규 카메라 등록은 managed
`config/cameras/`(`CameraRepository`, `cameras.json` 0600, 자격증명 분리 보관)를 사용합니다.
1회성 이관은 `scripts/migrate_legacy_cameras.py`로 수행합니다. 기본은 dry-run이며
`--apply`에서만 기록합니다.

```bash
# 1) 계획 확인 (기록 없음)
./.venv/bin/python -B scripts/migrate_legacy_cameras.py --source config/cameras.json --dest config/cameras

# 2) 실제 기록
./.venv/bin/python -B scripts/migrate_legacy_cameras.py --source config/cameras.json --dest config/cameras --apply
```

주의사항:

- 목적지 managed 저장소에 카메라가 1대라도 있으면 치명 오류(exit 2,
  "관리 저장소가 비어있지 않음 — 수동 병합 필요")로 중단합니다. 병합은 수동으로 수행합니다.
- `stream_url`에 `${...}`가 남은(env 미주입) 항목과 id/필수 필드 검증 실패 항목은
  skip+경고로 보고하고 계속합니다(종료코드 0).
- URL의 `user:pass@` 자격증명은 `CredentialStore`로 분리 저장하고(`camera:<id>` 참조,
  target_scope 포함) 저장된 `rtsp_url`에서 제거합니다. 출력 URL은 마스킹됩니다.
- `name`/`location`/`enabled`/`fps`/`resolution`/`detection_type`→`profile`/
  `enabled_events`/`zones`는 있으면 보존하고 없으면 기본값을 사용합니다.
- 이관 후 레거시 파일은 삭제하지 않고 남겨 둡니다(롤백 대비). managed 이관 완료를
  확인한 뒤 현장 판단으로 제거합니다.

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

### Web console ports

`ui.dashboard_port`는 폐기된 Streamlit 설정이며 Next.js console은 사용하지 않습니다. 개발/랩 launcher는
`KSI_CCTV_WEB_PORT`(기본 3100)를 사용하고, Docker 개발 compose web은 내부 3000을 사용합니다. 운영 Appliance는
Caddy HTTPS 443만 LAN/VPN에 노출하며 engine, MediaMTX, web 내부 포트는 외부에 노출하지 않습니다.

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
  encrypt_images: true               # 기본값. KSI_CCTV_CAPTURE_KEY 필수
  log_max_bytes: 10485760           # 로그 파일 최대 크기 (10MB)
  log_backup_count: 5               # 로테이션 보관 개수
```

`save_images: true`인 상태에서 키가 없거나 잘못되면 데몬은 평문 저장으로 강등하지 않고 시작에 실패합니다.
이미지 보관이 필요 없는 설치만 `save_images: false`를 명시하고 암호화를 끌 수 있습니다.

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

카메라별 도메인과 활성 이벤트를 정의합니다. 실행 모델은 직접 지정하지 않습니다.
`EventPolicyEngine`이 선택된 이벤트의 `trigger.models`를 합산해 필요한 모델만 실행하므로,
이벤트를 비활성화하면 해당 이벤트 전용 모델도 실행되지 않습니다.

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
> 모델을 직접 선택하는 설정은 첫 Appliance release에서 지원하지 않는다. 사용자 모델은 policy에 명시적으로
> 연결하고 smoke test·정확도·라이선스 승인을 거친 뒤 활성 이벤트가 요구할 때만 실행한다.

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

프로덕션 헬스 transport는 엔진 FastAPI compat 라우트이다(엔진 :8081, 비인증).

| 경로 | 설명 |
|---|---|
| `GET /health` | 전체 헬스 리포트 (JSON) |
| `GET /healthz` | `/health`와 동일 |
| `GET /health/live` | Liveness: 프로세스 생존 여부 |
| `GET /health/ready` | Readiness: disk/memory 임계값 통과 여부 |

정상 시 HTTP 200, 비정상 시 HTTP 503을 반환합니다.

---

## DGX Spark 주의사항

- 지원 운영 플랫폼은 Ubuntu ARM64 Appliance(DGX Spark profile)다. Jetson, Windows, macOS는 현재 release 범위 밖이다.
- `scripts/install.sh`는 개발/랩 전용이며 ONNX Runtime GPU 설치를 시도할 뿐 PyTorch cu130 휠을 설치하지 않는다.
- ONNX Runtime: 기본 `CPUExecutionProvider`. GPU 추론은 target image에서 검증된 `onnxruntime-gpu`와 실제 `CUDAExecutionProvider`가 필요하다.
- `batch=-1` 절대 금지 — 통합메모리 OOM 발생.
- ARM64 아키텍처: amd64 전용 바이너리 사용 불가.
