# KSI-CCTV — 프로젝트 지침 (AI/개발자 공통)

DGX Spark 기반 지능형 CCTV 안전 관제 시스템.
스택: Python 3.10 · YOLO26 ONNX(1차 고속 필터) → Ollama Qwen3-VL(2차 정밀 분석) · FastAPI 엔진 API · Next.js 웹 콘솔 · Docker Compose.
구조: `src/capture` · `src/detection` · `src/pipeline` · `src/policy` · `src/vlm` · `src/alerts` · `src/api`(엔진 API) · `frontend/`(웹 콘솔).

## SSOT 문서 — 비자명한 변경 전 반드시 읽어라

| 문서 | 내용 |
|---|---|
| `docs/architecture.md` | 목표 아키텍처(MultiModelRegistry → EventPolicyEngine → VLM Dispatcher) |
| `docs/project-definition.md` | 제품 비전 및 카메라별 프로파일 원칙 |
| `docs/event-policy-engine.md` | 이벤트 정책 엔진 설계(trigger/rule/vlm/alert 흐름) |
| `docs/model-management.md` | 기본 모델 vs 사용자 업로드 모델 관리 전략 |
| `docs/adr/0001-multi-model-policy-architecture.md` | 다중 모델 + 정책 아키텍처 결정 기록 |

## "완료" 게이트 — 모두 통과해야 함

```bash
pip install pytest        # pytest는 dev 전용 — 런타임 requirements.txt에 없음(최초 1회만)
python3 -m pytest tests/
```

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

## 언어

한국어로 답한다. 기술 용어는 원어 유지. 코드/주석은 repo 관습(한국어 주석).
