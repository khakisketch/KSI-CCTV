# KSI-CCTV: DGX Spark 기반 지능형 CCTV 안전 관리 시스템

건설 현장, 학교 등 여러 발주처의 CCTV 영상을 중앙 컴퓨터(DGX Spark)에서 분석해 불안전 행동, 쓰러짐, 안전장비 미착용, 화재/연기 등 이상 상황을 실시간 감지하고 관리자에게 알람과 자연어 설명을 제공하는 AI 기반 솔루션입니다.

## 시스템 아키텍처

- **중앙집중형 관제**: CCTV → 네트워크 허브 → DGX Spark 중앙 처리
- **MultiModelRegistry**: ONNX 메타데이터 기반 클래스 자동 인식, 모델별 latency/error metrics, 모델 로드 실패 격리
- **EventPolicyEngine**: 이벤트별 trigger/rule/VLM/alert 정책 YAML 기반 평가
- **VLM Dispatcher**: Ollama Qwen3-VL — 필요한 이벤트만 분석 (TTL cache + backpressure)
- **AlertManager**: 이벤트 로그, 캡처 이미지 저장, 얼굴 블러링, 이미지 암호화(Fernet), SMTP 알람
- **엔진 API (FastAPI)**: 이벤트·카메라·모델·정책·설정·헬스 읽기 + 프롬프트/활성 쓰기 + 제어(start/stop). fail-closed X-API-Key 인증, 시크릿 마스킹. 데몬에 in-process 마운트(탐지+API 단일 서비스)
- **Next.js 웹 콘솔**: 데스크톱 관제 + 모바일 뷰어(반응형, 다크·라이트). 라이브 영상·이벤트·모델 프롬프트·설정. 자체 로그인(HMAC 세션). API 키는 서버 전용(브라우저 미노출)
- **MediaMTX 영상 게이트웨이**: RTSP → HLS/WebRTC 재발행. 웹 콘솔은 same-origin 프록시로 라이브 재생(게이트웨이 주소·RTSP 자격증명 미노출)
- **웹 콘솔 기능**: 실시간 모니터링·이벤트(SSE)·라이브 영상(HLS)·갤러리·모델(목록/프롬프트/업로드/활성·삭제)·알림 설정·시스템 상태·파이프라인 제어·카메라 설정·감사 로그 — 모두 Next.js 웹 콘솔로 제공(구 Streamlit 대시보드 대체·폐기)
- **헬스체크**: `/health`, `/healthz`, `/health/live`, `/health/ready` HTTP 엔드포인트

## 핵심 기능

- RTSP 스트림 수신 및 프레임 큐 관리 (RTSP 타임아웃, 지수백오프 재연결, FPS 이동평균)
- YOLO26 ONNX 다중 모델 추론 (general_yolo, ppe_yolo, fire_smoke_yolo)
- ONNX 메타데이터 기반 클래스 자동 인식 — config 불일치 시 메타데이터 우선 + 경고
- 이벤트별 VLM 정책 (never/always/on_ambiguous/on_high_confidence/on_low_confidence)
- 사용자 ONNX 모델 업로드/검증/격리 저장/활성화 (대시보드 "모델 업로드" 탭)
- 이벤트 로그 및 캡처 이미지 저장 (얼굴 블러링, 선택적 Fernet 암호화)
- scrypt 비밀번호 + rate limit + 세션 만료 + 감사 로그 대시보드 보안
- RTSP와 로컬 비디오 파일 (`.mp4`, `.avi` 등) 입력 지원
- Docker Compose / systemd 서비스 배포 지원

## 빠른 시작

```bash
# 설치 (가상환경, 의존성, 런타임 디렉터리)
bash scripts/install.sh

# 환경변수 설정
cp .env.example .env
# .env에서 KSI_CCTV_DASHBOARD_PASSWORD, CCTV_CAM_001_URL 수정
# config/cameras.json에서 사용할 채널의 enabled를 true로 변경

# 사전학습 안전 모델 다운로드 (선택 — 인터넷 필요)
./.venv/bin/python scripts/fetch_safety_models.py --all

# 헬스체크
./.venv/bin/python scripts/healthcheck.py --json

# 탐지 데몬 실행 (파이프라인 + 엔진 API/헬스 :8081)
./.venv/bin/python src/run.py
```

### 웹 콘솔(Next.js) + 영상 게이트웨이 — Docker

```bash
# .env에 키 설정(엔진/웹 공유): KSI_CCTV_API_KEY, KSI_CCTV_WEB_PASSWORD
# 엔진(데몬) + 웹 콘솔 + 영상 게이트웨이 동시 기동
docker compose --profile web up --build
# 웹 콘솔: http://localhost:3000  (로그인 후 카메라 페이지에서 라이브 영상)
#   - 엔진 API/헬스(8081)와 게이트웨이(8888)는 호스트에 노출하지 않고 compose 네트워크 내부에서 연동
#   - 원격 접속은 Tailscale 등으로 3000 포트를 tailnet에 노출
```

> 웹 콘솔 라이브 영상은 카메라(또는 RTSP 소스)가 게이트웨이에 발행/풀될 때 재생된다. 카메라 없이
> 검증하려면 `ffmpeg`로 샘플을 합성 RTSP로 발행하면 된다(`docs/implementation-plan.md` P5 검증 참조).

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

- `src/capture/`: RTSP 스트림 캡처 (타임아웃, 재연결, FPS 이동평균)
- `src/detection/`: MultiModelRegistry, YOLO ONNX 탐지, 클래스 어댑터
- `src/policy/`: EventPolicyEngine, RuleEvaluatorRegistry
- `src/pipeline/`: SafetyPipeline (policy-mode / legacy 이중 경로)
- `src/vlm/`: VLM inference (Ollama), PromptSelector, TTL cache
- `src/alerts/`: AlertManager (로그, 캡처, 얼굴 블러, 암호화, SMTP)
- `src/api/`: 엔진 API (FastAPI app·service·auth) — 읽기/쓰기/제어 + compat 헬스
- `src/models/`: 사용자 모델 업로드 검증기
- `src/utils/`: 헬스체크, 시스템 메트릭, 설정 로더
- `frontend/`: Next.js 웹 콘솔 (App Router, Tailwind, shadcn/ui — 관제/뷰어, 라이브 영상)
- `config/`: YAML 설정 파일, 도메인별 VLM 프롬프트, `mediamtx.yml`(영상 게이트웨이)
- `scripts/`: 설치, 헬스체크, E2E 테스트, 프로파일링, 모델 다운로드/학습
- `tests/`: 유닛/통합 테스트 (462 passed, 3 skipped)

## 개발 환경

- Python 3.10+
- NVIDIA GPU + CUDA 환경 권장
- DGX Spark 실증 기준
- 개발 보조 모델: Qwen 3.6 27B Dense 권장

## 현재 상태 (2026-06-17)

- 단위 테스트: **462 passed, 3 skipped** (`./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider -q`)
- E2E 스모크: `ok: true`, `failed_models: {}`
- 엔진: policy-mode (MultiModelRegistry + EventPolicyEngine) + 데몬에 FastAPI in-process(탐지+API 단일 서비스)
- 설치형 제품화: Next.js 웹 콘솔 + MediaMTX 영상 게이트웨이(라이브 HLS 브라우저 재생 검증 완료). 진행 트래커 = `docs/implementation-plan.md`
- 활성 모델: `general_yolo`, `ppe_yolo`, `fire_smoke_yolo`

## 다음 단계

1. 실 RTSP 카메라로 라이브 영상·탐지 E2E 검증 (게이트웨이 풀 + 엔진 탐지 동시)
2. DGX Spark 환경에서 `CUDAExecutionProvider`/TensorRT 가속 검증
3. 설치형 패키징 마무리 (Windows 서비스/인스톨러·오프라인 번들) + 라이브 영상 텔레메트리/탐지 오버레이 보강
> 커스텀 모델 학습은 제품 비목표(범용 사전학습 + open-vocab 텍스트 탐지로 대체) — 필요 시 `scripts/train_*.py`로 별도 수행 가능(데이터셋/GPU 필요).

## 문서

### 제품 방향 로드맵 (설계 단계 — 구현 전)
| 문서 | 내용 |
|---|---|
| `docs/architecture-target.md` | **타깃 아키텍처** — 엔진+FastAPI+웹UI(Next/shadcn)+영상게이트웨이, Streamlit 마이그레이션 단계 |
| `docs/roadmap-capability.md` | **능력 로드맵** — open-vocab(YOLO-World 등) 텍스트 즉시탐지 + VLM 상황탐지 통합 |
| `docs/roadmap-packaging.md` | **크로스플랫폼 패키징** — OS×가속(EP)×설치방식 매트릭스(Win/Win-ARM64/macOS/Linux) |

### 운영·구현 문서
| 문서 | 내용 |
|---|---|
| `docs/configuration.md` | 환경변수·설정파일 레퍼런스 |
| `docs/operations.md` | 24h 운영·로그 관리·헬스체크 |
| `docs/troubleshooting.md` | 장애 진단 및 해결 가이드 |
| `docs/delivery-checklist.md` | 납품용 설치 체크리스트 + 시연 체크리스트 |
| `docs/deployment-guide.md` | 설치·배포·systemd 상세 가이드 |
| `docs/architecture.md` | 목표 아키텍처 (policy-mode) |
| `docs/model-management.md` | 모델 관리 전략 (VLM provider 결정 포함) |
| `docs/model-sourcing.md` | **공개 사전학습 모델 소싱 가이드** (PPE/화재 추천·AGPL 경고·교체 절차) |
| `docs/event-policy-engine.md` | 이벤트 정책 엔진 설계 |
| `docs/custom-model-training.md` | 커스텀 모델 학습 전체 가이드 |
| `docs/phase-cd-readiness.md` | **Phase C/D 실행 runbook** — 실환경 검증·커스텀 모델 정량 합격 기준 |
| `docs/development-roadmap.md` | 단계별 개발 로드맵 |
| `docs/completion-plan.md` | 납품 완성 계획 (체크박스 현행화) |

## License

MIT License
