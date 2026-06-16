> **상태: 로드맵 초안(설계 단계 — 구현 전)** · 작성 2026-06-16 · 근거: multi-agent 리서치(실 코드 read + 웹 검증)
> 관련 문서: [능력 로드맵](roadmap-capability.md) · [타깃 아키텍처](architecture-target.md)
> ⚠️ 이 문서는 방향·후보·단계를 정리한 것으로, 표기된 FPS·라이선스·EP 가용성 등은 구현 착수 시 "사실 검증 플래그"의 [검증필요] 항목을 실측·확정해야 한다.

# 로드맵: 크로스플랫폼 AI 런타임 + 설치형 패키징

> 대상 파일(초안 경로): `docs/roadmap-packaging.md`
> 작성 근거: 실제 레포(`requirements.txt`, `Dockerfile`, `docker-compose.yml`, `scripts/install.sh`, `scripts/docker-entrypoint.sh`, `systemd/ksi-cctv.service`, `src/run.py`, `src/utils/health.py`, `src/detection/yolo_detector.py`, `src/vlm/ollama_provider.py`, `src/vlm/vlm_inference.py`, `src/detection/model_registry.py`) Read + 2026-06 웹 검증.

## 0. 현 상태 진단 (실 레포 기준)

KSI-CCTV는 **DGX Spark(ARM64 + CUDA) 단일 타깃**에 최적화돼 있고, 설치형 멀티-OS 납품에 필요한 런타임 추상화가 아직 없다. 제품 목표(macOS·Linux·Windows·Windows ARM64 전부 지원, 설치형 오프라인 납품, 비기술 고객 더블클릭)와 현 코드 사이의 구체적 gap:

| 영역 | 현 구현(파일·라인) | gap |
|---|---|---|
| EP 선택 | `yolo_detector.py:256-261` — `device in {"cuda","auto"}`면 `CUDAExecutionProvider`, 아니면 무조건 `CPUExecutionProvider`만 append | DirectML / CoreML / QNN / TensorRT 분기 **전무**. Windows GPU·Apple Silicon·Windows ARM64 NPU에서 전부 CPU로만 동작 |
| device 기본값 | `model_registry.py:209` `device=profile.get("device","cuda")`, `config/default.yaml:73` `primary: "cuda"` | 기본값이 `cuda` 하드코딩 — 비-NVIDIA 호스트에서 매번 CPU 폴백 로그만 남기고 가속 미사용 |
| 휠 설치 | `requirements.txt:9` `onnxruntime==1.20.1`(CPU), `install.sh:58-64`·`Dockerfile:47-50`이 nvidia-smi/ENABLE_GPU 감지 시 `onnxruntime-gpu`로 교체 | onnxruntime-gpu는 **ARM64 휠 부재**(이미 주석에 명시) → DGX에서도 CPU 폴백. Windows/macOS 가속 패키지(`onnxruntime-directml`/`onnxruntime-qnn`/CoreML) 설치 경로 없음 |
| 패키징 | Docker/compose + Linux systemd(`install.sh`) | Windows 서비스·인스톨러·PyInstaller·오프라인 번들 **전무**. install.sh는 `apt-get`/`systemctl` 의존(Linux 전용) |
| VLM | `ollama_provider.py` + `vlm_inference.py:create_vlm_provider` (Ollama 단일, 외부 API는 `provider` 분기에 미구현) | "AI는 로컬 또는 외부서버 선택" 목표 중 **외부 API provider 미구현**. Windows ARM은 Ollama가 CPU-only(가속 없음) |

**핵심 한 줄:** 탐지 코어는 ONNX Runtime이라 이론상 어디서나 돌지만, **EP 선택 로직이 CUDA/CPU 2-way라 멀티-OS 가속이 0**이다. 패키징은 Linux/Docker만 존재해 한국 CCTV 용역(Windows·더블클릭)에 바로 못 낸다.

---

## 1. onnxruntime Execution Provider 플랫폼 매트릭스 (2026-06 검증)

### 1.1 OS × 아키텍처 × EP 가용성

| OS / Arch | CUDA | TensorRT | DirectML | CoreML | QNN(NPU) | CPU | pip 패키지(권장) |
|---|---|---|---|---|---|---|---|
| **Windows x64** | O(NVIDIA) | O(NVIDIA) | **O**(DX12: NVIDIA/AMD/Intel) | - | △(x64 QNN) | O | `onnxruntime-directml`(범용 GPU) 또는 `onnxruntime-gpu`(NVIDIA) |
| **Windows ARM64** | - | - | **X** (DirectML ARM64 미지원) | - | **O** (`onnxruntime-qnn`, Snapdragon NPU) | O | `onnxruntime-qnn`(NPU) / 없으면 `onnxruntime`(CPU) |
| **Linux x64** | O | O | - | - | △ | O | `onnxruntime-gpu`(NVIDIA) / `onnxruntime`(CPU) |
| **Linux ARM64**(DGX Spark·Jetson) | △ **공식 휠 없음** | △ | - | - | - | O | `onnxruntime`(CPU 기본) / GPU는 소스빌드·커뮤니티 휠·NVIDIA 컨테이너 |
| **macOS (Apple Silicon)** | - | - | - | **O** (CoreML EP, ANE/GPU) | - | O | `onnxruntime`(CoreML EP가 기본 패키지에 포함) |
| **macOS (Intel)** | - | - | - | △(CoreML 제한) | - | O | `onnxruntime`(CPU) |

범례: O=지원·휠 있음, △=제한적/조건부(소스빌드 또는 비공식 휠), X=미지원, -=비해당.

### 1.2 검증된 핵심 사실 (과장 배제)

- **DirectML는 ARM64 미지원** — Windows ARM64(Snapdragon Surface 등)에서 GPU 가속을 DirectML로 못 받는다. NPU는 `onnxruntime-qnn`(Windows ARM64 cp312/cp313 휠 존재)으로만. ([onnxruntime DirectML 문서])
- **onnxruntime-gpu는 ARM64 공식 휠 부재** — DGX Spark(ARM64)·Jetson에서 `pip install onnxruntime-gpu` 실패(레포 주석이 이미 경험). 해법: ① NVIDIA NGC 컨테이너(현 Dockerfile 방향) ② 소스빌드 ③ 커뮤니티/Ultralytics asset 휠. ([onnxruntime issue #21232, #24380])
- **DirectML는 NVIDIA/AMD/Intel GPU 공통(DX12)** — Windows x64 설치형에서 GPU 벤더 불문 단일 패키지(`onnxruntime-directml`)로 가속 가능. NVIDIA 전용 최고속은 CUDA/TensorRT.
- **CoreML EP는 기본 onnxruntime 패키지에 내장**(macOS) — 별도 GPU 패키지 불필요, Apple Neural Engine/GPU 사용. ([onnxruntime EP 문서])
- **onnxruntime-directml·onnxruntime-gpu·onnxruntime-qnn·onnxruntime는 동일 import 네임스페이스**(`import onnxruntime`)라 **동시 설치 금지** — 현 레포가 CPU↔GPU 교체로 이미 처리하는 패턴을 4-way로 확장해야 함.

### 1.3 EP 선택/폴백 전략 (코드 변경 설계)

**원칙:** "설치된 패키지가 노출하는 provider를 런타임에 감지해 우선순위대로 시도, 실패 시 다음으로 폴백" — 휠 종류와 무관하게 `ort.get_available_providers()` 결과만 신뢰. 현 `yolo_detector._init_session`을 다음으로 교체:

```python
# device: "auto"(기본 권장) | "cuda" | "directml" | "coreml" | "qnn" | "tensorrt" | "cpu"
PREFERENCE = {
    "auto": ["TensorrtExecutionProvider", "CUDAExecutionProvider",
             "DmlExecutionProvider", "CoreMLExecutionProvider",
             "QNNExecutionProvider", "CPUExecutionProvider"],
    "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
    "directml": ["DmlExecutionProvider", "CPUExecutionProvider"],
    "coreml": ["CoreMLExecutionProvider", "CPUExecutionProvider"],
    "qnn":  ["QNNExecutionProvider", "CPUExecutionProvider"],
    "cpu":  ["CPUExecutionProvider"],
}
available = set(ort.get_available_providers())
providers = [p for p in PREFERENCE[device] if p in available] or ["CPUExecutionProvider"]
```

추가 고려:
- **fp16 분기:** CUDA/TensorRT는 fp16 유효, CPU/DirectML(일부)·CoreML은 fp32 권장. 현 `input_dtype`은 ONNX 입력 타입으로 자동 판정하므로 호환되나, export 시 half=True 모델을 CPU 호스트에 깔면 느려짐 → **fp32/fp16 모델 둘 다 번들**하거나 device별 권장 모델 안내.
- **device="auto"를 신규 기본값으로** (`config/default.yaml` `primary: "auto"`, `model_registry` 기본 `"auto"`) — 호스트가 알아서 최적 EP 선택. DGX는 CUDA 컨테이너에선 CUDA, 맨몸 ARM64에선 CPU.
- **첫 추론 워밍업 + 폴백 로깅:** EP 초기화는 성공해도 첫 run에서 실패하는 경우(DirectML shape 등) 있음 → try/except로 CPU 재생성 1회.

---

## 2. VLM(Ollama) 크로스플랫폼 + 외부 API 옵션

### 2.1 Ollama 플랫폼 매트릭스 (2026-06 검증)

| 플랫폼 | Ollama 지원 | GPU 가속 |
|---|---|---|
| macOS (Apple Silicon) | O | **Metal 자동**(M1~M4) |
| Linux x64 | O | CUDA/ROCm/Vulkan |
| Linux ARM64 | O | CUDA(드라이버 있으면) |
| Windows x64 | O | CUDA/ROCm |
| **Windows ARM64** | O(네이티브) | **CPU-only**(가속 라이브러리 미지원) |

→ **Windows ARM64는 VLM이 CPU-only**라 Qwen3-VL 같은 무거운 VLM은 실사용 부적합. 이 플랫폼은 **(a) VLM 비활성(1차 YOLO만) (b) 외부 API VLM (c) 외부서버 Ollama 가리키기** 셋 중 택. 현 코드는 (a)는 이미 graceful(`create_vlm_provider`가 None 반환 시 1차만), (b)·(c)는 추가 필요.

### 2.2 외부 API / 외부서버 옵션 (제품 목표 "AI는 로컬 또는 외부서버 선택")

현 `create_vlm_provider`(`vlm_inference.py:68`)는 `provider: ollama`만 구현. 확장:
- **외부 Ollama 서버**: 이미 `base_url`만 바꾸면 됨(코드 변경 불필요, 문서/UI 노출만). 단 PII(얼굴) 전송 경고 + TLS 권장.
- **클라우드 VLM provider 추가**: `VLMProvider` ABC 구현체 추가(`anthropic`/`openai` 등). **단 ADR-0001이 "외부 SaaS 미사용·PII 외부전송 금지"를 명시** → 기본 OFF, 명시적 opt-in + 동의 게이트 + base_url 스킴검증(이미 존재)과 동급의 가드 필요. 이 결정은 product owner 확인 필요(open question).
- **권장 디폴트(플랫폼별):**
  - Windows ARM64 / 저사양: VLM OFF(1차만) 또는 외부서버 Ollama
  - macOS/Win x64 GPU / Linux: 로컬 Ollama
  - 외부전송 허용 현장만: 클라우드 API

---

## 3. 설치형 패키징 옵션 비교

### 3.1 후보 비교표

| 방식 | 대상 OS | 장점 | 단점 | 비기술 고객 | GPU |
|---|---|---|---|---|---|
| **Docker + compose**(현존) | Linux 우선·Win/mac(Docker Desktop) | 환경 격리·재현성·현 자산 재사용·GPU 패스스루(Linux 네이티브, Win은 WSL2) | Docker Desktop 설치 필요·라이선스(기업)·Win ARM64 GPU 제약·비기술 고객엔 무거움 | △ | Linux O / Win(WSL2) O / mac X(GPU) |
| **Linux systemd**(현존) | Linux | 네이티브·경량·무인 24/7·watchdog(이미 구현) | Linux 전용 | X(IT용) | O |
| **Windows Service(NSSM)** | Windows | 무인 부팅 자동시작·서비스 관리(GUI 없는 데몬에 적합)·`src/run.py` 그대로 ExecStart 대응 | NSSM 번들 필요·서비스 등록 권한 | △(인스톨러로 감춤) | DirectML O |
| **PyInstaller(onedir)** | 전 OS | Python 미설치 호스트에 배포·인터프리터 번들 | onnxruntime/opencv/torch DLL 수집 까다로움(`--collect-all onnxruntime` 필수)·용량 큼·torch 포함 시 GB급·플랫폼별 별도 빌드 | O | EP 포함 빌드 가능 |
| **Inno Setup / MSI 인스톨러** | Windows | 더블클릭 설치·시작메뉴·서비스 등록·언인스톨러·전문적 외관 | Windows 전용·서명 필요(SmartScreen) | **O(최적)** | - |
| **네이티브 패키지(.deb/.pkg)** | Linux/mac | OS 표준 설치 경험 | 빌드 파이프라인·서명(mac notarization) | O | - |

### 3.2 권장 (고객 유형별)

- **한국 CCTV 용역 / 비기술 Windows 고객(주력):**
  **PyInstaller(onedir) + NSSM(Windows Service) + Inno Setup 인스톨러** 조합.
  - PyInstaller로 `src/run.py`(데몬)와 대시보드를 onedir 번들 → 고객 PC에 Python 불필요.
  - NSSM으로 데몬을 Windows Service 등록(부팅 자동시작·무인). `src/run.py`는 sd_notify가 NOTIFY_SOCKET 없으면 no-op이라 그대로 동작(`run.py:50`).
  - Inno Setup이 더블클릭 설치 + 모델/의존성 번들 + 서비스 등록 + 시작메뉴 + 언인스톨 + (선택)Ollama 설치 안내.
  - GPU: `onnxruntime-directml` 번들(NVIDIA/AMD/Intel 공통). Windows ARM64 빌드는 `onnxruntime-qnn` 또는 CPU.
- **IT 보유 / Linux 사이트:** **현 Docker compose + GPU 패스스루** 유지(재현성·현 자산). 또는 install.sh+systemd(네이티브). 둘 다 이미 존재 — 문서화·강건화만.
- **macOS 현장(드뭄):** PyInstaller(.app) 또는 brew + launchd. CoreML EP 기본 포함이라 추가 가속 패키지 불필요.

### 3.3 OS × EP × 설치방식 종합 매트릭스

| OS / Arch | 권장 EP(가속) | 권장 onnxruntime 휠 | VLM 기본 | 권장 설치방식 | 무인 자동시작 |
|---|---|---|---|---|---|
| Windows x64 (NVIDIA) | CUDA→DML→CPU | `onnxruntime-gpu` 또는 `onnxruntime-directml` | 로컬 Ollama(CUDA) | Inno Setup + NSSM | NSSM Service |
| Windows x64 (AMD/Intel) | DirectML→CPU | `onnxruntime-directml` | 로컬 Ollama / 외부 | Inno Setup + NSSM | NSSM Service |
| Windows ARM64 | QNN(NPU)→CPU | `onnxruntime-qnn` / `onnxruntime` | OFF 또는 외부서버 | Inno Setup(ARM64) + NSSM | NSSM Service |
| Linux x64 (NVIDIA) | TensorRT→CUDA→CPU | `onnxruntime-gpu` | 로컬 Ollama | Docker / systemd | systemd / Docker restart |
| Linux ARM64 (DGX Spark) | CUDA(컨테이너) / CPU | NGC 컨테이너 / `onnxruntime`(CPU) | 로컬 Ollama | **Docker(NGC GPU)** | Docker / systemd |
| macOS Apple Silicon | CoreML→CPU | `onnxruntime`(CoreML 내장) | 로컬 Ollama(Metal) | PyInstaller(.app)/brew | launchd |

---

## 4. 오프라인 설치 · 저사양 폴백 · GPU 패스스루 · 업데이트/롤백

### 4.1 오프라인 설치(모델·의존성 번들) — 설치형 핵심
- **Python 의존성:** `pip download -r requirements.txt --platform <tag> -d wheelhouse/` 로 타깃 플랫폼 휠 사전 수집 → 오프라인 `pip install --no-index --find-links wheelhouse`. PyInstaller 번들은 애초에 인터프리터+패키지 동봉이라 이게 가장 깔끔.
- **EP별 휠 분기:** 빌드 매트릭스에서 OS/arch마다 다른 onnxruntime 휠을 번들(directml/qnn/gpu/cpu). 동시 설치 금지(§1.2) — 인스톨러가 호스트 감지 후 하나만.
- **모델:** ONNX 모델·VLM(Ollama blob)을 인스톨러에 동봉하거나 1회 온라인 fetch. 현 `scripts/fetch_safety_models.py`·`prepare_yolo26.py`는 온라인 의존 → 오프라인 번들 변형 필요.
- **Ollama 모델:** `ollama pull` 캐시(`~/.ollama/models`)를 사전 동봉하거나 설치 후 1회 pull 안내. Windows ARM은 VLM OFF 권장이라 번들 불필요.
- **torch 제거 검토:** 런타임 추론은 onnxruntime만 필요. `requirements.txt`의 `torch/torchvision/torchaudio`(GB급)는 **export/학습 전용**(`ultralytics` export). 설치형 런타임 번들에선 torch를 분리(`requirements-export.txt`)해 용량 대폭 감축 — PyInstaller 크기에 직결. (검증·테스트 필요)

### 4.2 저사양 폴백
- EP 폴백 체인(§1.3)이 1차 방어 — GPU 없으면 자동 CPU.
- 코어 자체 폴백 노브(이미 일부 존재): `skip_frames`(`run.py:121`), 모델 크기(yolo26n vs s/m), `input_size`(640→320), VLM OFF.
- **저사양 프로파일 프리셋** 제안: `config/profiles/low_end.yaml`(CPU EP·yolo26n·input 320·skip_frames 높임·VLM OFF) — 인스톨러가 호스트 스펙(코어/RAM/GPU) 감지해 자동 선택.
- 헬스(`health.py check_gpu`)가 GPU 부재를 healthy로 처리하므로 CPU-only도 false-red 없음(이미 OK).

### 4.3 GPU 패스스루 (Docker)
- **Linux:** 현 compose `deploy.resources...nvidia`(이미 구현) + 호스트 nvidia-container-toolkit. DGX는 NGC CUDA 베이스(현 Dockerfile) 필요.
- **Windows:** Docker Desktop + **WSL2 백엔드에서만** GPU 가능. WSL2 distro에 nvidia-container-toolkit 설치 + daemon.json `default-runtime: nvidia` 필요(검증됨). 비기술 고객에겐 무거움 → Windows는 네이티브 PyInstaller+DirectML 권장(Docker 회피).
- **macOS:** Docker GPU 패스스루 **불가** → 네이티브 설치만(CoreML).

### 4.4 업데이트 / 롤백
- **Linux/Docker:** 이미지 태그 핀(`build:` → 레지스트리 태그) + `docker compose pull && up -d`, 롤백=이전 태그. systemd는 현 롤백 절차(deployment-guide) 존재.
- **Windows 인스톨러:** Inno Setup 버전 업그레이드(자동 언인스톨→재설치), 서비스 stop/start 래핑. 롤백=이전 인스톨러 재실행. 설정/모델/DB는 설치 디렉토리 밖(ProgramData)에 분리해 업그레이드 시 보존.
- **공통:** `config/`·`models/uploaded/`·`data/`는 코드와 분리(현 compose 볼륨 패턴과 동일 철학) → 업데이트가 사용자 데이터를 안 건드리게.

---

## 5. 단계별 실행 로드맵 (우선순위)

| 단계 | 작업 | 산출물 | 선행 |
|---|---|---|---|
| **P1 (런타임 추상화·코드)** | `yolo_detector` EP 선택을 6-way 폴백으로 교체, `device="auto"` 기본화, EP별 fp16 분기, 워밍업 폴백 | `yolo_detector.py`·`model_registry.py`·`config/default.yaml` + 테스트 | - |
| **P2 (휠 매트릭스)** | OS/arch별 onnxruntime 휠 선택 로직(install.sh 확장 + Windows 설치 스크립트), 동시설치 금지 가드, torch 런타임 분리 검토 | `requirements-runtime.txt`/`requirements-export.txt`, `scripts/select_onnxruntime.py` | P1 |
| **P3 (Windows 패키징)** | PyInstaller spec(데몬+대시보드), NSSM 서비스 래퍼, Inno Setup 스크립트, ARM64 빌드(QNN/CPU) | `packaging/windows/` | P1·P2 |
| **P4 (오프라인 번들)** | wheelhouse·모델·Ollama blob 번들 절차, 저사양 프로파일 프리셋, 호스트 스펙 자동 감지 | `packaging/offline/`, `config/profiles/low_end.yaml` | P2 |
| **P5 (VLM 옵션 확장)** | 외부서버 Ollama UI/문서, (선택)클라우드 VLM provider + PII 동의 게이트, 플랫폼별 VLM 기본 프리셋 | `vlm_inference.py` 분기, 문서 | ADR 확인 |
| **P6 (macOS·CI)** | macOS .app/launchd, GitHub Actions 빌드 매트릭스(6 타깃), 서명/notarization | `.github/workflows/build-installers.yml` | P3 |
| **P7 (문서·검증)** | OS별 설치 가이드, 업데이트/롤백 runbook, 각 타깃 실기 smoke(EP 실제 사용 로그 확인) | `docs/install/<os>.md` | 전체 |

---

## 6. 권장 사항 요약

1. **`device="auto"` + 6-way EP 폴백을 런타임 추상화의 1순위로** — 현 CUDA/CPU 2-way가 멀티-OS 가속 0의 근본 원인. 코드 한 곳(`yolo_detector._init_session`)이 전 플랫폼을 결정.
2. **Windows 주력 납품은 PyInstaller+NSSM+Inno Setup, Linux/DGX는 Docker/systemd 유지** — 한 가지로 통일 말고 고객 유형별 2-트랙(비기술 Windows vs IT/Linux).
3. **GPU 휠은 플랫폼별로 갈라라** — Win x64=DirectML(벤더 공통), Win ARM64=QNN/CPU, Linux NVIDIA=onnxruntime-gpu, DGX ARM64=NGC 컨테이너, macOS=기본(CoreML 내장). onnxruntime-gpu ARM64 휠 없음이 확정.
4. **런타임 번들에서 torch 분리** — export/학습 전용 torch(GB급)를 추론 번들에서 빼 PyInstaller 용량 대폭 감축(검증 후).
5. **오픈-vocab 모델은 라이선스가 블로커** — YOLO-World(GPL-v3)·YOLOE(AGPL-3.0)는 proprietary 설치형 납품 시 enterprise license 필요(별도 담당 영역이나 패키징 번들 대상에 직결, 여기 flag).
6. **Windows ARM64 VLM은 CPU-only** — 이 플랫폼 기본은 VLM OFF 또는 외부서버. 무거운 로컬 VLM 기대 금지.

## 7. Superb AI류 마케팅 vs 현실 한계 (과장 배제)

- "학습 없이 텍스트로 즉시 탐지"는 open-vocab(YOLO-World/YOLOE/GroundingDINO)로 **기술적으로 가능**하나, ① 정확도가 closed-set 대비 낮고 ② 실시간성은 모델/하드웨어 의존(YOLO-World/YOLOE는 실시간 가능, GroundingDINO는 무거움) ③ **라이선스가 상업 설치형에 제약**(GPL/AGPL). "아무 객체나 완벽히"는 마케팅 — 현장 정확도는 프롬프트·임계·후처리 튜닝 필요.
- 설치형 멀티-OS 가속은 가능하나 **단일 패키지로 전 플랫폼 최적 가속은 불가** — OS/arch별 휠·빌드가 본질적으로 갈린다(특히 ARM64 GPU). "어디서나 똑같이 빠름"은 비현실적, 플랫폼별 기대치를 명시해야 함.

---

## 핵심 권장 요약
- `yolo_detector._init_session`의 EP 선택을 CUDA/CPU 2-way → 6-way(TensorRT/CUDA/DirectML/CoreML/QNN/CPU) 우선순위 폴백으로 교체하고 device 기본값을 'auto'로 — 현 코드(yolo_detector.py:256-261, model_registry.py:209, default.yaml:73)가 멀티-OS 가속 0의 단일 근본 원인
- 납품을 2-트랙으로: 비기술 Windows 고객=PyInstaller+NSSM(Windows Service)+Inno Setup 더블클릭 인스톨러, IT/Linux/DGX=현 Docker compose/systemd 유지 — 한 방식 강제는 비현실적
- onnxruntime 휠을 플랫폼별로 분기: Win x64=onnxruntime-directml(NVIDIA/AMD/Intel 공통 DX12), Win ARM64=onnxruntime-qnn(NPU)/CPU, Linux NVIDIA=onnxruntime-gpu, DGX ARM64=NGC 컨테이너, macOS=기본(CoreML 내장) — onnxruntime-gpu ARM64 공식 휠 부재 확정(레포 주석도 경험)
- 런타임 추론 번들에서 torch/torchvision/torchaudio(export·학습 전용, GB급)를 분리해 PyInstaller 용량 대폭 감축 — 추론은 onnxruntime만 필요(검증 후 적용)
- Windows ARM64는 Ollama가 CPU-only(가속 미지원 확인)라 무거운 로컬 VLM 부적합 → 이 플랫폼 VLM 기본은 OFF(1차 YOLO만, 현 create_vlm_provider가 이미 graceful) 또는 외부서버 Ollama
- open-vocab 모델(YOLO-World=GPL-v3, YOLOE=AGPL-3.0)은 proprietary 설치형 납품 시 라이선스 블로커 — enterprise license 또는 라이선스 양립 모델 검토 필요(번들 대상 선정에 직결)
- 오프라인 설치는 OS/arch별 wheelhouse(pip download --platform) + 모델/Ollama blob 동봉, 저사양은 EP CPU 폴백 + low_end 프로파일 프리셋(yolo26n·input 320·VLM OFF)으로 대응

## 구현 전 결정·검증 필요(open questions)
- 클라우드 VLM provider(외부 API) 추가 여부 — ADR-0001이 'PII 외부전송 금지·외부 SaaS 미사용'을 명시하므로, '외부서버 선택' 목표가 (a)외부 Ollama 서버만 허용인지 (b)클라우드 API까지인지 product owner 확정 필요. 후자면 동의 게이트·법적 검토 필요
- 오픈-vocab 탐지(YOLO-World/YOLOE)를 실제 번들 모델로 채택할지 — GPL/AGPL 라이선스가 상업 설치형과 양립 불가. enterprise license 구매 vs 별도 open-vocab 담당 영역과 경계 정리 필요
- 런타임 번들에서 torch 분리 가능 여부 — ultralytics가 ONNX 추론 경로에서도 torch를 import하는지 실측 필요(현 코드는 yolo_detector가 onnxruntime만 직접 쓰지만 ultralytics 패키지 자체가 requirements에 있음)
- PyInstaller로 onnxruntime+opencv+ultralytics 번들 시 실제 동작/용량 — DLL 수집 이슈(--collect-all onnxruntime) 실기 검증, 6개 타깃 빌드 인프라(CI 러너 macOS/Windows ARM64 가용성) 확보 여부
- Windows ARM64 실기 검증 — onnxruntime-qnn NPU 가속이 YOLO ONNX 모델에서 실제 동작하는지, Snapdragon 타깃 고객이 실재하는지(투자 우선순위 판단)
- 코드 서명/공증 정책 — Windows SmartScreen(Authenticode 인증서), macOS notarization 비용·절차 — 비기술 고객 더블클릭 설치의 경고 회피에 필수

## 사실 검증 플래그(근거·출처)
- [검증됨 2026-06] DirectML는 ARM64 미지원 — Windows ARM64 GPU 가속은 DirectML로 불가, NPU는 onnxruntime-qnn(cp312/cp313 휠 존재)만 (onnxruntime DirectML/QNN 공식 문서)
- [검증됨 2026-06] onnxruntime-gpu는 ARM64 공식 pip 휠 부재 — DGX Spark/Jetson에서 pip 설치 실패, 소스빌드·NGC 컨테이너·커뮤니티 휠 필요 (onnxruntime GitHub issue #21232, #24380; 레포 requirements.txt 주석도 동일 경험 기록)
- [검증됨 2026-06] DirectML EP는 NVIDIA/AMD/Intel GPU 공통(DX12)으로 Windows x64에서 벤더 불문 단일 패키지(onnxruntime-directml) 가속 가능
- [검증됨 2026-06] CoreML EP는 기본 onnxruntime 패키지에 내장(macOS) — 별도 GPU 패키지 불필요, Apple Silicon에서 ANE/GPU 사용
- [검증됨 2026-06] Ollama는 Windows ARM64 네이티브 지원하나 GPU/NPU 가속 라이브러리 미지원으로 CPU-only 동작 (Ollama 시스템 요구사항 문서)
- [검증됨 2026-06] YOLO-World=GPL-v3, YOLOE=AGPL-3.0 — 둘 다 proprietary 상업 배포 시 enterprise license 필요 (각 공식 repo/docs)
- [검증됨 2026-06] Docker GPU 패스스루: Linux=nvidia-container-toolkit 네이티브, Windows=WSL2 백엔드 한정(distro에 toolkit 설치+daemon.json), macOS=불가
- [검증됨 2026-06] PyInstaller+onnxruntime는 DLL 로드 이슈 알려짐 — --collect-all onnxruntime 필요, Inno Setup 조합이 Windows 표준 패턴 (다수 GitHub issue)
- [검증 필요] onnxruntime EP 정확한 provider 문자열명(DmlExecutionProvider/CoreMLExecutionProvider/QNNExecutionProvider/TensorrtExecutionProvider) — 구현 전 ort.get_available_providers() 실기 확인 권장
- [검증 필요] ultralytics 추론 경로의 torch 의존성 실측 — 런타임 번들 torch 분리 가능 여부
