# KSI-CCTV — 프로젝트 지침 (AI/개발자 공통)

DGX Spark 기반 지능형 CCTV 안전 관제 시스템.
스택: Python 3.10 · YOLO26 ONNX(1차 고속 필터) → Ollama Qwen3-VL(2차 정밀 분석) · FastAPI 엔진 API · Next.js 웹 콘솔 · Docker Compose.
구조: `src/capture` · `src/detection` · `src/pipeline` · `src/policy` · `src/vlm` · `src/alerts` · `src/api`(엔진 API) · `frontend/`(웹 콘솔).

## SSOT 문서 — 비자명한 변경 전 반드시 읽어라

| 문서 | 내용 |
|---|---|
| **`docs/release-gate.md`** | **출고 승인 기준 SSOT** — Advanced-AI v1 범위, 소프트웨어 CI와 실장비 증적의 독립 게이트. "완료/출고 가능"을 판단할 땐 여기가 우선한다 |
| `docs/internal-ci.md` | 차단형 게이트 실행 절차와 필수 증적 |
| `docs/offline-appliance.md` | 서명 오프라인 번들 생성·설치·복구 |
| `docs/architecture.md` | 목표 아키텍처(MultiModelRegistry → EventPolicyEngine → VLM Dispatcher) |
| `docs/project-definition.md` | 제품 비전 및 카메라별 프로파일 원칙 |
| `docs/event-policy-engine.md` | 이벤트 정책 엔진 설계(trigger/rule/vlm/alert 흐름) |
| `docs/model-management.md` | 기본 모델 vs 사용자 업로드 모델 관리 전략 |
| `docs/adr/0001-multi-model-policy-architecture.md` | 다중 모델 + 정책 아키텍처 결정 기록 |

> `docs/completion-plan.md`·`docs/delivery-checklist.md`·`docs/implementation-plan.md`·`.ai/*`는
> **역사적 기록**이다. 과거 체크박스의 완료 표시는 출고 승인을 뜻하지 않는다.

## "완료" 게이트 — 모두 통과해야 함

```bash
./.venv/bin/python -m pip install -r requirements-dev.txt   # pytest·ruff·mypy·pip-audit (최초 1회)
./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider
```

> 반드시 `.venv`를 사용한다. 시스템 `python3`로 돌리면 `cv2` 등 의존성이 없어
> collection 단계에서 `NameError`로 실패한다. 새 컴퓨터 셋업은 `docs/development-handoff.md`.

> `-B -p no:cacheprovider`를 붙여라. 그냥 `pytest`로 돌리면 `src/**/__pycache__`가 생기고,
> 릴리스 게이트가 "production Docker context에 ignored 파일이 있다"며 실행 전에 차단된다.

**웹 콘솔(`frontend/`) 변경 시 추가 게이트:**

```bash
cd frontend && npm run build      # tsc + eslint + 라우트 생성(타입/린트 게이트)
# 화면 변경 시: 엔진(uvicorn src.api.app:app) + 웹(npm run dev) 띄워 브라우저 확인
```

기동 후 눈으로 확인할 항목:
- 빈 데이터(이벤트 0건) 상태에서 레이아웃이 깨지지 않는가 (빈 테이블·빈 상태가 graceful하게 렌더되는가)
- 이벤트 없음 상태에서 오류 메시지·빈 화면 대신 적절한 안내 문구가 표시되는가
- 한글 텍스트가 어절 단위로 줄바꿈되는가 (`word-break: keep-all` 적용 여부)
- 브라우저 콘솔에 JavaScript 오류가 없는가
- 데스크톱 + 모바일(390px) 양쪽 렌더

> Ruff 설정은 `ruff.toml`, mypy 설정은 `pyproject.toml`에 있다. 빠른 개발 게이트는
> `bash scripts/test.sh`, release candidate 게이트는 `bash scripts/verify_release_candidate.sh --strict`다.
> mypy strict 범위는 인증/SSE service, durable event store, Appliance CLI/unit validator부터 점진적으로 확대한다.

## 웹 콘솔 확인

```bash
docker compose --profile web up --build   # 엔진+게이트웨이+웹 콘솔 → http://localhost:3000
# 또는 로컬: uvicorn src.api.app:app (엔진) + cd frontend && npm run dev (웹)
```

> Streamlit 대시보드는 Next.js 웹 콘솔로 완전 이관·폐기되었다(2026-06-17). `src/dashboard`·`run_dashboard.sh` 없음.

## 운영 주의

- 하드웨어: DGX Spark(GB10, sm_121, CUDA 13). PyTorch는 **cu130** 휠로 설치.
- **`batch=-1` 절대 금지** — 통합메모리 OOM. 학습 시 고정 batch 값을 명시.
- RTSP 계정/비밀번호는 저장소에 커밋 금지 — `.env`(`.env.example` 참고) 또는 현장별 보안 저장소 사용.
- COCO pretrained 모델(`class_profile: coco`)에서는 safety gear violation 이벤트를 자동 생성하지 않는다(false alarm 방지). 안전모/조끼/화재/연기는 커스텀 ONNX 모델과 `safety_custom` 프로파일 필요.
- 모델 파일(`.onnx`)은 `models/`에 위치. 새 모델 등록은 `config/model_profiles.yaml` + `MultiModelRegistry` 경로를 따른다 — `yolo_detector.py` 직접 수정 금지.

## Superpowers 운용 (필수 — 매 턴 첫 행동 전 점검)

- 관련 가능성 1%라도 있으면 `skill` 도구로 해당 스킬을 먼저 로드한다. 작업 전 점검을 건너뛰고 탐색·질문·구현부터 하지 않는다.
- 창의적 작업 전 `brainstorming`, 버그/실패 분석 전 `systematic-debugging`, 다단계 구현 전 `writing-plans`, 구현 시 `test-driven-development`(실패 테스트→구현→통과→커밋), 완료 주장 전 `verification-before-completion`, 코드 리뷰 전후 `requesting-code-review`/`receiving-code-review`를 사용한다.
- 공식 Superpowers는 변경 없이 사용한다. 커스터마이징용 복사·편집·포크 금지.
- 위임 매핑: 발견·조사=explore, 구현=developer(복잡·결합 시 developer-complex), 테스트 실행=test-runner, 리뷰=reviewer, 중요 계획 비판=plan-reviewer. 서브에이전트로 받은 단일 작업 실행 중에는 이 점검을 반복하지 않는다.
- 한 공유 워크트리당 작성자는 1명. 병렬 구현은 분리 워크트리 + 비중첩 소유일 때만 허용한다. 작성자는 독립 테스트/리뷰 전에 손을 멈추고 실제 diff를 인계한다.
- 커밋·푸시·PR·배포·자격증명 변경·비가역 정리·외부 효과는 명시적 승인 후에만 수행한다.

## 언어

한국어로 답한다. 기술 용어는 원어 유지. 코드/주석은 repo 관습(한국어 주석).
