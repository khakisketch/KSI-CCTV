# KSI-CCTV: Ubuntu ARM64 Appliance 기반 지능형 CCTV 안전 관리 시스템

건설 현장, 학교 등 여러 발주처의 CCTV 영상을 DGX Spark ARM64 오프라인 Appliance에서 분석해 안전장비 미착용, 화재/연기, ByteTrack 기반 line-crossing/loitering 후보를 감지하고 관리자에게 알람을 제공하는 AI 기반 솔루션입니다. 안전 이벤트의 현장 정확도와 운영 가능 범위는 별도 qualification과 승인이 필요합니다.

> **Advanced-AI v1 상태 (2026-08-01):** HLS와 single-admin은 대상 범위이며 COCO/PPE/화재·연기 및
> ByteTrack tracking은 구현됐다. pose/ST-GCN fall/fight와 bounded local VLM verification은 개발 중이다.
> VLM 실패는 반드시 `unverified` alert가 되어야 하며 silent suppression은 허용되지 않는다. ARM64 CUDA,
> 현장 데이터/정확도, 실제 카메라/HLS, 복구 및 채널 soak evidence는 아직 qualification 필요이고, 16채널은
> 입증되지 않았다. WebRTC, cloud/native app, RBAC, production 기본 open-vocab, LLM reporting, 자동
> few-shot/GNN, theft/weapons/smoking은 v1 범위 밖이다.

## 시스템 아키텍처

- **중앙집중형 관제**: CCTV → 네트워크 허브 → DGX Spark 중앙 처리
- **MultiModelRegistry**: ONNX 메타데이터 기반 클래스 자동 인식, 모델별 latency/error metrics, 모델 로드 실패 격리
- **EventPolicyEngine**: 이벤트별 trigger/rule/VLM/alert 정책 YAML 기반 평가
- **VLM Dispatcher**: bounded local VLM verification 개발 경로. qualification 전에는 release capability가 아니며, 실패는 `unverified` alert로 보존해야 함
- **AlertManager**: 이벤트 로그, 캡처 이미지 저장, 얼굴 블러링, 이미지 암호화(Fernet), SMTP 알람
- **엔진 API (FastAPI)**: 이벤트·카메라·모델·정책·설정·헬스 읽기 + 프롬프트/활성 쓰기 + 제어(start/stop). fail-closed X-API-Key 인증, 시크릿 마스킹. 데몬에 in-process 마운트(탐지+API 단일 서비스)
- **Next.js 웹 콘솔**: 데스크톱 관제 + 모바일 뷰어(반응형, 다크·라이트). 라이브 영상·이벤트·모델 프롬프트·설정. 자체 로그인(HMAC 세션). API 키는 서버 전용(브라우저 미노출)
- **MediaMTX 영상 게이트웨이**: RTSP → HLS 재발행. 1차 Appliance release는 HLS only이며 WebRTC는 WHEP/ICE qualification 후속 범위다. 웹 콘솔은 same-origin 프록시로 라이브 재생한다.
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

## Production Deployment

**지원되는 v1 운영 배포 대상은 DGX Spark ARM64 offline Appliance뿐입니다.** 저장소 clone, `scripts/install.sh`,
`scripts/run_web.sh`, 일반 Docker Compose는 개발/랩 전용이며 운영 시스템에 사용하지 않습니다.

1. 서명된 ARM64 release media와 별도 보관한 Ed25519 공개키, 0600 site env를 준비합니다.
2. `docs/offline-appliance.md`의 `doctor`, 서명 검증 설치, activation health, site CA 신뢰 절차를 수행합니다.
3. Caddy HTTPS endpoint만 운영자 VLAN/VPN에 노출합니다. 엔진 API와 MediaMTX는 Appliance Compose 네트워크 내부에 둡니다.
4. 설치 후 웹 wizard로 카메라를 등록하고 `/api/cameras/readiness`와 현장 soak 검증을 완료합니다.

## Development/Lab Quick Start

아래 절차는 개발 또는 격리된 랩에서만 사용합니다. Appliance 운영 절차를 대체하지 않습니다.

```bash
# 개발/랩 설치 (가상환경, 의존성, 런타임 디렉터리)
bash scripts/install.sh

# 환경변수 설정
cp .env.example .env
# .env에서 KSI_CCTV_API_KEY와 CCTV_CAM_001_URL을 설정
# config/cameras.json에서 사용할 채널의 enabled를 true로 변경

# 사전학습 안전 모델 다운로드 (선택 — 인터넷 필요)
./.venv/bin/python scripts/fetch_safety_models.py --all

# 헬스체크
./.venv/bin/python scripts/healthcheck.py --json

# 탐지 데몬 실행 (파이프라인 + 엔진 API/헬스 :8081)
./.venv/bin/python src/run.py
```

### 웹 콘솔 직접 확인 (개발/랩)

로컬에서 전체 웹 콘솔(엔진 API + 영상 게이트웨이 + Next.js UI)을 한 번에 띄운다:

```bash
bash scripts/run_web.sh                  # http://127.0.0.1:3100
KSI_CCTV_DEMO=1 bash scripts/run_web.sh  # 데모 이벤트/캡처 시드 + sample.mp4를 cam_001 라이브로 송출
# loopback next dev에서만 로그인 생략(항상 경고 배너 표시)
KSI_CCTV_WEB_DEV=1 KSI_CCTV_DEV_AUTH_BYPASS=1 bash scripts/run_web.sh
```

KSI-CCTV 전용 포트(다른 프로젝트와 충돌 회피): **웹 3100 · 엔진 8181 · 게이트웨이 HLS 8910 / RTSP 8954**
(각각 `KSI_CCTV_WEB_PORT`/`KSI_CCTV_ENGINE_PORT`/`KSI_CCTV_GW_HLS_PORT`/`KSI_CCTV_GW_RTSP_PORT`로 override).
기본 바인드는 loopback이며 API와 MediaMTX도 loopback-only다. 비루프백 웹 바인드는 격리된 시험망에서만
`KSI_CCTV_INSECURE_REMOTE_DEV=1`을 명시해 허용된다. 자격증명을 주지 않으면 임시 파일에 생성되며 콘솔에 출력하지 않는다.
인증 생략은 위 지원 launcher가 소유한 loopback `next dev` socket과 launcher가 로컬 브라우저에 발급한
일회용 capability cookie가 모두 있을 때만 허용된다. 기본 브라우저를 자동으로 열며, 실패 시 mode `0600`
fallback URL 파일 경로만 출력한다. URL/cookie를 공유하거나 reverse proxy, Tailscale Serve, SSH tunnel로
게시하지 않는다. 직접 실행한 `next dev`와 build/start/Docker/비루프백에서는 거부된다. 값은 정확히 `1`만
활성화하며, 원격 개발은 로그인을 유지하고 `KSI_CCTV_WEB_SESSION_TTL_SEC`(최대 30일)로 재로그인 주기만 조정한다.
종료는 `Ctrl+C`다.

### 웹 콘솔(Next.js) + 영상 게이트웨이 — Docker 개발/랩

```bash
# 개발/랩 .env에 엔진 키 설정: KSI_CCTV_API_KEY
# 엔진(데몬) + 웹 콘솔 + 영상 게이트웨이 동시 기동
docker compose --profile web up --build
# 웹 콘솔: http://localhost:3000  (로그인 후 카메라 페이지에서 라이브 영상)
#   - 엔진 API/헬스(8081)와 게이트웨이(8888)는 호스트에 노출하지 않고 compose 네트워크 내부에서 연동
#   - 운영 배포에는 사용하지 않음. Appliance Caddy HTTPS endpoint를 사용
```

> 웹 콘솔 라이브 영상은 카메라(또는 RTSP 소스)가 게이트웨이에 발행/풀될 때 재생된다. 카메라 없이
> 검증하려면 `ffmpeg`로 샘플을 합성 RTSP로 발행하면 된다(`docs/implementation-plan.md` P5 검증 참조).

웹 콘솔 인증은 선택 사항이다. `KSI_CCTV_WEB_PASSWORD`를 설정하면 환경변수 비밀번호가 항상 우선하며 UI 관리
보호는 사용할 수 없고 값을 UI에서 변경할 수 없다. 비밀번호를 설정하지 않은 초기 상태의 콘솔은 열려 있다. 이 경우에는
Tailnet 또는 HTTPS로 보호된 운영자 전용 네트워크에서만 접근을 제공한다. Settings에서 최초 보호를 설정하려면 원격
관리자 선점을 막기 위해 별도의 32자 이상 `KSI_CCTV_WEB_SETUP_TOKEN`이 필요하며 설정 후 제거한다. 로컬 UI 관리 인증을 초기화하려면
`docker compose --profile web exec ksi-cctv-web node /usr/local/bin/ksi-cctv-reset-local-auth --confirm`을 실행한다.
환경 비밀번호가 없으면 초기화 후 콘솔은 다시 열린다.

## 설정 파일

- `config/default.yaml`: 모델 경로, 임계값, 알람, 디바이스 설정
- `config/cameras.json`: CCTV 채널 설정 샘플 (`${CCTV_CAM_001_URL}` 환경변수 확장 지원)
- `config/model_profiles.yaml`: 다중 모델 프로파일 및 Appliance model contract
- `config/event_policies.yaml`: 이벤트별 판단 정책
- `config/camera_profiles.yaml`: 카메라별 도메인/이벤트/모델 선택
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
- `tests/`: 유닛/통합/Appliance contract 테스트. 정확한 결과와 skip/audit evidence는 release record에서 관리한다.

## 개발 환경

- Python 3.10+
- NVIDIA GPU + CUDA 환경 권장
- DGX Spark 실증 기준
- 개발 보조 모델: Qwen 3.6 27B Dense 권장

## 현재 상태

- Python, frontend, browser 결과는 frozen release commit에서 `bash scripts/verify_release_candidate.sh --strict`로 생성한 release record만 사용한다.
- Playwright는 인증, LiveWall, 카메라 등록/작업, 이벤트 export, 모델 제어, commissioning/handoff mock workflow를 검증한다. 실제 camera, inference, ARM64 qualification 증거는 아니다.
- 내부 runner gate: `bash scripts/verify_release_candidate.sh --strict` (`docs/internal-ci.md`)
- 엔진: policy-mode (MultiModelRegistry + EventPolicyEngine) + 데몬에 FastAPI in-process(탐지+API 단일 서비스)
- 설치형 제품화: Next.js 웹 콘솔 + MediaMTX HLS gateway. pose/ST-GCN과 bounded local VLM verification은 qualification 전 개발 중이며 release capability가 아니다.
- 구현된 AI: `general_yolo`, `ppe_yolo`, `fire_smoke_yolo`, ByteTrack `line_crossing`/`loitering` tracking

## 다음 단계

1. DGX Spark에서 required model의 실제 `CUDAExecutionProvider` inference를 검증
2. signed ARM64 Appliance image/bundle을 생성하고 실제 RTSP/HLS/복구 E2E를 검증
3. 승인하려는 동시 채널 수의 soak, 특히 아직 미입증인 16채널의 evidence를 확보하고 모델 라이선스/정확도를 확정

## 문서

### 제품 방향 로드맵 (설계 단계 — 구현 전)
| 문서 | 내용 |
|---|---|
| `docs/architecture-target.md` | **타깃 아키텍처 이력** — 완료된 Next.js 전환과 후순위 후보를 구분 |
| `docs/roadmap-capability.md` | **능력 로드맵** — open-vocab(YOLO-World 등) 텍스트 즉시탐지 + VLM 상황탐지 통합 |
| `docs/roadmap-packaging.md` | **후순위 패키징 후보** — 현재 release 범위 밖인 Windows/macOS 계획 |
| `docs/ai-architecture-redesign.md` | **Advanced-AI v1 AI 방향** — pose/ST-GCN과 bounded local VLM verification의 개발·qualification 범위 및 후순위 제안 |

### 운영·구현 문서
| 문서 | 내용 |
|---|---|
| `docs/configuration.md` | 환경변수·설정파일 레퍼런스 |
| `docs/operations.md` | 24h 운영·로그 관리·헬스체크 |
| `docs/troubleshooting.md` | 장애 진단 및 해결 가이드 |
| `docs/delivery-checklist.md` | 납품용 설치 체크리스트 + 시연 체크리스트 |
| `docs/deployment-guide.md` | 설치·배포·systemd 상세 가이드 |
| `docs/installer-camera-onboarding.md` | 설치 업자용 ONVIF/수동 등록·가상 CCTV·현장 인수 절차 |
| `docs/offline-appliance.md` | 서명된 Ubuntu 오프라인 Appliance 빌드·설치·업그레이드·롤백 |
| `docs/internal-ci.md` | GitHub 비의존 internal runner release gate |
| `docs/architecture.md` | 목표 아키텍처 (policy-mode) |
| `docs/model-management.md` | 모델 관리 전략 (VLM provider 결정 포함) |
| `docs/model-sourcing.md` | **공개 사전학습 모델 소싱 가이드** (PPE/화재 추천·AGPL 경고·교체 절차) |
| `docs/event-policy-engine.md` | 이벤트 정책 엔진 설계 |
| `docs/custom-model-training.md` | 커스텀 모델 학습 전체 가이드 |
| `docs/phase-cd-readiness.md` | **Phase C/D 실행 runbook** — 실환경 검증·커스텀 모델 정량 합격 기준 |
| `docs/development-roadmap.md` | 단계별 개발 로드맵 |
| `docs/completion-plan.md` | 납품 완성 계획 (체크박스 현행화) |

## License

애플리케이션 코드는 MIT License입니다. 제3자 모델 가중치와 컨테이너 구성요소에는 각 원저작자의
라이선스가 적용됩니다. 현재 공개 YOLO 계열 가중치는 폐쇄형 상용 납품 전에
`docs/model-sourcing.md`의 AGPL/Enterprise License 검토를 완료해야 합니다.
