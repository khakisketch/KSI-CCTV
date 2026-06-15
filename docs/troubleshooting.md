# KSI-CCTV 문제 해결 가이드

현장 운영 중 발생할 수 있는 문제별 진단 절차와 해결 방법을 설명합니다.

---

## 빠른 진단

문제 발생 시 가장 먼저 헬스체크를 실행합니다.

```bash
./.venv/bin/python scripts/healthcheck.py --json
```

결과의 `overall_healthy: false`인 경우 `components` 목록에서 `healthy: false` 항목을 찾습니다.

```bash
# VLM 포함 진단
./.venv/bin/python scripts/healthcheck.py --check-vlm --json

# 카메라 URL 확인 포함
./.venv/bin/python scripts/healthcheck.py --check-cameras --json

# GPU provider 요구
./.venv/bin/python scripts/healthcheck.py --require-gpu --json
```

---

## 카메라 / RTSP 문제

### 증상: 대시보드에 "카메라 연결 실패" 표시

**원인 및 해결:**

1. 환경 변수 확인
   ```bash
   # URL이 설정되었는지 확인 (실제 값은 노출되지 않음)
   ./.venv/bin/python scripts/healthcheck.py --check-cameras --json
   ```
2. RTSP URL 형식 확인: `rtsp://user:password@IP:554/stream1`
3. 카메라와의 네트워크 연결 확인 (`ping IP`)
4. 포트 `554`가 방화벽에서 허용되었는지 확인
5. 카메라 계정 정보가 올바른지 확인

### 증상: 프레임이 끊기거나 FPS가 낮음

**원인 및 해결:**

1. 네트워크 대역폭 부족 → RTSP 해상도를 낮추거나 `config/default.yaml`의 `cameras.reconnect_interval_sec` 조정
2. 탐지 간격 조정: `detection.skip_frames` 값을 높여 CPU 부하 감소
3. `dynamic_skip` 활성화: `SafetyPipeline(dynamic_skip=True)` — 정적 장면에서 자동으로 skip_frames 증가

### 증상: 재연결이 계속 반복됨

**확인:**
```bash
tail -f logs/alerts.log | grep -i "reconnect\|camera\|rtsp"
```

**원인:** 카메라 신호 불안정 또는 타임아웃 값이 너무 짧음.  
`config/default.yaml`의 `cameras.timeout_sec` 값을 늘립니다.

---

## YOLO / 탐지 문제

### 증상: 모델 로드 실패

```bash
# 모델 파일 존재 여부 확인
ls -lh models/*.onnx

# 헬스체크에서 failed_models 확인
./.venv/bin/python scripts/healthcheck.py --json | python3 -c "
import json, sys
r = json.load(sys.stdin)
for c in r.get('components', []):
    if 'model' in c['name'].lower():
        print(c)
"
```

**해결:**
- `general_yolo` 모델(`models/yolo26.onnx`)이 없으면 `scripts/prepare_yolo26.py`로 다운로드
- PPE/fire 모델이 없으면: `./.venv/bin/python scripts/fetch_safety_models.py --all`

### 증상: ONNX provider 오류 / CUDA 사용 불가

```bash
python3 -c "import onnxruntime as ort; print(ort.get_available_providers())"
```

**CUDAExecutionProvider가 없을 때:**
- DGX Spark 환경: `onnxruntime-gpu` cu130 빌드 필요 (`scripts/install.sh` 참조)
- 일반 환경: `pip install onnxruntime-gpu` 또는 CPU 모드로 운영
- `config/default.yaml`의 `device.primary: "cpu"`로 CPU fallback 강제

**ONNX 클래스 불일치 경고 (`target_classes 도달성 경고`) 발생 시:**

config `classes`와 실제 ONNX 모델 출력 클래스 수가 다릅니다.  
`model_profiles.yaml`에서 해당 모델의 `classes: null`로 설정하면  
ONNX 메타데이터에서 클래스를 자동 인식합니다.

### 증상: 탐지 결과가 전혀 없음 (COCO 모델)

COCO pretrained 모델(`class_profile: coco`)은 `helmet`, `vest`, `fire`, `smoke`, `fall` 클래스를  
직접 탐지할 수 없습니다. 이는 모델의 구조적 한계입니다.

**해결:**
- `person` 탐지 프레임을 VLM에 전달하여 안전 상황 분석 (`vlm_trigger_classes: "person"`)
- 안전 이벤트 직접 탐지가 필요하면 커스텀 `safety_custom` ONNX 모델이 필요합니다 (`docs/custom-model-training.md` 참조)
- 대시보드에 COCO 한계 경고 배지가 표시됩니다

---

## VLM (Ollama) 문제

### 증상: VLM 분석이 항상 실패함

```bash
# Ollama 서버 상태 확인
curl http://localhost:11434/api/tags

# 모델 목록 확인
ollama list
```

**해결:**
1. Ollama 서버 시작: `ollama serve`
2. 모델 다운로드: `ollama pull qwen3-vl:latest`
3. `config/default.yaml`의 `models.vlm.base_url` 주소가 올바른지 확인

### 증상: VLM 분석이 너무 느림 (타임아웃)

**원인:** GPU 미사용 또는 모델이 너무 큼.

**해결:**
- `config/default.yaml`의 `models.vlm.timeout_sec` 값 조정
- Ollama에 GPU가 할당되었는지 확인: `ollama ps`
- `vlm_cooldown_sec`을 늘려 VLM 호출 빈도 감소

### 증상: VLM JSON 파싱 실패 표시

대시보드 이벤트 카드에 "VLM 파싱 실패" 배지가 표시되면  
VLM이 JSON 형식이 아닌 자유 텍스트를 반환한 것입니다.  
`raw_response` 원문이 함께 표시됩니다. VLM 모델 또는 프롬프트 조정이 필요합니다.

---

## 대시보드 문제

### 증상: 대시보드 로그인 불가 (비밀번호 오류)

```bash
# 환경 변수가 설정되었는지 확인
echo "KSI_CCTV_DASHBOARD_PASSWORD is set: $([ -n "$KSI_CCTV_DASHBOARD_PASSWORD" ] && echo yes || echo no)"
```

`.env` 파일이 올바른 경로에 있는지, 비밀번호에 특수문자가 포함된 경우 따옴표로 감쌌는지 확인합니다.

### 증상: 로그인 시도 차단 (rate limit)

60초 내 5회 실패 시 60초 잠금이 걸립니다. 잠금 시간이 지나면 자동 해제됩니다.  
서버를 재시작하면 즉시 해제됩니다.

### 증상: 자동 로그아웃 반복

30분 무활동 시 세션이 만료됩니다. 대시보드를 자주 사용한다면 사이드바의 "자동 새로고침" 기능을 활성화하면 세션이 유지됩니다.

### 증상: 대시보드 포트 충돌 / 접속 불가

```bash
# 포트 사용 여부 확인
sudo ss -tlnp | grep 8080
```

`config/default.yaml`의 `ui.dashboard_port`를 다른 포트로 변경하거나 기존 프로세스를 종료합니다.

---

## 알람 / 이메일 문제

### 증상: 이메일이 발송되지 않음

1. SMTP 환경 변수 설정 여부 확인 (`KSI_CCTV_SMTP_HOST`, `PORT`, `USER`, `PASSWORD`, `RECIPIENTS`)
2. 대시보드 "시스템 상태" 탭의 "SMTP 테스트" 버튼으로 연결 테스트
3. `config/alert_preferences.yaml`의 `email_alert_level_threshold` 확인 — `medium` 이하 이벤트는 발송되지 않음
4. 이벤트의 `alert_level`이 임계값 이상인지 확인 (`fire`는 `critical`, `person_detected`는 `low`)

### 증상: 알람이 폭주함

- `config/event_policies.yaml`의 각 이벤트 `cooldown_sec` 값을 조정
- `config/default.yaml`의 `detection.alert_cooldown_sec` 값을 늘림
- 배치 처리 활성화: `AlertConfig(batch_flush_interval=60)` — 1분마다 묶어서 발송

---

## 디스크 / 로그 문제

### 증상: 디스크 사용량이 계속 증가

```bash
# 캡처 이미지 크기 확인
du -sh data/captures/

# 로그 파일 크기 확인
du -sh logs/
```

**해결:**
- `config/default.yaml`의 `alerts.image_retention_days`를 줄임 (예: 7)
- 오래된 이미지 수동 삭제: `find data/captures/ -mtime +7 -delete`
- 헬스체크의 `disk_space` 컴포넌트가 임계값(`90%` 기본)을 초과하면 경고

### 증상: 로그 파일이 지나치게 큼

`RotatingFileHandler`가 10MB 도달 시 자동 로테이션하며 5개를 보관합니다.  
`config/default.yaml`의 `alerts.log_max_bytes`와 `log_backup_count`로 조정합니다.

---

## 메모리 / GPU 문제

### 증상: 메모리 부족 오류 (DGX Spark)

- **`batch=-1` 절대 금지** — 통합메모리 전체를 소비하여 OOM 발생
- 학습 시: `batch=8` 또는 `batch=4`로 고정
- ONNX 추론 시: `SafetyPipeline(dynamic_skip=True)`로 정적 장면에서 추론 빈도 줄임

### 증상: 메모리 사용량이 계속 증가 (메모리 누수 의심)

```bash
# 프로파일링으로 메모리 추이 확인
./.venv/bin/python scripts/profile_pipeline.py \
    --frames 20 --iterations 5 \
    --json-out reports/memory_check.json
```

파이프라인은 캡처 이미지를 최근 50건만 보존합니다 (`_record_event()` 내 메모리 보호).  
50건 초과 시 오래된 이미지가 자동 해제됩니다.

---

## 설치 및 환경 문제

### 증상: `import` 오류 / 패키지 없음

```bash
# 가상환경 활성화 확인
which python3
# /home/.../ksi-cctv/.venv/bin/python3 이어야 함

# 재설치
bash scripts/install.sh
```

### 증상: `onnxruntime` ARM64 호환 오류

DGX Spark (ARM64 `aarch64`)에서는 x86_64 전용 패키지가 동작하지 않습니다.  
`scripts/install.sh`가 ARM64 환경을 감지하고 적절한 패키지를 설치합니다.  
수동 설치 시 `onnxruntime` 대신 `onnxruntime-gpu` (cu130 빌드) 또는 ARM 빌드를 사용합니다.

### 증상: Python 컴파일 오류 또는 테스트 실패

```bash
# 전체 컴파일 확인
python3 -m compileall -q src tests scripts

# 단위 테스트 실행
./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider -q
# 기대 결과: 333 passed, 1 skipped
```

---

## 자주 확인하는 명령

```bash
# 헬스체크 (기본)
./.venv/bin/python scripts/healthcheck.py --json

# 최신 이벤트 로그
tail -20 logs/alerts.log

# 모델 상태
./.venv/bin/python -c "
from src.detection.model_registry import MultiModelRegistry
reg = MultiModelRegistry.from_config('./config/model_profiles.yaml')
import json; print(json.dumps(reg.to_status_dict(), indent=2, default=str))
"

# VLM 연결 확인
curl -s http://localhost:11434/api/tags | python3 -m json.tool

# 단위 테스트
./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider -q

# E2E 스모크 테스트
./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode
```
