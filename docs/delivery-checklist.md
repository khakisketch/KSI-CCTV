# KSI-CCTV 납품 및 시연 체크리스트

이 문서는 현장 납품 전 설치 점검과 시연 진행 절차를 제공합니다.

---

## 파트 A: 납품용 설치 체크리스트

현장 설치 담당자가 납품 전 모든 항목을 확인합니다.

### A1. 환경 준비

- [ ] Python 3.10 이상 설치 확인: `python3 --version`
- [ ] GPU 드라이버 및 CUDA 설치 확인 (DGX Spark: CUDA 13 / cu130)
- [ ] Ollama 설치 확인: `ollama --version`
- [ ] `qwen3-vl:latest` 모델 다운로드 완료: `ollama list | grep qwen3-vl`
- [ ] 저장소 클론 또는 배포 패키지 압축 해제 완료

### A2. 설치 스크립트 실행

```bash
bash scripts/install.sh
```

- [ ] 가상환경 생성 완료 (`.venv/`)
- [ ] 패키지 설치 완료 (오류 없음)
- [ ] 런타임 디렉터리 생성 완료 (`logs/`, `data/captures/`, `models/uploaded/`)

### A3. 환경 변수 설정

```bash
cp .env.example .env
nano .env   # 또는 vi .env
```

- [ ] `KSI_CCTV_DASHBOARD_PASSWORD` — 강력한 비밀번호로 변경 (기본값 `change-me` 금지)
- [ ] `CCTV_CAM_001_URL` — 현장 카메라 1번 RTSP URL 입력
- [ ] 추가 카메라 URL (`CCTV_CAM_002_URL` 등) 입력 (해당 시)
- [ ] `KSI_CCTV_SMTP_*` — SMTP 설정 입력 (이메일 알람 사용 시)
- [ ] `.env` 파일 권한 제한: `chmod 600 .env`

### A4. 모델 준비

```bash
# 기본 YOLO 모델 확인
ls -lh models/yolo26.onnx

# 사전학습 안전 모델 다운로드 (인터넷 필요)
./.venv/bin/python scripts/fetch_safety_models.py --all
```

- [ ] `models/yolo26.onnx` 존재 확인
- [ ] `models/yolo26_ppe.onnx` 존재 확인 (PPE 탐지 사용 시)
- [ ] `models/yolo26_fire_smoke.onnx` 존재 확인 (화재/연기 탐지 사용 시)

### A5. 설정 파일 검토

- [ ] `config/cameras.json` — 카메라 채널 ID와 활성화 여부 확인
- [ ] `config/camera_profiles.yaml` — 카메라별 도메인/이벤트 설정 확인
- [ ] `config/event_policies.yaml` — 이벤트 정책 검토 (기본값 사용 가능)
- [ ] `config/model_profiles.yaml` — 활성 모델 목록 확인 (`enabled: true`)

### A6. 헬스체크 통과

```bash
./.venv/bin/python scripts/healthcheck.py --json
```

- [ ] `overall_healthy: true` 확인
- [ ] `disk_space`: 통과 (사용률 90% 이하)
- [ ] `memory_usage`: 통과 (사용률 90% 이하)
- [ ] GPU 사용 환경에서: `./.venv/bin/python scripts/healthcheck.py --require-gpu --json` 통과
- [ ] VLM 사용 환경에서: `./.venv/bin/python scripts/healthcheck.py --check-vlm --json` 통과

### A7. 단위 테스트 통과

```bash
./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider -q
```

- [ ] `333 passed, 1 skipped` (또는 이상 없음) 확인

### A8. E2E 스모크 테스트

```bash
./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode
```

- [ ] `ok: true` 확인
- [ ] `failed_models: {}` 확인 (모든 활성 모델 로드 성공)

### A9. 대시보드 기동 확인

```bash
bash scripts/run_dashboard.sh
```

- [ ] `http://localhost:8080` 접속 성공
- [ ] 설정한 비밀번호로 로그인 성공
- [ ] 빈 데이터(이벤트 0건) 상태에서 레이아웃이 정상으로 렌더됨
- [ ] "시스템 상태" 탭에서 모델 로드 상태 확인

### A10. systemd 서비스 등록 (운영 환경)

```bash
# site-specific 값 수정 후 설치
sudo install -D -m 0644 systemd/ksi-cctv.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ksi-cctv
sudo systemctl start ksi-cctv
sudo systemctl status ksi-cctv
```

- [ ] `Active: active (running)` 확인
- [ ] 재부팅 후 자동 시작 확인: `sudo systemctl is-enabled ksi-cctv`

---

## 파트 B: 시연용 체크리스트

시연 담당자가 시연 당일 사용하는 절차입니다.

### B1. 시연 전 준비

- [ ] 시연 장비에 KSI-CCTV 대시보드가 기동 중임 확인 (`http://localhost:8080`)
- [ ] 시연용 샘플 영상 준비 (`data/sample.mp4` 또는 실제 현장 영상)
  - 권장: 사람이 포함된 영상 (YOLO 탐지 박스가 표시됨)
  - 선택: 안전모 미착용/화재 장면 (커스텀 모델 사용 시)
- [ ] CCTV 카메라 or 영상 파일 URL이 `.env`에 설정되어 있음 확인
- [ ] Ollama VLM이 실행 중임 확인 (`curl http://localhost:11434/api/tags`)
- [ ] (이메일 시연 시) SMTP 설정 완료 및 테스트 완료

### B2. 시연 동선

#### 2-1. 대시보드 로그인

- [ ] 브라우저에서 `http://localhost:8080` 접속
- [ ] 비밀번호 입력 후 로그인

#### 2-2. 파이프라인 시작

- [ ] 사이드바에서 카메라 소스 선택 (영상 파일 또는 RTSP)
- [ ] "파이프라인 시작" 버튼 클릭
- [ ] 상태가 "실행 중"으로 전환됨 확인

#### 2-3. 실시간 영상 및 탐지 확인 ("실시간 모니터링" 탭)

- [ ] 실시간 프레임이 표시됨
- [ ] 사람이 포함된 프레임에서 YOLO 탐지 박스(초록 테두리)가 표시됨
- [ ] 탐지 클래스 레이블이 표시됨

#### 2-4. VLM 분석 확인 ("이벤트" 탭)

- [ ] person 탐지 이벤트가 이벤트 목록에 나타남
- [ ] VLM 분석이 완료된 이벤트에 `risk_level`, `description`, `recommended_action`이 표시됨
- [ ] (파싱 실패 시) "VLM 파싱 실패" 배지와 원문이 표시됨

#### 2-5. 알람 로그 확인 ("이벤트 로그" 탭)

- [ ] `logs/alerts.log`의 이벤트가 탭에 표시됨
- [ ] 이벤트 유형 필터 동작 확인

#### 2-6. 캡처 이미지 확인 ("갤러리" 탭)

- [ ] 저장된 캡처 이미지가 표시됨
- [ ] 얼굴 블러링이 적용된 이미지 확인
- [ ] (`encrypt_images: true` 시) `.enc` 파일이 복호화되어 표시됨

#### 2-7. 시스템 상태 확인 ("시스템 상태" 탭)

- [ ] CPU/메모리/디스크 사용률 표시됨
- [ ] 활성 모델 목록이 `general_yolo`, `ppe_yolo`, `fire_smoke_yolo`로 표시됨
- [ ] VLM 메트릭 (호출 수, 성공률, 평균 레이턴시) 표시됨
- [ ] SMTP 테스트 버튼 클릭 시 성공 메시지 표시됨 (SMTP 설정 시)

#### 2-8. 모델 업로드 시연 (해당 시)

- [ ] "모델 업로드" 탭에서 `.onnx` 파일 업로드
- [ ] smoke test 성공 후 `enabled: false` 상태로 등록됨 확인
- [ ] "활성화" 버튼 클릭 후 모델이 활성화됨 확인

#### 2-9. 이벤트 내보내기 시연

- [ ] "이벤트" 탭에서 "CSV 내보내기" 클릭 후 파일 다운로드 확인
- [ ] "JSON 내보내기" 클릭 후 파일 다운로드 확인

### B3. COCO 모델 한계 설명 포인트

시연 중 반드시 안내해야 할 항목:

- 기본 YOLO26 모델은 COCO pretrained 모델입니다.
- `helmet`, `vest`, `fire`, `smoke`, `fall` 클래스를 **직접 탐지하지 못합니다**.
- 대시보드에 COCO 한계 경고 배지가 표시됩니다.
- 안전 이벤트 탐지를 위해 VLM 분석(Ollama qwen3-vl) 또는 커스텀 safety 모델이 필요합니다.
- 커스텀 모델 학습 방법: `docs/custom-model-training.md` 참조.

### B4. 시연 완료 후

- [ ] "파이프라인 중지" 클릭
- [ ] 불필요한 캡처 이미지 정리 (`data/captures/`)
- [ ] 시연용 테스트 데이터 정리

---

## 납품 완료 기준

아래 모든 항목이 통과되어야 납품 완료입니다.

| 항목 | 기준 |
|---|---|
| 단위 테스트 | 333 passed (또는 이상 없음) |
| E2E 스모크 테스트 | `ok: true`, `failed_models: {}` |
| 헬스체크 | `overall_healthy: true` |
| 대시보드 기동 | `http://localhost:8080` 접속 성공, 로그인 성공 |
| 실시간 탐지 | 영상 프레임에서 YOLO 탐지 박스 표시 |
| VLM 분석 | 이벤트에 VLM 결과(`risk_level`, `description`) 표시 |
| 알람 로그 | `logs/alerts.log` 이벤트 기록 확인 |
| 캡처 이미지 | `data/captures/` 얼굴 블러 이미지 저장 확인 |

---

## 참고 문서

- [설정 레퍼런스](configuration.md)
- [운영 가이드](operations.md)
- [문제 해결 가이드](troubleshooting.md)
- [배포 가이드](deployment-guide.md)
- [아키텍처](architecture.md)
