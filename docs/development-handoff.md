# KSI-CCTV 개발 인계 — 새 컴퓨터에서 이어서 개발하기

최종 갱신: 2026-10-29
저장소: <https://github.com/khakisketch/KSI-CCTV>
기본 브랜치: **`master`** (GitHub default branch. `main`이 아니다.)

> 이 문서는 "다른 컴퓨터에서 이어서 개발"하기 위한 실전 인계 문서다.
> 제품 범위·출고 판단의 SSOT는 `docs/release-gate.md`, 현재 마일스톤은
> `docs/superpowers/product-state.md`다. 이 문서는 그 둘을 대체하지 않는다.

---

## 0. 5분 요약

- **코드/문서 상태**: 작업 트리 clean, `master`가 `origin/master`와 동일. 미푸시 커밋 없음.
- **2026-10-29 로컬 기준선**:
  - Python: `1217 passed, 5 skipped` (`pytest`, 49 subtests)
  - `bash scripts/test.sh`: ruff lint·coverage(86.53%)·mypy 차단/점검 통과
  - Frontend: `vitest 351 passed` (63 files), `eslint` 통과, `next build` 통과
  - 정책 모드 E2E: `scripts/e2e_smoke_test.py --policy-mode` exit 0
  - 실행 환경: Python 3.12.3 · Node 24.19.0 · npm 11.17.0 · Docker 29.6.2
- **출고 상태**: M7 진행 중. 소프트웨어 strict gate는 코드 리비전 `459943b`(2026-09-23)에서
  `passed:true`·`strict:true`·`clean_tree:true`로 통과했다. 이후 `a89ef2d`(웹훅 UX)와 문서 커밋이
  추가됐으므로 **출고 전 strict gate 재실행이 필요**하다.
- **남은 출고 증거**: DGX Spark ARM64 실카메라/HLS·채널 soak·전원/복구, 법률 승인,
  4-image 서명 provenance.
- **새 컴퓨터**: clone → venv/의존성 → `.env` → 모델 → 데모 실행 → 테스트. 아래 §2를 그대로 따른다.
- **Git에 없는 것**: `.env`, `models/*.onnx`, 현장 설정, `data/`, `logs/`, `reports/`, `dist/`.
  §3 표를 보고 "재생성"과 "안전하게 복사"를 구분한다.

---

## 1. 저장소와 브랜치

```bash
git clone https://github.com/khakisketch/KSI-CCTV.git
cd KSI-CCTV
git switch master
git pull --ff-only
git log -1 --oneline
```

- GitHub 기본 브랜치는 `master`다. `main`으로 rename하지 않았다(기존 링크·문서·릴리스 기록 호환).
- 기능 작업은 `master`에서 분기한다: `feat/...`, `fix/...`, `docs/...`.
- `master` 직접 push가 가능하지만, 위험한 변경은 PR로 리뷰하는 편을 권장한다.
- 강제 push(`--force`)는 하지 않는다. 출고 기준선 커밋 해시가 증적에 기록된다.
- 커밋 메시지는 기존 관례를 따른다:
  `feat(scope): ...`, `fix(scope): ...`, `docs(scope): ...`, `test(scope): ...`, `refactor(scope): ...`.

---

## 2. 새 컴퓨터 셋업 (권장: 수동 설치)

### 2.1 준비물

| 항목 | 권장/검증 버전 | 비고 |
|---|---|---|
| OS | Ubuntu 22.04/24.04 (x86_64 개발) | 운영 대상은 Ubuntu ARM64 Appliance |
| Python | 3.12 (최소 3.10) | `python3 --version` |
| Node.js | 22 LTS 이상 (24.19 검증) | Next.js 16 요구 |
| npm | 11.x | `npm ci` 사용 |
| Docker + Compose v2 | 선택 | HLS 게이트웨이·Compose 실행에 사용 |
| ffmpeg | 선택 | 데모 라이브 스트림 송출에 사용 |
| Ollama | 선택 | VLM 2차 검증. 기본 비활성 |
| 디스크 | 20GB+ 여유 | PyTorch/ultralytics 의존성이 큼 |
| RAM | 8GB+ | 모델·브라우저 동시 실행 기준 |

필수 시스템 패키지(예):

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip build-essential \
  libgl1 libglib2.0-0 ffmpeg
```

> GPU(DGX/CUDA)는 개발 성능 검증용이며, 운영 지원 범위 증명이 아니다.
> CPU만으로도 데모·테스트는 동작한다.

### 2.2 코드와 Python 의존성

```bash
cd KSI-CCTV
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt

# ONVIF 카메라 탐색/등록까지 개발할 때만
./.venv/bin/python -m pip install -r requirements-onvif.txt

cd frontend
npm ci
cd ..
```

> `bash scripts/install.sh`도 있지만 **개발/랩 전용**이며 `sudo apt-get`과 systemd 서비스 등록을
> 시도한다. 공용/새 노트북에서는 위 수동 설치를 권장한다.

### 2.3 `.env` 만들기 (Git에 절대 커밋 금지)

```bash
cp .env.example .env
chmod 600 .env
```

최소한 아래 값을 채운다.

```dotenv
KSI_CCTV_API_KEY=<충분히 긴 랜덤 값>
KSI_CCTV_WEB_PASSWORD=<콘솔 로그인 비밀번호>
KSI_CCTV_WEB_SECRET=<32자 이상 독립 랜덤 값>
KSI_CCTV_CAPTURE_KEY=<Fernet 키>
CCTV_CAM_001_URL=./data/sample.mp4
```

생성 예:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

- `.env`는 `.gitignore` 대상이다. 다른 컴퓨터로 옮길 때는 Git이 아니라
  비밀번호 관리자·암호화 압축·`scp` 같은 안전한 경로를 쓴다.
- 웹 비밀번호를 비우면 콘솔은 공개 상태로 시작한다. 최초 보호 설정에는 별도
  `KSI_CCTV_WEB_SETUP_TOKEN`(32자 이상)이 필요하다. 자세한 내용은 `docs/configuration.md`.
- RTSP/SMTP/Fernet 값은 로그·문서·스크린샷에 남기지 않는다.

### 2.4 모델 준비 (Git에 없음)

`models/*.onnx`는 저장소에 커밋되지 않는다(용량·AGPL 라이선스 검토). 새 컴퓨터에서 재생성하거나
기존 컴퓨터에서 안전하게 복사한다.

```bash
# 1) 기본 COCO 모델 (yolo26n.pt → models/yolo26.onnx)
./.venv/bin/python scripts/prepare_yolo26.py --model yolo26n.pt --device cpu

# 2) 안전장비/화재·연기 모델 (인터넷 필요)
./.venv/bin/python scripts/fetch_safety_models.py --all

ls -lh models/*.onnx
```

정상 기준(대략):

| 파일 | 크기 | 용도 |
|---|---|---|
| `models/yolo26.onnx` | ~4.8MB | 기본 COCO 탐지 |
| `models/yolo26_ppe.onnx` | ~5.9MB | 안전모/조끼 |
| `models/yolo26_fire_smoke.onnx` | ~5.1MB | 화재/연기 |

- 다운로드가 막히면 기존 컴퓨터의 `models/`에서 위 파일을 복사한다.
- `yolov8s-world.pt`(open-vocab)는 기본 비활성(`enabled:false`)이다. 필요할 때만 별도 확보한다.
- 모델 라이선스는 `docs/model-sourcing.md`를 따른다. 폐쇄형 상용 납품 전 Ultralytics
  AGPL/Enterprise 검토가 필요하다.

### 2.5 샘플 영상 (데모용, Git에 없음)

```bash
./.venv/bin/python scripts/create_sample_video.py
# → data/sample.mp4 (합성 테스트 패턴)
```

### 2.6 실행 — 로컬 풀스택

```bash
set -a; source .env; set +a
KSI_CCTV_DEMO=1 bash scripts/run_web.sh
```

- 웹 콘솔: <http://127.0.0.1:3100>
- 엔진 API: `http://127.0.0.1:8181` (loopback)
- HLS 게이트웨이: `127.0.0.1:8910`, RTSP: `127.0.0.1:8954`
- `run_web.sh`는 프로덕션 빌드를 서빙한다. 프론트를 수정하며 HMR이 필요하면:

```bash
KSI_CCTV_WEB_DEV=1 KSI_CCTV_DEMO=1 bash scripts/run_web.sh
```

- `.env`에 자격증명이 없으면 launcher가 임의 값을 생성하고 `0600` 임시 파일 경로를 출력한다.
  종료 시 삭제된다. 값을 화면에 출력하지 않는다.
- 인증 bypass(`KSI_CCTV_DEV_AUTH_BYPASS=1`)는 `KSI_CCTV_WEB_DEV=1` + loopback에서만 허용된다.
  `next dev`를 직접 띄우면 bypass가 적용되지 않는다.
- Docker Compose로 한 번에 띄우는 개발/랩 경로:

```bash
docker compose --profile web up --build
# 웹 콘솔: http://localhost:3000 (root .env 자동 적용)
```

### 2.7 설치 검증

```bash
# Python 단위/통합 테스트 (반드시 venv 사용)
./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider

# lint + 커버리지 + shell syntax (+ mypy advisory)
bash scripts/test.sh

# 프론트엔드
cd frontend
npm run lint
npm test
npm run build
cd ..

# 엔진/모델/런타임 상태
./.venv/bin/python scripts/healthcheck.py --json

# 정책 모드 E2E 스모크 (모델 준비 후)
./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode
```

기대 기준선(2026-10-29): `1217 passed, 5 skipped`, 프론트 `351 passed`, lint/build 통과.
테스트는 `-B -p no:cacheprovider`로 실행해 `__pycache__`/캐시가 release gate clean-worktree
전제를 깨지 않게 한다.

---

## 3. Git에 없는 것 — 재생성 vs 안전하게 복사

| 경로 | Git | 새 컴퓨터에서 |
|---|---|---|
| `.env` | ignored | **재생성** 권장. 기존 값을 옮길 때는 암호화/비밀번호 관리자 사용 |
| `models/*.onnx`, `*.pt` | ignored | `scripts/prepare_yolo26.py`·`fetch_safety_models.py`로 재생성 또는 `scp` 복사 |
| `data/sample.mp4` | ignored | `scripts/create_sample_video.py`로 재생성 |
| `data/*.db`, `data/captures/`, `data/outbox/` | ignored | 런타임 데이터. 데모는 재생성 가능, 현장 데이터는 승인 후 암호화 복사 |
| `logs/` | ignored | 재생성. 보존이 필요하면 별도 백업 |
| `config/cameras/`, `config/cameras.local.json` | ignored | 현장 카메라 등록 정보. 콘솔에서 재등록하거나 안전하게 복사 |
| `config/webhooks.yaml` | ignored | 현장 Webhook/HMAC. **비밀 포함** — 안전하게 복사 |
| `reports/` | ignored | 릴리스 증적. 출고 기록을 이어가려면 복사 |
| `dist/` | ignored | ARM64 onnxruntime 휠 등 빌드 산출물. 재빌드 또는 복사 |
| `.venv/`, `frontend/node_modules/`, `frontend/.next/` | ignored | 재생성 |
| `design-previews/` | ignored | 로컬 디자인 산출물. 인계에 필수 아님 |

> `reports/release-candidate/`의 strict gate 증적은 Git에 없다. 출고 승인 기록을 다른 컴퓨터로
> 가져가려면 해당 디렉터리를 별도 보관한다(개인정보·비밀 포함 여부 확인).

---

## 4. 현재 제품 상태와 다음 작업

### 완료

- M1–M6: 탐지 레지스트리·정책 엔진·SafetyPipeline·모델 업로드·Next.js 콘솔·구조화·레거시 이관.
- P1 RBAC(관리자/관제원/뷰어), P2 Webhook, P3 정기 LLM 보고.
- M7-T1 ARM64 CUDA 휠/GPU 자격, M7-T2 VLM 자격(`qwen3-vl:2b-instruct`).

### 진행 중 (M7 출고 증거)

1. **실장비 검증**: DGX Spark ARM64에서 실제 RTSP/HLS, 전원/복구, 채널별 soak.
2. **법률·라이선스**: 모델 가중치·컨테이너 구성요소의 배포 라이선스 승인.
3. **서명 provenance**: 4-image 번들 서명·attestation을 승인된 signer/HSM에서 수행.
4. **strict gate 재실행**: 현재 HEAD(문서 커밋 포함)에서 `bash scripts/verify_release_candidate.sh --strict`.
   마지막 attestation은 `459943b` 기준이며, 이후 `a89ef2d` 프론트 변경이 있다.

### 백로그(비차단)

- `requestActor` 이중 읽기(TOCTOU) — 비활성 전환 순간 감사 actor가 `system`으로 기록될 수 있음.
- proxy 수준 비활성/삭제 세션 회귀 테스트 갭.
- theft/weapons/smoking·production open-vocab — 모델·라이선스·정확도 데이터 의존.

우선순위·수용 기준은 `docs/superpowers/product-state.md`와 `docs/release-gate.md`를 따른다.

---

## 5. 개발 워크플로

```bash
git switch master
git pull --ff-only
git switch -c feat/my-change

# ... 구현/테스트 ...

git add -A
git commit -m "feat(scope): 한 줄 요약"
git push -u origin feat/my-change
```

- PR 전 최소 게이트: `bash scripts/test.sh` + `cd frontend && npm run build`.
- 화면 변경 시 데스크톱과 모바일(390px), 빈/오류/로딩 상태를 실제 브라우저에서 확인한다.
- 한글 줄바꿈은 `word-break: keep-all` 관례를 따른다.
- Release gate는 clean worktree를 전제로 한다. 실행 전 `git status`를 확인한다.
- AI 에이전트로 이어서 개발할 때는 `CLAUDE.md`의 SSOT 표와 `docs/release-gate.md`를 먼저 읽는다.
  `.ai/*`와 `docs/codex-handoff.md`는 **역사 기록**이므로 현재 작업 목록으로 쓰지 않는다.

---

## 6. 자주 막히는 지점

| 증상 | 원인/해결 |
|---|---|
| `NameError: name 'RTSPCapture' is not defined` (pytest collection) | 시스템 `python3`에 `cv2` 등 의존성이 없음. `./.venv/bin/python -B -m pytest ...` 사용 |
| `cv2` import 시 `libGL.so.1` 오류 | `sudo apt-get install -y libgl1 libglib2.0-0` |
| `npm run build` 실패 | Node 22+ 확인. `cd frontend && npm ci` 재실행 |
| 포트 충돌 | `KSI_CCTV_WEB_PORT`·`KSI_CCTV_ENGINE_PORT`·`KSI_CCTV_GW_HLS_PORT`·`KSI_CCTV_GW_RTSP_PORT`로 변경 |
| HLS 라이브가 안 보임 | 헤드리스 Chromium은 H.264 미탑재. 실제 Chrome/Firefox로 확인 |
| `run_web.sh`가 non-loopback bind 거부 | 개발 launcher는 평문 HTTP. 격리망에서만 `KSI_CCTV_INSECURE_REMOTE_DEV=1` |
| Docker 권한 오류 | `sudo usermod -aG docker $USER` 후 재로그인(또는 `sudo docker ...`) |
| 모델이 없어 이벤트가 안 뜸 | `models/*.onnx` 확인. §2.4 재생성 |
| VLM 검증이 동작 안 함 | 기본 `enabled:false`. Ollama 모델·`runtime_qualified`·`docs/release-gate.md` 자격 절차 확인 |
| strict gate가 clean-worktree에서 실패 | `__pycache__`·`.coverage`·ignored 산출물 확인. 테스트는 `-B -p no:cacheprovider` |

---

## 7. 인계 체크리스트

### 기존 컴퓨터(이쪽)

- [x] 작업 트리 clean, `master` push 완료
- [x] Python/프론트 테스트·lint·build 기준선 확인(2026-10-29)
- [ ] `.env`를 안전한 경로로 새 컴퓨터에 전달(또는 새로 생성)
- [ ] `models/*.onnx` 복사 여부 결정(재생성 가능)
- [ ] `reports/release-candidate/` 등 출고 증적이 필요하면 별도 보관
- [ ] 현장 `config/cameras/`·`config/webhooks.yaml`이 있으면 승인 후 암호화 복사

### 새 컴퓨터

- [ ] clone + `git switch master` + `git pull --ff-only`
- [ ] `.venv` + Python/프론트 의존성 설치
- [ ] `.env` 작성(`chmod 600`)
- [ ] 모델 준비 + `data/sample.mp4` 생성
- [ ] `pytest`·`npm test`·`npm run build` 통과
- [ ] `KSI_CCTV_DEMO=1 bash scripts/run_web.sh`로 콘솔 확인
- [ ] `docs/superpowers/product-state.md`·`docs/release-gate.md` 읽고 다음 작업 선택

---

## 8. 문의

주식회사 케이에스아이 (KSI Corporation) · 대표번호 **043-288-4860**
