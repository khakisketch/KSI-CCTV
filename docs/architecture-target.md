> **상태: 로드맵 초안(설계 단계 — 구현 전)** · 작성 2026-06-16 · 근거: multi-agent 리서치(실 코드 read + 웹 검증)
> 관련 문서: [능력 로드맵](roadmap-capability.md) · [패키징 로드맵](roadmap-packaging.md)
> ⚠️ 이 문서는 방향·후보·단계를 정리한 것으로, 표기된 FPS·라이선스·EP 가용성 등은 구현 착수 시 "사실 검증 플래그"의 [검증필요] 항목을 실측·확정해야 한다.

# 타깃 아키텍처 — 엔진 + API + 웹UI + 영상 (설치형 AI CCTV 제품)

> 본 문서는 `docs/architecture-target.md` 초안이다. 현 `docs/architecture.md`(현행 policy-mode 구현)를 **대체하지 않고**, 그것이 지향할 "제품 형태"의 목표를 정의한다. 현 구조(헤드리스 데몬 + Streamlit)에서 **엔진 자산을 보존한 채** 얇은 API와 웹 프론트로 진화하는 경로를 그린다.

## 0. 현 상태 vs 타깃 (한눈에)

| 축 | 현재 (2026-06, repo 확인) | 타깃 |
|---|---|---|
| 탐지 엔진 | `src/run.py` 헤드리스 데몬 (MultiModelRegistry→EventPolicyEngine→VLM Dispatcher→AlertManager) | **유지** — 그대로 서비스화 |
| 모델 | YOLO ONNX 고정 클래스(COCO/safety_custom) | + **open-vocab 텍스트 프롬프트 탐지**(YOLOE/YOLO-World ONNX) |
| API | 없음. `http.server` 기반 `/health*`만(port 8081, loopback) | **FastAPI**(REST 설정/이벤트/제어 + WS/SSE 실시간) |
| UI | Streamlit 13탭, 단일 화면, **영상=placeholder 검은 이미지(실 스트리밍 없음)** | **Next.js+TS+Tailwind+shadcn** (데스크톱 관제 / 모바일 뷰어, 다크·라이트) |
| 라이브 영상 | 없음(상태·캡처 중심) | RTSP→**MediaMTX/go2rtc**→WebRTC/HLS 브라우저 재생 |
| 배포 | Docker Compose / systemd | + 데스크톱 설치본(Tauri 래퍼, 선택) · macOS/Linux/Windows/Win ARM64 |
| AI 위치 | on-prem Ollama(고정), `VLMProvider` ABC로 교체 가능 | 로컬 **또는** 외부서버 선택(설정 토글) |

> **중요(SSOT 충돌 주의):** 현 `docs/project-definition.md` §비목표는 "실시간 영상 스트리밍 / 모바일 앱 / 클라우드 호스팅"을 **현재 비목표**로 명시한다. 본 타깃은 사용자 확정 제품 목표(①~⑥)에 따라 그 중 **영상 스트리밍·모바일 뷰어를 목표로 승격**한다. 채택 시 `project-definition.md` §비목표를 함께 갱신해야 SSOT 모순이 없어진다. (클라우드 호스팅은 여전히 선택 — "설치형 오프라인"이 1차.)

---

## 1. 타깃 컴포넌트 다이어그램

레퍼런스(Frigate, Viso Suite, Milestone XProtect, NVIDIA Metropolis, Superb AI)는 공통적으로 **(a) 헤드리스 추론 엔진/서비스 + (b) 얇은 API 서버 + (c) 웹 UI + (d) 별도 영상 게이트웨이** 4층으로 분리한다. 추론과 영상 전송을 같은 프로세스에 두지 않는 것이 핵심(Frigate가 go2rtc를 내장 게이트웨이로 끼우는 이유와 동일).

```text
┌──────────────────────────────────────────────────────────────────────┐
│  CCTV / RTSP (현장 카메라 N대)                                          │
└───────────────┬───────────────────────────────┬──────────────────────┘
                │ (분석용 프레임)                 │ (원본 스트림, 재인코딩 無)
                ▼                                 ▼
┌───────────────────────────────┐   ┌────────────────────────────────┐
│ (a) 추론 엔진 (현 src/run.py)  │   │ (d) 영상 게이트웨이              │
│  RTSPCapture                  │   │     MediaMTX 또는 go2rtc        │
│  → MultiModelRegistry(ONNX)   │   │  RTSP in → WebRTC/HLS out       │
│    + open-vocab 어댑터(신규)   │   │  H.264 passthrough(<0.3~0.5s)   │
│  → EventPolicyEngine          │   └──────────────┬─────────────────┘
│  → VLM Dispatcher(로컬/원격)   │                  │ (브라우저가 직접 구독)
│  → AlertManager               │                  │
│  이벤트/상태/메트릭 emit ──────┼──┐               │
└───────────────────────────────┘  │               │
                                    ▼               │
                  ┌─────────────────────────────┐  │
                  │ (b) FastAPI 서비스 (신규)    │  │
                  │  REST: 설정/이벤트/제어      │  │
                  │  WS/SSE: 이벤트·상태 푸시    │  │
                  │  /events 영속화(SQLite)      │  │
                  │  인증(scrypt 이식)·RBAC      │  │
                  └──────────────┬──────────────┘  │
                                 │ REST + WS/SSE    │ WebRTC/HLS
                                 ▼                  ▼
                  ┌──────────────────────────────────────────┐
                  │ (c) 웹 프론트 (Next.js+TS+Tailwind+shadcn) │
                  │  데스크톱 관제(멀티뷰·정책·모델·메트릭)     │
                  │  모바일 뷰어(이벤트·단일 라이브·알림)       │
                  │  다크/라이트, 반응형, word-break:keep-all   │
                  └──────────────────────────────────────────┘
                                 │ (선택)
                                 ▼  Tauri 래퍼 = 위 웹 UI를 네이티브 창에
                  ┌──────────────────────────────────────────┐
                  │ 데스크톱 설치본 (mac/Linux/Win/Win-ARM64)  │
                  └──────────────────────────────────────────┘
```

### 컴포넌트 책임표

| # | 컴포넌트 | 기술 | 책임 | 현 자산 |
|---|---|---|---|---|
| a | **추론 엔진** | Python + onnxruntime | 프레임 샘플링·다중모델 추론·정책 평가·VLM 라우팅·알람. 영상 전송/HTTP UI 책임 없음 | `src/run.py`, `src/pipeline`, `src/detection`, `src/policy`, `src/vlm`, `src/alerts` **그대로** |
| a+ | **open-vocab 어댑터** | YOLOE/YOLO-World ONNX | "텍스트로 아무 객체" — 프롬프트→임베딩→탐지를 `YOLODetector` 인터페이스로 래핑해 `MultiModelRegistry`에 1모델로 등록 | 신규 (`src/detection/openvocab_detector.py`) |
| b | **API 서비스** | **FastAPI + Uvicorn** | REST(설정·이벤트·카메라·모델·제어) + WS/SSE(이벤트·상태 푸시). 엔진 in-process 또는 IPC | 신규. 인증/감사/헬스 로직은 기존 코드 이식 |
| b- | **이벤트 저장소** | **SQLite**(설치형 기본) | 현재 in-memory `event_history`(휘발) + 로그파일 → 질의 가능 영속 저장 | 신규. AlertManager 로그 파서 재사용 |
| c | **웹 프론트** | **Next.js(App Router)+TS+Tailwind+shadcn/ui** | 데스크톱 관제 + 모바일 뷰어. SaaS 룩, 다크/라이트, 반응형 | Streamlit 13탭의 IA를 이식 |
| d | **영상 게이트웨이** | **MediaMTX** 또는 **go2rtc** | RTSP→WebRTC(저지연)/HLS(호환). H.264 passthrough(재인코딩 無) | 신규(외부 바이너리, 직접 구현 금지) |
| - | 데스크톱 래퍼 | **Tauri 2**(선택) | 웹 UI를 네이티브 설치본으로. mac/Linux/Win/Win-ARM64 | 후순위(Phase 4) |

---

## 2. 라이브 영상 — 직접 구현 금지, 게이트웨이 활용

브라우저는 RTSP를 직접 재생 못 한다. **추론 엔진이 영상까지 푸시하면 안 된다**(엔진 부하·재인코딩·동기화 지옥). 검증된 패턴: 원본 RTSP를 **MediaMTX/go2rtc**가 받아 **WebRTC(저지연)** 또는 **HLS(호환)** 로 재발행, 브라우저가 직접 구독.

| 항목 | MediaMTX | go2rtc |
|---|---|---|
| 라이선스 | MIT | MIT |
| 저지연 | WebRTC 서브초 | WebRTC ~0.3s(H.264 passthrough), 0.5s 수준 |
| 출력 | RTSP/WebRTC/HLS/RTMP | RTSP/WebRTC/HLS/MSE/MJPEG (가장 다양) |
| 플랫폼 | Linux/mac/Win | Linux/mac/Win |
| 비고 | 단순·안정, 운영 문서 풍부 | 프로토콜 폭넓음, Frigate 채택 |

**권장:** 초기엔 **MediaMTX**(단순·문서 풍부). 다양한 코덱/프로토콜 폴백이 필요해지면 go2rtc로 전환(둘 다 MIT, 설정 호환 개념 유사 → 교체 비용 낮음). 둘 다 H.264 passthrough라 재인코딩 없이 CPU/GPU를 거의 안 쓴다.

**동선:** 카메라 RTSP URL은 **엔진 + 게이트웨이가 공유**(`.env`/config SSOT). 프론트는 `/api/cameras/{id}/stream` 같은 엔드포인트로 게이트웨이 URL(WebRTC offer/whep 또는 HLS m3u8)만 받아 `<video>`+WebRTC 또는 hls.js로 재생. 자격증명은 게이트웨이 토큰으로 가리고 프론트에 RTSP 비번 노출 금지.

> **검증 필요(verification_flags):** WebRTC는 NAT/방화벽 환경에서 STUN/TURN이 필요할 수 있다. 설치형 LAN 내부 관제는 보통 직결로 충분하나, 원격 접속 시나리오는 TURN 또는 HLS 폴백을 기본 제공해야 한다.

---

## 3. Streamlit → 웹 프론트 마이그레이션 (자산 보존)

**원칙:** 탐지/정책/보안/알람 **백엔드는 한 줄도 다시 안 짠다.** Streamlit은 "엔진을 직접 import해 in-process로 쓰는 UI"라 백엔드와 강결합돼 있다. 마이그레이션은 **그 결합을 FastAPI 경계로 끊는 것**이 본질이다.

### 단계 (점진적 strangler-fig — Streamlit과 신규 UI 병존)

- **단계 0 — 영속화 선행(차단요인 제거).** 현 `event_history`는 in-memory(휘발). FastAPI/웹UI 이전에 **이벤트를 SQLite에 영속화**한다(AlertManager 로그 파서 `parse_alert_log_line` 재사용). 이게 없으면 새 UI가 재시작마다 빈 화면.
- **단계 1 — FastAPI 골격 + 엔진 in-process 마운트.** `src/run.py`의 조립부(`build_pipeline`)를 그대로 FastAPI `lifespan`에 넣어 엔진을 띄우고, 기존 `/health*`를 FastAPI로 흡수(중복 HTTP 서버 제거). 인증(scrypt)·rate limit·감사 로그(`_write_audit_log`)를 FastAPI 미들웨어/디펜던시로 이식. **이 시점에 Streamlit은 그대로 둔다.**
- **단계 2 — 읽기 표면부터 REST/WS화.** `pipeline.get_status()`·`get_metrics()`·이벤트 목록을 REST로, 실시간 이벤트/상태를 **WS 또는 SSE**로 노출. Next.js로 **읽기 전용 관제 화면(라이브 영상 + 이벤트 피드 + 메트릭)** 부터 구축 — 가장 가치 높고 위험 낮음.
- **단계 3 — 쓰기/제어 표면 이관.** 카메라 설정·모델 업로드·정책·알림 설정 같은 mutating 탭을 REST POST로. 모델 업로드 검증기(`src/models/upload_validator.py`)·격리 저장은 그대로 호출.
- **단계 4 — Streamlit 폐기.** 모든 13탭 기능이 웹 UI에 도달하면 Streamlit 제거. (그 전까지 운영자는 둘 중 하나 사용 가능 — 무중단 전환.)

### API 표면 스케치 (엔드포인트 목록)

> 인증: 모든 `/api/*`는 세션/토큰 필요. `/health*`만 공개(loopback 기본). 기존 scrypt+rate-limit+세션만료 정책 이식.

**시스템·헬스**
- `GET /health`, `/healthz`, `/health/live`, `/health/ready` — 기존 로직 이식
- `GET /api/metrics` — `pipeline.get_metrics()`(FPS·latency·VLM 큐·모델별 metrics)
- `GET /api/status` — `pipeline.get_status()`(running·카메라·VLM 캐시/큐)

**카메라**
- `GET /api/cameras` / `GET /api/cameras/{id}` — 목록·상태(connected/fps/dropped/stale)
- `POST /api/cameras` / `PUT /api/cameras/{id}` / `DELETE` — CRUD(cameras.json 갱신)
- `GET /api/cameras/{id}/stream` — 영상 게이트웨이 URL(WebRTC/HLS) 반환
- `GET /api/cameras/{id}/snapshot` — 최신 프레임 JPEG(얼굴 블러 적용)

**이벤트·알람**
- `GET /api/events?from&to&camera&type&level&q&page` — SQLite 질의(고급 검색 탭 대체)
- `GET /api/events/{id}` — 상세(VLM 설명·risk·캡처 이미지 링크)
- `GET /api/events/{id}/image` — 캡처 이미지(블러·복호화 적용)
- `WS /api/ws/events` 또는 `GET /api/sse/events` — 실시간 이벤트 푸시
- `WS /api/ws/status` 또는 SSE — 카메라/큐/모델 상태 푸시

**정책·프로파일**
- `GET/PUT /api/policies` — `event_policies.yaml`
- `GET/PUT /api/camera-profiles` — 카메라별 도메인/이벤트/zone
- `GET/PUT /api/alert-preferences` — 알림 설정 탭

**open-vocab 프롬프트 (제품 차별점 ①)**
- `GET/PUT /api/cameras/{id}/prompts` — "사람이 넘어짐", "박스가 통로를 막음" 등 자연어 클래스 목록 설정 → 엔진이 어댑터로 반영

**모델 관리**
- `GET /api/models` — 등록/활성 모델·load_status·metrics
- `POST /api/models/upload` — ONNX 업로드(검증기·격리 저장 재사용)
- `POST /api/models/{name}/activate` / `deactivate`

**제어·감사**
- `POST /api/pipeline/start` / `stop` / `restart`(설치형은 보통 상시가동이라 선택)
- `GET /api/audit-log` — 기존 감사 로그

> **WS vs SSE:** 이벤트/상태는 서버→클라 단방향 푸시라 **SSE로 충분**(재연결·프록시 친화·구현 단순). 양방향(라이브 제어 협상)이 필요해지면 WS. 초기엔 SSE 권장.

---

## 4. 데스크톱/모바일 IA (기능 분리)

같은 백엔드, **다른 정보 밀도**. 데스크톱=풀 관제(밀집), 모바일=이벤트 뷰어+단일 라이브(경량). Tailwind 반응형 분기 하나로 두 경험을 만든다(앱 두 벌 금지).

### 데스크톱 — 풀 관제 (운영자/안전관리자)
- **라이브 월(멀티뷰)**: 2×2/3×N 그리드 WebRTC, 카메라별 상태 배지(fps/dropped/stale), 클릭→단일 확대
- **이벤트 센터**: 실시간 피드(SSE) + 고급 검색/필터 + 상세(VLM 설명·권고조치·캡처) + **VLM 미확인 배지**(`vlm_unconfirmed` 노출 — false positive 수동확인 유도)
- **메트릭/헬스 대시보드**: FPS·latency·VLM 큐 backpressure·모델별 error/latency·헬스체크
- **설정**: 카메라 CRUD, 카메라별 도메인/이벤트/zone, **open-vocab 프롬프트 편집기**, 모델 업로드/활성화, 알림 설정, AI 위치(로컬/원격) 토글
- **감사 로그**

### 모바일 — 경량 뷰어 (현장 담당자/순찰)
- **이벤트 피드**: 시간순 카드(레벨 색상·썸네일·VLM 한 줄 요약), 푸시 알림 진입점
- **이벤트 상세**: 큰 캡처 + VLM 설명 + 권고조치 + "라이브 보기"
- **단일 카메라 라이브**: 탭한 카메라만 WebRTC/HLS 전체화면
- **(읽기 위주)** 설정·모델 업로드·멀티뷰 월은 **숨김/축소**(데스크톱 전용)
- 한글 카드: `word-break: keep-all`, 긴 카메라명 세로 reflow(가로스크롤 금지), 터치타겟 ≥44px

### IA 분리 매트릭스 (요약)

| 기능 | 데스크톱 | 모바일 |
|---|---|---|
| 멀티뷰 라이브 월 | ✅ | ❌(단일만) |
| 단일 라이브 | ✅ | ✅ |
| 이벤트 피드/검색 | ✅ 고급 | ✅ 기본 |
| 메트릭/헬스 | ✅ | 요약만 |
| 카메라/정책/모델 설정 | ✅ | ❌ |
| open-vocab 프롬프트 | ✅ | ❌ |
| 알림 수신 | ✅ | ✅(푸시 핵심) |

### Tauri 래퍼 위치 (선택, Phase 4)
- 동일 Next.js 빌드를 **Tauri 2** 네이티브 창에 로딩 → "설치형 데스크톱 앱"(한국 CCTV 용역의 Windows 친숙성 충족).
- 타깃: macOS(arm64/x64) · Linux · **Windows x64 · Windows ARM64**(`aarch64-pc-windows-msvc`, MSVC ARM64 빌드툴 필요). 멀티아치 MSIX 번들 가능.
- **결정 필요:** Tauri는 "UI를 네이티브로 감싸는" 래퍼다. 엔진(Python)·게이트웨이는 여전히 별도 프로세스/서비스로 동봉 설치돼야 한다(Tauri sidecar로 번들 또는 별도 인스톨러). 단순 LAN 관제면 **브라우저 접속만으로 충분** — Tauri는 "단일 PC 올인원 설치본"이 필요할 때만.

---

## 5. open-vocab "텍스트로 아무 객체 탐지" — 현실과 한계

제품 목표 ①(Superb AI류)의 핵심. **YOLOE / YOLO-World**가 ONNX export + 실시간(T4 161 FPS급) 가능함을 확인. 단, **마케팅과 현실을 구분**:

- **가능:** "사람, 헬멧, 지게차, 박스" 같은 **명사 객체**를 학습 없이 텍스트로 탐지. ONNX로 export해 현 `MultiModelRegistry`에 1모델로 등록 가능.
- **한계(과장 금지):** open-vocab는 **객체 탐지**지 **상황/행동 이해**가 아니다. "쓰러짐", "싸움", "통로 막힘" 같은 **상황**은 여전히 zone/overlap **rule + VLM 2차 분석**(현 EventPolicyEngine 패턴) 조합으로 푼다. open-vocab은 후보 객체를 넓히고, VLM이 상황을 판정하는 **역할 분담**이 정답.
- **정확도:** 도메인 특화 fine-tuned 모델보다 zero-shot 정확도가 낮을 수 있음 → 핵심 안전 이벤트는 여전히 전용 모델 권장, open-vocab은 "임의 객체 즉시 추가" 유연성 레이어.

### ⚠️ 라이선스 — 설치형/판매 제품의 최대 블로커 (verification_flags)
- **Ultralytics YOLOE / YOLO-World 패키지 = AGPL-3.0.** 판매용 설치형 제품에 코드/모델/파이프라인을 쓰면 **전체 제품 소스 공개 의무** 또는 **Ultralytics Enterprise 유료 라이선스** 필요. → **납품 전 반드시 라이선스 결정.**
- 원조 `AILab-CVC/YOLO-World` = GPL-v3(상업 사용 허용이나 copyleft 주의).
- MIT 라이선스 **ONNX 추론 래퍼**(예: yolo-world-onnx)는 **이미 export된 가중치를 돌리는 런타임**만 MIT — **가중치 자체의 라이선스는 별도**. "코드 MIT" ≠ "모델 자유".
- **권장:** ① 상용 라이선스 가능한 open-vocab 모델 소싱(또는 Enterprise 라이선스 예산), 또는 ② 핵심 이벤트는 자체학습 회피한 **상용허용 pretrained 안전모델** + open-vocab은 "보조 유연성"으로 포지셔닝. (현 repo는 이미 `docs/model-sourcing.md`에 AGPL 경고 체계가 있음 — open-vocab도 같은 게이트에 편입.)

---

## 6. 단계별 실행 로드맵 (요약)

| Phase | 산출물 | 핵심 위험 |
|---|---|---|
| **P0** 영속화 | 이벤트 SQLite 저장 + 조회 API 기반 | 스키마 설계(이벤트 필드 ↔ SafetyEvent.to_dict 매핑) |
| **P1** API 골격 | FastAPI에 엔진 in-process 마운트, `/health*`·인증·감사 이식, 읽기 REST + SSE | 엔진 생명주기·스레드안전(현 `_state_lock` 패턴 유지) |
| **P2** 영상 | MediaMTX 도입, `/cameras/{id}/stream` 발급, 브라우저 WebRTC 재생 | NAT/TURN, 자격증명 누출 차단 |
| **P3** 웹 프론트 | Next.js 관제(읽기)→설정(쓰기), 모바일 뷰어, 다크/라이트 | UX 목표 선정의(/deep-interview), 한글 reflow |
| **P3.5** open-vocab | YOLOE/YOLO-World ONNX 어댑터 + 프롬프트 API/UI | **라이선스 결정(블로커)**, 정확도 검증 |
| **P4** 패키징 | Tauri 데스크톱 설치본(mac/Linux/Win/Win-ARM64), Streamlit 폐기 | sidecar(Python 엔진+게이트웨이) 동봉, Win ARM64 빌드 |

---

## 7. 결정/검증 필요 (요약)
- AI 위치 로컬/원격 토글: 현 `VLMProvider` ABC + `create_vlm_provider` 분기에 "원격 provider"(Claude API 등)만 추가하면 됨 — 단 **CCTV 프레임=PII** 외부전송 동의 게이트 필수(현 코드 주석이 이미 이 우려를 명시).
- 엔진↔API 결합: in-process(단순, 단일 프로세스 크래시 공유) vs IPC/별도 서비스(격리, 복잡). 설치형 단일PC면 **in-process로 시작**, 다중노드 확장 시 분리.
- open-vocab 라이선스(최우선).

---

## 핵심 권장 요약
- 엔진을 다시 짜지 마라 — src/run.py의 build_pipeline(MultiModelRegistry→EventPolicyEngine→VLM→AlertManager)을 그대로 FastAPI lifespan에 in-process 마운트하고 그 위에 얇은 REST+SSE만 얹는다. 백엔드 자산 100% 보존이 마이그레이션의 핵심.
- 마이그레이션 차단요인은 영속화다 — 현 event_history는 in-memory(휘발). FastAPI/웹UI보다 먼저 이벤트를 SQLite에 영속화(AlertManager parse_alert_log_line 재사용)해야 새 UI가 재시작 후 빈 화면이 안 된다.
- 라이브 영상은 직접 구현 금지 — RTSP→MediaMTX(MIT, 단순·문서풍부) WebRTC passthrough(<0.5s)로 브라우저 재생. 추론 엔진과 영상 전송을 같은 프로세스에 두지 않는다(Frigate/Viso 공통 패턴). 코덱 폴백 필요시 go2rtc로 교체(둘 다 MIT).
- open-vocab(YOLOE/YOLO-World)는 ONNX export·실시간(161FPS T4) 가능하나 Ultralytics 패키지=AGPL-3.0이 판매용 설치형의 블로커 — 납품 전 라이선스(Enterprise 구매 vs 상용허용 모델 소싱) 결정 필수. 코드 MIT 래퍼≠모델 자유.
- open-vocab은 '객체 탐지'지 '상황 이해'가 아니다 — 명사 객체는 텍스트로 즉시 추가하되, 쓰러짐·싸움·통로막힘 같은 상황은 현 EventPolicyEngine의 rule+VLM 조합으로 판정하는 역할분담이 정답(Superb 마케팅과 현실 구분).
- 단일 백엔드·반응형 분기로 데스크톱(멀티뷰 풀관제)/모바일(이벤트피드+단일라이브 경량뷰어)을 만든다 — 앱 두 벌 금지. Tailwind+shadcn, 다크/라이트, 한글 word-break:keep-all·터치타겟 44px.
- 이벤트/상태 실시간 푸시는 단방향이라 WS보다 SSE로 시작(재연결·프록시·구현 단순), 양방향 제어가 필요해지면 WS로 승급.
- Tauri 2 데스크톱 래퍼(Win ARM64 포함 가능)는 후순위 — LAN 관제는 브라우저 접속으로 충분. Python 엔진+영상 게이트웨이를 sidecar로 동봉해야 하므로 '단일PC 올인원 설치본'이 실제로 필요할 때만 착수.

## 구현 전 결정·검증 필요(open questions)
- 엔진↔API 결합 방식: in-process(설치형 단일PC 단순) vs 별도 서비스/IPC(격리·다중노드 확장). 초기엔 in-process 권장하나 확정 필요.
- 이벤트 영속 저장소: SQLite(설치형 기본·무설정)로 시작 vs 처음부터 Postgres(다중노드·대규모). 스키마를 SafetyEvent.to_dict 필드와 어떻게 매핑할지 설계 필요.
- WebRTC 원격접속 시 STUN/TURN 인프라가 설치형 오프라인 전제와 충돌 — LAN 직결만 지원할지, HLS 폴백+TURN을 기본 제공할지 결정.
- open-vocab 라이선스 경로 확정: Ultralytics Enterprise 라이선스 예산 vs 상용허용 pretrained 안전모델로 우회 vs open-vocab을 보조레이어로만 한정.
- AI 외부서버 옵션(로컬/원격 토글)에서 CCTV 프레임=PII의 외부전송 동의/규정 게이트를 어떻게 강제할지(현 코드는 on-prem 기본·우려 주석만 존재).
- project-definition.md §비목표(실시간 영상스트리밍·모바일앱)를 본 타깃에 맞춰 갱신해야 SSOT 모순 제거 — 갱신 시점/범위 합의 필요.
- 웹 프론트 착수 전 UX 목표(페르소나·클릭예산·상태인벤토리·마이크로카피·접근성) 정의 — /deep-interview 권장.

## 사실 검증 플래그(근거·출처)
- 확인됨(WebSearch 2026-06): YOLOE(2025-03, Tsinghua, YOLOv10 기반)·YOLO-World v2는 ONNX export 지원, 실시간(YOLOE T4 161 FPS) — open-vocab 텍스트/비주얼/프롬프트프리 모드. 출처: docs.ultralytics.com/models/yoloe, /models/yolo-world
- 확인됨(WebSearch): Ultralytics YOLO(YOLOE/YOLO-World 포함)=AGPL-3.0 — 상업적 설치형 제품은 전체 소스공개 또는 Enterprise 유료라이선스 필요. 원조 AILab-CVC/YOLO-World=GPL-v3. MIT는 ONNX 추론 래퍼 코드뿐, 가중치 라이선스는 별도. 출처: ultralytics.com/license, github issue #19390
- 확인됨(WebSearch): MediaMTX·go2rtc 모두 MIT, RTSP→WebRTC H.264 passthrough로 서브초(go2rtc ~0.3s) 저지연, Linux/mac/Win 지원. go2rtc가 프로토콜 더 다양(Frigate 채택). 출처: go2rtc.com, stable-learn.com mediamtx 가이드
- 확인됨(WebSearch): Tauri 2는 Windows ARM64 네이티브 빌드 가능(aarch64-pc-windows-msvc + MSVC ARM64 빌드툴), 멀티아치 MSIX 번들 가능. 출처: v2.tauri.app/distribute/windows-installer, mobzystems.com tauri 크로스컴파일
- 확인됨(repo Read): src/run.py가 헤드리스 데몬으로 완비(MultiModelRegistry+EventPolicyEngine+SafetyPipeline+AlertManager 조립, http.server 기반 /health* 포트8081 loopback). FastAPI/REST API는 부재 — health HTTP 핸들러만 존재.
- 확인됨(repo Read): Streamlit 대시보드(app.py 2325줄, 13탭)가 유일 UI. render_stream_view는 placeholder 검은 이미지(np.zeros)로 실 영상 스트리밍 없음 — 상태·캡처 중심. project-definition §비목표가 '실시간 영상스트리밍·모바일앱·클라우드'를 현재 비목표로 명시(타깃에서 승격 대상).
- 확인됨(repo Read): VLM은 VLMProvider ABC+create_vlm_provider 팩토리로 교체가능(현 ollama만, on-prem PII 이유). 로컬/원격 토글은 원격 provider 분기 추가로 구현 가능. onnxruntime EP는 CUDA/CPU 자동선택 구현됨.
- 검증 필요: WebRTC NAT 통과용 STUN/TURN 필요성(설치형 LAN 내부면 직결 가능하나 원격접속은 폴백 필요).
- 검증 필요(미확인): yolo-world-onnx 등 MIT 래퍼로 export한 ONNX 가중치를 상업 배포할 때 원본 가중치 학습 라이선스가 전파되는지 — 모델 카드별 개별 확인 요망.
