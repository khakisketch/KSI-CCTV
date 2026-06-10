# Latest Build Handoff

## Phase 9 — M4 + M3 + M1 + M2 + Should-fix: 모든 Phase 9 마일스톤 완료 (완료 ✅)

Phase 8 완료 이후 Phase 9의 핵심 4개 마일스톤(M4, M3, M1, M2)과 review Should-fix를 모두 마쳤다.
- M4: 사전학습 모델(`yolo26_ppe.onnx`, `yolo26_fire_smoke.onnx`) 다운로드 + e2e 추론 검증, `fetch_safety_models.py --all` 지원, `huggingface_hub`를 `requirements.txt`에 추가
- M3: `scripts/stress_test.py` 작성/검증. `failed_frames=0`, `events_by_type={fire:15}`. `cam_id`가 engine.evaluate에 정상 반영
- M1: `OllamaProvider` thread-safe 메트릭(호출/성공/실패/평균/최대 레이턴시/last_error/risk_level), `SafetyPipeline` VLM TTL cache (bypass=critical/high), 대시보드 시스템 상태 탭에 VLM metrics + cache stats + reset 버튼
- M2: `scripts/profile_pipeline.py` 작성/검증. cProfile + tracemalloc으로 모델별/파이프라인별 레이턴시(p50/p95) + 피크 메모리 측정. `--cprofile` 상세 출력, `--json-out` 저장 지원
- Should-fix: `pipeline.py`에 `reset_vlm_cache()` 공개 메서드 추가, 대시보드에서 private attribute 대신 공개 메서드 사용

## Build Handoff
- User intent: Phase 9 모든 마일스톤(M4, M3, M1, M2) + Should-fix 완료로 개발 개선 전 과정 마무리
- Plan summary:
  - M4: `scripts/fetch_safety_models.py`에 `--all` 옵션, fire 모델 docstring에 401 fallback 사유 명시, `requirements.txt`에 `huggingface_hub` 추가, e2e_smoke_test --policy-mode로 검증
  - M3: `scripts/stress_test.py`에서 `_create_pipeline()`로 컴포넌트 1회 생성, `cam_id`를 `engine.evaluate(camera_id=cam_id)`에 전달, `--cameras` 의미 명확화, `--json-out` 옵션 추가. `PolicyDecision.event_type`이 `SafetyEvent` 객체가 되지 않도록 `d.event.event_type`로 접근
  - M1: `OllamaProvider`에 thread-safe metrics + `get_metrics()` / `reset_metrics()`, `SafetyPipeline`에 `vlm_cache_ttl_sec` / `vlm_cache_bypass_levels` 파라미터, `_analyze_with_vlm`에서 cache 검사 + bypass, `get_vlm_cache_stats()` 추가, 대시보드 시스템 상태 탭에 VLM 메트릭/캐시 섹션 + reset 버튼
  - M2: `scripts/profile_pipeline.py` 신규 작성 — cProfile + tracemalloc, 개별 모델/전체 파이프라인 프로파일링, 합성 프레임/비디오 파일 지원, JSON 저장
  - Should-fix: `pipeline.py`에 `reset_vlm_cache()` 공개 메서드 추가, `src/dashboard/app.py`에서 `hasattr(pipeline, "reset_vlm_cache")` 확인 후 호출로 private attribute 접근 제거
- Acceptance criteria:
  - `fetch_safety_models.py --all --json` 성공, 2개 모델 모두 OK
  - `e2e_smoke_test.py --policy-mode`에서 `failed_models={}`
  - `stress_test.py --duration 30 --policy-mode`에서 `failed_frames=0`, `events_by_type` 채워짐
  - `OllamaProvider`가 `get_metrics()` / `reset_metrics()` 노출
  - 대시보드 시스템 상태 탭에 VLM 메트릭 / cache stats 표시
  - `profile_pipeline.py --frames 5 --iterations 1 --pipeline-iterations 1 --json-out` 성공, 3개 모델 프로파일링 완료
  - compileall, unittest(66) 통과
- Persistent artifacts read/updated:
  - Read: `.ai/handoff.md`, `.ai/task-state.md`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `scripts/fetch_safety_models.py` (재작성) — fire 모델 대체 repo, `--all` 옵션, docstring 정리
  - `requirements.txt` — `huggingface_hub>=0.20.0` 추가
  - `scripts/stress_test.py` — `cam_id` 평가에 반영, `active_model_names` 속성 사용, `--json-out` 옵션, `event_type` 문자열로 저장
  - `src/vlm/ollama_provider.py` — thread-safe metrics
  - `src/pipeline/pipeline.py` — VLM TTL cache + bypass, `get_vlm_cache_stats()`, `reset_vlm_cache()`, `Any` import
  - `src/dashboard/app.py` — VLM 메트릭/캐시 섹션 + reset 버튼 (공개 메서드 사용)
  - `scripts/profile_pipeline.py` (신규) — cProfile + tracemalloc 프로파일링 도구
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
  - `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` — 3 models loaded, `failed_models={}`
  - `./.venv/bin/python scripts/stress_test.py --duration 30 --policy-mode` — `failed_frames=0`, `events_by_type={fire:15}`, `events_by_alert_level={critical:15}`, JSON saved to `reports/stress_phase9.json`
  - `./.venv/bin/python scripts/stress_test.py --duration 30 --policy-mode --cameras 2` — `failed_frames=0`
  - `./.venv/bin/python scripts/fetch_safety_models.py --all --json` — 두 모델 모두 OK
  - `./.venv/bin/python scripts/profile_pipeline.py --frames 5 --iterations 1 --pipeline-iterations 1 --json-out reports/profile_phase9.json` — 3개 모델 프로파일링 완료, 전체 파이프라인 avg 2890ms, peak memory 11.72MB
- Known risks:
  - 실제 VLM 호출은 Ollama 미가동 환경에서 메트릭 0 유지 (Ollama 실행 시 자동 기록)
  - `--cameras`는 single-threaded camera_id 시뮬레이션일 뿐 실제 멀티스레드 처리 아님
  - 모델 파일은 git 추적 대상이 아니므로 별도 artifact 관리 정책 필요
  - CUDA provider 미검증 (CPU baseline만)
- Questions for acceptance: Phase 9 전체 완료. 다음은 (1) Phase 10 계획 (plan 모드), (2) 현재 상태로 동결 (다른 프로젝트 학습 완료 대기)?
- Current phase: summarize
- Completed: Phase 9 M4 + M3 + M1 + M2 + Should-fix
- Blocked: Ollama 실측, 멀티스레드 RTSP, 커스텀 학습
- Next recommended phase: plan (Phase 10) 또는 done
- Stop condition: Phase 9 전체 완료

## Phase 9 전체 완료 요약

| 항목 | 상태 | 핵심 산출물 |
|---|---|---|
| M4: 모델 다운로드 | ✅ | `yolo26_ppe.onnx`, `yolo26_fire_smoke.onnx`, `--all` 지원, `huggingface_hub` 의존성 추가 |
| M3: stress test | ✅ | `scripts/stress_test.py`, `failed_frames=0`, `events_by_type={fire:15}` |
| M1: VLM observability | ✅ | `OllamaProvider` metrics, `SafetyPipeline` cache, 대시보드 VLM 섹션 |
| M2: 성능 프로파일링 | ✅ | `scripts/profile_pipeline.py`, cProfile + tracemalloc, 모델별/파이프라인별 레이턴시+메모리 |
| Should-fix: cache reset | ✅ | `reset_vlm_cache()` 공개 메서드, 대시보드 private attr 접근 제거 |

## Suggested next prompt
Phase 9 전체 완료. 다음 단계 선택:
- Phase 10 계획 (plan 모드)
- 현재 상태로 동결 (다른 프로젝트 학습 완료 대기)
- 사전학습 모델 파일 git ignore 정책 결정

---

## Phase 8 — Milestone 5.3: 대시보드/성능/보안 개선 (3순위) (완료 ✅)

M5.2에서 약속한 3순위 3개 작업을 완성. D3(카메라별 통계)로 통계 탭을 카메라 단위로 강화하고, P1(동적 프레임 skip)으로 정적 장면에서 CPU 사용량을 줄이며, S3(로그 URL 마스킹)으로 RTSP/HTTP 계정 비밀번호가 로그/이벤트 로그에 노출되지 않도록 보호. M1~M5.3 모두 완료되어 **Phase 8 완료** 선언.

## Build Handoff
- User intent: 3순위(D3, P1, S3) 완성 + Phase 8 종합 완료.
- Plan summary: `render_statistics()` 카메라별 상세 통계로 확장, `SafetyPipeline`에 dynamic_skip 모션 기반 스킵 추가, `src/utils/config.py`에 `mask_url()` / `mask_text()` 유틸리티 추가하고 `alert_manager.py` 로그 경로에 적용.
- Acceptance criteria:
  - 통계 탭에 요약 메트릭(총/카메라/High+Critical/최근) + 카메라 선택 필터 + 24시간 추이 + 비교 차트 + 요약 테이블
  - `SafetyPipeline(dynamic_skip=True, motion_threshold=0.02, static_skip_multiplier=4)`로 정적 장면에서 `skip_frames * 4` 적용
  - `rtsp://user:pw@host` 형태의 URL이 로그에 `rtsp://***:***@host`로 마스킹됨
  - compileall, unittest(66) 통과
- Persistent artifacts read/updated:
  - Read: `.ai/handoff.md`, `.ai/task-state.md`, `src/dashboard/app.py`, `src/pipeline/pipeline.py`, `src/utils/config.py`, `src/alerts/alert_manager.py`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `src/dashboard/app.py` — `render_statistics()` 카메라별 통계로 확장
  - `src/pipeline/pipeline.py` — `__init__`에 `dynamic_skip` / `motion_threshold` / `static_skip_multiplier`, `_process_camera()`에 모션 기반 스킵, `_detect_motion()` 추가
  - `src/utils/config.py` — `mask_url()`, `mask_text()` 유틸리티, `_RTSP_PATTERN` / `_HTTP_AUTH_PATTERN` 정규식
  - `src/alerts/alert_manager.py` — `from src.utils.config import mask_text`, `_log_event()` / `_log_batch()`에 `mask_text()` 적용
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- Known risks:
  - `dynamic_skip` 기본값 `False` — 대시보드/파이프라인에서 명시적으로 활성화 필요
  - `mask_text()`는 RTSP/HTTP만 마스킹 — file://, websocket:// 등 미처리
  - 카메라별 통계는 세션 내 이벤트만 집계 — 영구 저장 미구현
  - 모션 임계값(0.02), 정적 배수(4x)는 실 환경 튜닝 필요할 수 있음
- Questions for acceptance: Phase 8 전체 승인 후 다음 단계는 (1) 사전학습 모델 다운로드 + 추론 검증, (2) Phase 9 계획, (3) 현재 상태로 동결하고 다른 프로젝트 학습 완료 대기?
- Current phase: summarize
- Completed: **Phase 8 전체 완료** (M1~M5.3)
- Blocked: 실제 사전학습 모델 다운로드(네트워크 필요), 커스텀 학습(다른 프로젝트 학습 완료 후)
- Next recommended phase: build (사전학습 모델 다운로드 + 추론 검증) 또는 plan (Phase 9)
- Stop condition: Phase 8 완료, 학습은 다른 프로젝트 완료 후

## Phase 8 전체 완료 요약

| 마일스톤 | 상태 | 핵심 산출물 |
|---|---|---|
| M1: 사전학습 모델 통합 | ✅ | `class_adapter.py`, `fetch_safety_models.py`, `model_profiles.yaml` 확장 |
| M2: 데이터셋 인프라 | ✅ | `download_datasets.py`, `convert_to_yolo.py`, `validate_dataset.py`, `generate_dataset_yaml.py` |
| M3: 대시보드/문서화 | ✅ | 감사 로그/시스템 상태 탭, `deployment-guide.md`, `custom-model-training.md` |
| M4: 알림/내보내기/로그 뷰어 | ✅ | HTML 이메일, CSV/JSON 내보내기, 이벤트 로그 뷰어, SMTP 테스트 |
| M5.1: 자동 새로고침/보안 | ✅ | 자동 새로고침, 로그인 속도 제한, 세션 타임아웃 |
| M5.2: 알림 설정/배치 처리 | ✅ | 영구 YAML 저장, `AlertManager` 배치 처리, `_should_send_email()` |
| M5.3: 통계/성능/마스킹 | ✅ | 카메라별 통계, 동적 프레임 skip, URL 마스킹 |

## Suggested next prompt
Phase 8 전체 완료. 다음 단계 선택:
- 사전학습 모델 다운로드 및 추론 검증 (`scripts/fetch_safety_models.py --all`)
- Phase 9 계획 (plan 모드)
- 현재 상태로 동결하고 다른 프로젝트 학습 완료 대기

---

## Phase 8 — Milestone 5.2: 대시보드/성능/보안 개선 (2순위) (완료 ✅)

M5.1 review의 Must-fix(`.ai/handoff.md` 갱신)와 Should-fix(auto-refresh 예외 범위 좁히기)를 적용하고, M5.2의 D2(알림 설정 UI → 영구 YAML 저장 + AlertManager 연동)와 P2(AlertManager 배치 처리 로직)를 완성. `config/alert_preferences.yaml` 신규 생성, AlertManager는 초기화 시 이를 읽어 `_should_send_email()`로 발송 결정.

## Build Handoff
- User intent: M5.1 review에서 식별된 Must-fix/Should-fix 적용 + M5.2(D2 알림 설정 UI 완성 + P2 알람 배치 처리) 진행.
- Plan summary: `render_alert_settings()`에 영구 저장(`_save_alert_preferences()`) + 기본값 복구 버튼 추가, `config/alert_preferences.yaml` 신규 생성, AlertManager에 `_load_alert_preferences()` / `_should_send_email()` 추가하여 `_send_email()` / `_send_batch_email()`에서 사용. `handle_event()`를 배치 모드 지원으로 변경, `_maybe_flush_batch()` / `_flush_batch()` / `_log_batch()` / `_send_batch_email()` 신규 구현. auto-refresh 루프의 `except Exception: pass` 제거.
- Acceptance criteria:
  - 대시보드 "🔔 알림 설정" 탭에서 "💾 설정 저장" 시 `config/alert_preferences.yaml` 생성/갱신
  - "🔄 기본값으로 되돌리기" 버튼이 session state를 기본값으로 초기화
  - AlertManager가 초기화 시 `config/alert_preferences.yaml`을 읽고 발송 결정에 사용
  - `AlertConfig(batch_flush_interval=5.0)` 설정 시 `handle_event()`가 버퍼에 추가, 5초 후 또는 100건 도달 시 배치 flush
  - 배치 이메일은 medium+ 이벤트만 단일 이메일로 일괄 발송
  - M5.1 Should-fix: auto-refresh 루프의 `except Exception: pass` 제거
  - M5.1 Must-fix: `.ai/handoff.md`에 M5.1 핸드오프 추가 완료
  - compileall, unittest(66) 통과
- Persistent artifacts read/updated:
  - Read: `.ai/handoff.md`, `.ai/task-state.md`, `src/dashboard/app.py`, `src/alerts/alert_manager.py`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `src/dashboard/app.py` — `_load_alert_preferences()`, `_save_alert_preferences()`, `render_alert_settings()` 확장, auto-refresh 예외 제거
  - `src/alerts/alert_manager.py` — `import time`, `from typing import Dict, Any`, `AlertConfig` batch 필드, `_batch_buffer`/`_batch_timer`, `handle_event()` 배치 분기, `_maybe_flush_batch()`, `_flush_batch()`, `_log_batch()`, `_send_batch_email()`, `_load_alert_preferences()`, `_should_send_email()`
  - `config/alert_preferences.yaml` (신규)
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- Known risks:
  - AlertManager는 초기화 시점에 preferences 로드 — 대시보드에서 변경 후 파이프라인 재시작 필요
  - 배치 이메일은 개별 이벤트 이미지를 첨부하지 않음 (HTML 본문만)
  - Rate limiting은 여전히 session 기반 (M5.1 risk)
- Questions for acceptance: M5.2 승인 후 M5.3(카메라별 통계, 동적 프레임 skip, 로그 URL 마스킹) 진행?
- Current phase: summarize
- Completed: Phase 8 Milestone 5.2 — 알림 설정 UI 영구 저장 + AlertManager 배치 처리
- Blocked: 없음
- Next recommended phase: build (M5.3) 또는 review
- Stop condition: M5.1 review 피드백 반영 + M5.2 완성

## Suggested next prompt
Phase 8 M5.2 완료. 다음 단계 선택:
- M5.3 진행 (D3 카메라별 통계 + P1 동적 프레임 skip + S3 로그 URL 마스킹)
- 사전학습 모델 다운로드 및 추론 검증
- 현재 상태로 review 위임

---

## Phase 8 — Milestone 5.1: 대시보드/성능/보안 개선 (1순위) (완료 ✅)

대시보드에 자동 새로고침 컨트롤을 추가하고, 로그인 속도 제한(60초 내 5회 실패 → 60초 잠금) 및 세션 타임아웃(30분 무활동 → 자동 로그아웃) 보안을 강화. review에서 식별된 Must-fix(`.ai/handoff.md` 갱신)와 Should-fix(auto-refresh 예외 범위 좁히기) 적용 완료.

## Build Handoff
- User intent: 대시보드 자동 새로고침(UX), 로그인 속도 제한/세션 타임아웃(보안) — 1순위 3개.
- Plan summary: init_session_state()에 auto_refresh/last_activity/login_attempts/login_locked_until 추가, _is_rate_limited()/_record_login_failure() 신규, require_dashboard_auth()에 rate limiting + session timeout 통합, render_sidebar()에 새로고침 컨트롤, main() 끝에 auto-refresh 루프. tab7 중복 사용 버그 수정(tab8로 system status 분리).
- Acceptance criteria:
  - 대시보드 사이드바 "새로고침" 섹션에 체크박스(자동 새로고침 on/off) + 슬라이더(1~30초) 표시
  - 5회 로그인 실패 시 60초 동안 로그인 폼 잠금, 남은 시간 경고 표시
  - 30분 무활동 시 자동 로그아웃, "세션이 만료되었습니다" 경고 표시
  - session_timeout 이벤트 감사 로그 기록
  - compileall, unittest(66) 통과
- Persistent artifacts read/updated:
  - Read: `.ai/handoff.md`, `.ai/task-state.md`, `src/dashboard/app.py`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `src/dashboard/app.py` — init_session_state, require_dashboard_auth, render_sidebar, main, tab 수정
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- Known risks:
  - Rate limiting은 st.session_state 기반(브라우저 탭/쿠키 삭제 시 우회 가능) — 로컬 대시보드 용도로 수용
  - auto-refresh 루프는 매 interval마다 전체 스크립트 재실행 — 성능 영향 경미
  - M5.2 부분 코드(render_alert_settings, AlertConfig batch fields)가 다음 빌드 세션에서 완성 필요
- Questions for acceptance: M5.1 승인 후 M5.2(알림 설정 UI 연결 + AlertManager 배치 처리) 진행?
- Current phase: summarize
- Completed: Phase 8 Milestone 5.1 — 자동 새로고침, 로그인 속도 제한, 세션 타임아웃
- Blocked: M5.2 미완성 부분 — 알림 설정 UI는 session state만, AlertManager 배치 처리 로직 미구현
- Next recommended phase: build (M5.2 완료)
- Stop condition: M5.1 Must-fix 적용 후 M5.2 진행

## Suggested next prompt
Phase 8 M5.1 완료. M5.2 진행:
- D2 알림 설정 UI를 AlertManager/이벤트 정책과 연결 (session state → 실제 동작 반영)
- P2 AlertManager.handle_event()를 배치 모드로 변경, _flush_batch()/_maybe_flush() 구현
- 검증: compileall + unittest
- `.ai/task-state.md`, `.ai/handoff.md` 갱신

---

## Phase 8 — Milestone 4: 알림/내보내기/로그 뷰어 (완료 ✅)

이메일 알림을 HTML 포맷으로 강화하고 alert_level 필터링을 추가. 대시보드에 CSV/JSON 이벤트 내보내기 버튼과 alerts.log 기반 이벤트 로그 뷰어 탭을 추가. 시스템 상태 탭에 SMTP 연결 테스트 버튼을 추가.

## Build Handoff
- User intent: 추가 기능 개발 — 이메일 알림, 이벤트 내보내기, 로그 뷰어, SMTP 테스트.
- Plan summary: alert_manager.py에 HTML 이메일 + alert_level 필터 추가, dashboard에 CSV/JSON 내보내기 버튼 추가, alerts.log 파싱 이벤트 로그 뷰어 탭 추가, 시스템 상태 탭에 SMTP 테스트 버튼 추가.
- Acceptance criteria:
  - SMTP 이메일이 HTML 포맷으로 발송 (alert_level 색상 뱃지 포함)
  - `medium` 이상의 이벤트만 이메일 발송, `low`는 무시
  - 대시보드 "🔔 알람 로그" 탭에 CSV/JSON 내보내기 버튼이 있음
  - 대시보드 "📝 이벤트 로그" 탭에서 `logs/alerts.log` 내용 조회/필터 가능
  - 대시보드 "🔧 시스템 상태" 탭에서 SMTP 연결 테스트 가능
  - compileall, unittest(66) 통과
- Persistent artifacts read/updated:
  - Read: `.ai/handoff.md`, `.ai/task-state.md`, `src/alerts/alert_manager.py`, `src/dashboard/app.py`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `src/alerts/alert_manager.py` — `_send_email()` HTML 포맷, alert_level 필터
  - `src/dashboard/app.py` — 이벤트 내보내기, 이벤트 로그 탭, SMTP 테스트 버튼
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- Known risks:
  - 실제 SMTP 서버 연결 및 이메일 발송은 외부 SMTP 설정 필요
  - 이벤트 로그 뷰어는 `logs/alerts.log` 파일이 있을 때만 동작
  - 실제 데이터셋 다운로드/학습은 다른 프로젝트 완료 후
- Questions for acceptance: Phase 8 M4 알림/내보내기/로그 뷰어 승인 후, 다음 단계는 사전학습 모델 다운로드/추론 검증, 아니면 추가 기능 개발?
- Current phase: summarize
- Completed: Phase 8 Milestone 4 — 알림/내보내기/로그 뷰어
- Blocked: 실제 데이터셋 다운로드 및 모델 학습은 다른 프로젝트 학습 완료 후
- Next recommended phase: build (사전학습 모델 다운로드 및 추론 검증) 또는 build (추가 기능 개발)
- Stop condition: 알림/내보내기/로그 뷰어 완료. 실제 학습은 다른 프로젝트 완료 후.

## Suggested next prompt
Phase 8 M1 + M2 + M3 + M4 완료. 다음 단계 선택:
- 사전학습 모델 다운로드 및 실제 추론 검증
- 추가 기능 개발
- Phase 8 완료 대기 (다른 프로젝트 학습 완료 후 커스텀 모델 학습)

---

## Phase 8 — Milestone 3: 대시보드 개선 및 문서화 (완료 ✅)

대시보드에 감사 로그 뷰어, 시스템 상태 탭, 개선된 이벤트 필터를 추가하고, 배포 가이드와 커스텀 모델 학습 가이드 문서를 신규 작성. development-roadmap.md를 현재 상태로 갱신.

## Build Handoff
- User intent: 대시보드 기능 보강 및 문서화. 모델 학습 없이 UI/UX와 문서 품질 향상.
- Plan summary: 감사 로그 뷰어 탭, 시스템 상태 탭 (CPU/메모리/디스크/ONNX), 이벤트 필터 개선 (alert_level + 시간 범위), docs/deployment-guide.md, docs/custom-model-training.md, docs/development-roadmap.md 갱신.
- Acceptance criteria:
  - 대시보드에 "🔍 감사 로그" 탭이 있고 JSONL 오디트 로그를 필터링/조회 가능
  - 대시보드에 "🔧 시스템 상태" 탭이 있고 CPU/메모리/디스크/ONNX Runtime 정보 표시
  - 알람 로그에 alert_level 필터와 시간 범위 필터가 있음
  - `docs/deployment-guide.md`가 DGX Spark/일반 배포 절차를 포함
  - `docs/custom-model-training.md`가 데이터셋 다운로드→학습→ONNX export 전체 가이드를 포함
  - `docs/development-roadmap.md`가 Phase 7 완료, Phase 8 진행 중으로 갱신
  - compileall, unittest(66) 통과
- Persistent artifacts read/updated:
  - Read: `.ai/handoff.md`, `.ai/task-state.md`, `src/dashboard/app.py`, `docs/development-roadmap.md`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `src/dashboard/app.py` — 탭 추가 및 필터 개선
  - `docs/deployment-guide.md` (신규)
  - `docs/custom-model-training.md` (신규)
  - `docs/development-roadmap.md` (갱신)
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- Known risks:
  - Streamlit 대시보드는 실제 실행 시 브라우저에서 UI 확인 필요
  - psutil 미설치 시 시스템 상태 탭이 기본 정보만 표시
  - 감사 로그 뷰어는 pandas 미설치 시 수동 테이블로 폴백
  - 실제 데이터셋 다운로드/학습은 다른 프로젝트 완료 후
- Questions for acceptance: Phase 8 M3 대시보드/문서화 승인 후, 다음 단계는 사전학습 모델 다운로드/추론 검증, 아니면 추가 기능 개발?
- Current phase: summarize
- Completed: Phase 8 Milestone 3 — 대시보드 개선 및 문서화
- Blocked: 실제 데이터셋 다운로드 및 모델 학습은 다른 프로젝트 학습 완료 후
- Next recommended phase: build (사전학습 모델 다운로드 및 추론 검증) 또는 build (추가 기능 개발)
- Stop condition: 대시보드/문서화 완료. 실제 학습은 다른 프로젝트 완료 후.

## Suggested next prompt
Phase 8 M1 + M2 + M3 완료 (사전학습 모델 통합 + 데이터셋 인프라 + 대시보드/문서화). 다음 단계 선택:
- 사전학습 모델 다운로드 및 실제 추론 검증
- 추가 기능 개발
- Phase 8 완료 대기 (다른 프로젝트 학습 완료 후 커스텀 모델 학습)

---

## Phase 8 — Milestone 2: 데이터셋 인프라 구축 (완료 ✅)

사전학습 모델 이후 커스텀 학습을 위해 데이터셋 다운로드, 변환, 검증 파이프라인을 코드로 준비. Kaggle/Roboflow/URL 다운로드, Pascal VOC/COCO/CSV/Labelme → YOLO 변환, 이미지/라벨 무결성 검증, Ultralytics 학습용 dataset.yaml 생성까지 자동화. 실제 데이터 다운로드와 학습은 다른 프로젝트 학습 완료 후 실행.

## Build Handoff
- User intent: Phase 8 Track B — 데이터셋 수집/변환/검증 인프라를 코드로 준비. 모델 학습 없이 스크립트와 유틸리티만 작성.
- Plan summary: download_datasets.py로 다중 출처 데이터셋 다운로드, convert_to_yolo.py로 다양한 형식 → YOLO 변환, validate_dataset.py로 무결성 검증, generate_dataset_yaml.py로 학습 설정 생성.
- Acceptance criteria:
  - `scripts/download_datasets.py`가 Kaggle/Roboflow/URL에서 데이터셋 다운로드 가능
  - `scripts/convert_to_yolo.py`가 VOC/COCO/CSV/Labelme → YOLO 변환 가능
  - `scripts/validate_dataset.py`가 이미지/라벨 무결성 검증 및 클래스 분포 통계 출력
  - `scripts/generate_dataset_yaml.py`가 Ultralytics YOLO 학습용 dataset.yaml 생성
  - compileall, unittest(66) 통과
- Persistent artifacts read/updated:
  - Read: `.ai/handoff.md`, `.ai/task-state.md`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `scripts/download_datasets.py` (신규)
  - `scripts/convert_to_yolo.py` (신규)
  - `scripts/validate_dataset.py` (신규)
  - `scripts/generate_dataset_yaml.py` (신규)
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- Known risks:
  - 실제 데이터셋 다운로드는 네트워크 연결 및 API 키 필요
  - 데이터셋 변환/검증은 실제 다운로드 후 실행
  - 학습 스크립트는 Milestone 3에서 작성 예정
  - 실제 모델 학습은 다른 프로젝트 학습 완료 후 실행
- Questions for acceptance: Phase 8 M2 데이터셋 인프라 승인 후, 다음 단계는 사전학습 모델 다운로드/추론 검증, 아니면 다른 기능 개발?
- Current phase: summarize
- Completed: Phase 8 Milestone 2 — 데이터셋 인프라 코드 준비 완료
- Blocked: 실제 데이터셋 다운로드 및 모델 학습은 다른 프로젝트 학습 완료 후
- Next recommended phase: build (사전학습 모델 다운로드 및 추론 검증) 또는 build (다른 기능 개발)
- Stop condition: 데이터셋 인프라 코드 준비 완료. 실제 학습은 다른 프로젝트 완료 후.

## Suggested next prompt
Phase 8 M1 + M2 완료 (사전학습 모델 통합 + 데이터셋 인프라). 다음 단계 선택:
- 사전학습 모델 다운로드 및 실제 추론 검증
- 다른 기능 개발 (대시보드 개선, 문서화 등)
- Phase 8 완료 대기 (다른 프로젝트 학습 완료 후 커스텀 모델 학습)

---

## Phase 8 — Milestone 1: 사전학습 모델 통합 (완료 ✅)

---

## Phase 7 — Site-specific deployment hardening ✅ (Fully Approved)

Completed the bounded Phase 7 hardening follow-up after the initial operations scaffold. The service template is more explicit about deployment-specific values and writable paths are narrower. The healthcheck is now read-only by default and only creates runtime directories when explicitly requested. No service was installed or started, no `sudo` was used, and Phase 8 was not started.

### Should-fix #1 Applied
- Updated `_check_runtime_dirs` in `scripts/healthcheck.py` to include `--create-dirs` hint in the error message when a runtime directory is missing.
- Changed: `"Runtime directory is missing"` → `"Runtime directory is missing (use --create-dirs to create it)"`
- All verification passed after the change.

## Review Handoff (Final)
- User intent: Continue Phase 7 Operations/Deployment hardening, apply review Should-fix #1, and finalize acceptance.
- Plan summary: Keep the existing operations scaffold, then tighten site-specific systemd guidance, minimize service writable paths, make healthcheck default behavior non-mutating, preserve explicit repair/create-dirs behavior, strengthen secret/URL redaction, and add UX hint for missing directories.
- Acceptance criteria:
  - `systemd/ksi-cctv.service` remains a deployment template and does not install/start anything.
  - Site-specific `User`, `Group`, `WorkingDirectory`, and env-file expectations are clear.
  - Systemd writable paths are limited to required runtime/data/upload paths and current uploaded-model registration needs.
  - `scripts/healthcheck.py` does not create directories by default.
  - Directory creation is explicit via `--create-dirs` / `--repair-dirs`.
  - Missing directory error message suggests `--create-dirs` flag.
  - Healthcheck details/errors continue to avoid printing secrets or raw RTSP/HTTP URLs.
  - Verification commands pass or report bounded non-blocking warnings.
- Persistent artifacts read/updated:
  - Read: `.ai/handoff.md`, `scripts/healthcheck.py`, `systemd/ksi-cctv.service`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `scripts/healthcheck.py` — Should-fix #1 applied
  - `systemd/ksi-cctv.service`
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run (after Should-fix #1):
  - `bash -n scripts/install.sh` — passed
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python scripts/healthcheck.py --json` — passed (`ok: true`, 12 passes, 1 warning, 0 failures)
    - Warning: CUDA provider unavailable; CPU fallback used successfully.
  - `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
  - `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` — passed
  - Optional: `systemd-analyze verify systemd/ksi-cctv.service`
    - Blocked by unrelated local system unit permission issue: `dgx-dashboard-admin.service: Failed to open /etc/systemd/system/dgx-dashboard-admin.service: 허가 거부`.
- Known risks:
  - No service was installed/enabled/started; this remains a template/scaffold milestone.
  - `systemd/ksi-cctv.service` still uses template values (`User=ksi-cctv`, `Group=ksi-cctv`, `/opt/ksi-cctv`) and needs per-site review.
  - Current local environment lacks CUDA provider; DGX Spark GPU provider validation remains required.
  - Healthcheck does not probe RTSP or VLM by default; use `--check-cameras` / `--check-vlm` explicitly when safe.
  - `config/model_profiles.yaml` remains writable because Phase 6 uploaded model registration currently writes there via PyYAML.
  - Full `systemd-analyze verify` acceptance is not proven in this local environment due to the unrelated system permission issue above.
- Questions for acceptance: Phase 7 hardening is now fully approved. Should the next accepted milestone be DGX/GPU validation or Phase 8 custom safety model planning?
- Current phase: summarize
- Completed: Phase 7 site-specific deployment hardening milestone + Should-fix #1.
- Blocked: Full local `systemd-analyze verify` signal is blocked by unrelated system unit permission; real DGX/CUDA validation remains out of scope.
- Next recommended phase: done (or proceed to next milestone planning)
- Stop condition: Stop after hardening and handoff; do not install services, use sudo, start Phase 8, or alter runtime architecture without new plan/build instruction.

## Suggested next prompt
Phase 7 hardening is approved. Choose next step:
- DGX Spark CUDA provider 검증 및 GPU fallback 테스트
- Phase 8 Custom Safety Model planning 시작

---

## Plan-owned artifact creation — Completed in plan ✅

Created the missing Plan-owned planning artifacts directly under project `.ai/` and updated this shared handoff. No source code, tests, app config, migrations, or docs outside `.ai/` were modified.

## Build Handoff
- Goal: Persist project planning state by creating the four missing Plan-owned `.ai` artifacts.
- Scope: `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, `.ai/review-checklist.md`, and `.ai/handoff.md` only.
- Non-goals: Do not modify source code, tests, runtime config, migrations, docs outside `.ai/`, or begin Phase 7.
- Persistent artifacts read/updated:
  - Read: existing `.ai/handoff.md`, `.ai/task-state.md` state via current project context
  - Created: `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, `.ai/review-checklist.md`
  - Updated: `.ai/handoff.md`
- Files likely affected:
  - `.ai/project-brief.md`
  - `.ai/plan.md`
  - `.ai/decisions.md`
  - `.ai/review-checklist.md`
  - `.ai/handoff.md`
- Implementation steps:
  1. Created `.ai/project-brief.md` from the previous Plan response.
  2. Created `.ai/plan.md` from the previous Plan response.
  3. Created `.ai/decisions.md` from the previous Plan response.
  4. Created `.ai/review-checklist.md` from the previous Plan response.
  5. Updated `.ai/handoff.md` with this plan persistence handoff.
- Verification:
  - Verify `.ai/*` contains all expected files: `project-brief.md`, `plan.md`, `decisions.md`, `review-checklist.md`, `task-state.md`, `handoff.md`.
- Review focus:
  - Confirm artifacts match project reality and the previous Plan response.
  - Confirm no out-of-scope files were modified.
- Risks:
  - Artifact content summarizes current project state and should be revisited after Phase 7 or major scope changes.
- Acceptance criteria:
  - Four Plan-owned artifact files exist and are non-empty.
  - `.ai/handoff.md` records the persistence result.
  - No source/test/config/doc files outside `.ai/` are changed by this plan persistence step.
- Current phase: plan
- Completed: Plan-owned artifacts created and handoff updated.
- Blocked: None.
- Next recommended phase: review
- Stop condition: Stop after artifact creation/verification; do not begin Phase 7.

## Suggested review prompt
Review the newly created `.ai` planning artifacts for acceptance.

Check whether `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, and `.ai/review-checklist.md` accurately reflect the current KSI-CCTV project state and whether the next phase should be Phase 7 Operations/Deployment planning/build.

---

## OpenCode harness improvement for plan `.ai/**` writes ✅

Updated the global OpenCode harness so future `plan` sessions more explicitly treat `.ai/**` planning artifacts as the only allowed write exception while remaining source-read-only.

## Review Handoff
- User intent: Remove/avoid the plan read-only blocker for Plan-owned `.ai` artifacts and improve the OpenCode harness.
- Plan summary: Keep build blocked from Plan-owned artifacts, but strengthen the plan agent/global instructions so `plan` directly writes `.ai/**` planning artifacts and does not defer them to build because of generic read-only wording.
- Acceptance criteria:
  - Do not loosen build permissions for Plan-owned artifacts.
  - Clarify that `plan` is source-read-only but `.ai/**` planning-artifact-writable.
  - Update harness validation so the clarification is checked.
  - Run `python3 /home/ksi/.config/opencode/harness-check.py` successfully.
- Persistent artifacts read/updated:
  - Read: `.ai/task-state.md`, `.ai/handoff.md`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `/home/ksi/.config/opencode/agent/plan.md`
  - `/home/ksi/.config/opencode/opencode.jsonc`
  - `/home/ksi/.config/opencode/AGENTS.md`
  - `/home/ksi/.config/opencode/harness-check.py`
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 /home/ksi/.config/opencode/harness-check.py` — 172 passed, 0 warnings, 0 failures
- Known risks:
  - Running OpenCode sessions keep already-loaded config/instructions; restart OpenCode for these changes to take effect.
  - If OpenCode core injects an immutable system-level read-only constraint, config cannot fully remove that core behavior. The harness now clearly documents and verifies the intended `.ai/**` exception.
- Questions for acceptance: After restart, should `plan` be retried to create `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, and `.ai/review-checklist.md` directly?
- Current phase: summarize
- Completed: Global harness clarification and harness-check update.
- Blocked: Current running session may not reflect restarted config; Plan-owned artifact creation still belongs to a fresh/restarted `plan` phase.
- Next recommended phase: plan
- Stop condition: Stop after harness improvement; do not create Plan-owned project artifacts from build and do not start Phase 7.

## Suggested next prompt
After restarting OpenCode, run:

```md
Create the missing Plan-owned artifacts directly in plan mode:
- .ai/project-brief.md
- .ai/plan.md
- .ai/decisions.md
- .ai/review-checklist.md

Use the exact content from the previous Plan response code blocks.
Then update .ai/handoff.md and verify .ai/* contains all expected files.
Do not modify source code, tests, configs, or docs outside .ai/.
```

---

## Plan-owned artifact creation request — Blocked in build ⚠️

The user requested creation of four Plan-owned `.ai` artifacts using the previous Plan response content:

1. `.ai/project-brief.md`
2. `.ai/plan.md`
3. `.ai/decisions.md`
4. `.ai/review-checklist.md`

This build phase did **not** create those files because the current workflow contract says build must not create or update Plan-owned artifacts. Build may only record the blocker in `.ai/task-state.md` and `.ai/handoff.md`, then return to `plan`.

## Handoff Contract
- User intent: Create missing Plan-owned `.ai` artifacts and update handoff.
- Current phase: summarize
- Completed:
  - Confirmed current `.ai/` only contains `.ai/task-state.md` and `.ai/handoff.md`.
  - Recorded the artifact ownership blocker in `.ai/task-state.md` and this `.ai/handoff.md`.
- Changed files / likely affected files:
  - Changed: `.ai/task-state.md`, `.ai/handoff.md`
  - Not created by build: `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, `.ai/review-checklist.md`
- Verification / proposed verification:
  - Verified via `.ai/*` listing that the four Plan-owned files are absent.
  - Proposed for plan: create the four files from the previous Plan response, then verify `.ai/*` includes all six expected files.
- Acceptance criteria:
  - Plan-owned artifacts should be created by `plan`, not build.
  - Build must not broaden artifact ownership or silently violate workflow boundaries.
- Known risks / blockers:
  - The requested artifact creation is blocked in build by design.
  - Previous Plan response contains the intended file contents and should be reused by `plan`.
- Next recommended phase: plan
- Stop condition: Stop after recording the blocker; do not create Plan-owned files or begin Phase 7.
- Suggested next prompt:
  ```
  Create the missing Plan-owned artifacts directly in plan mode:
  - .ai/project-brief.md
  - .ai/plan.md
  - .ai/decisions.md
  - .ai/review-checklist.md

  Use the exact content from the previous Plan response code blocks.
  Then update .ai/handoff.md and verify .ai/* contains all expected files.
  Do not modify source code, tests, configs, or docs outside .ai/.
  ```

---

## Phase 6 — Review Should-fix resolution ✅

Resolved only the two requested Phase 6 review Should-fix items: delete confirmation and broader verification.

## Review Handoff
- User intent: Fix only the two Phase 6 review Should-fix items without broadening scope.
- Plan summary: Add a Streamlit confirmation pattern before uploaded model deletion, then run compileall, focused upload tests, full unittest suite, and policy-mode E2E smoke.
- Acceptance criteria:
  - Dashboard model deletion must require explicit confirmation before the delete button can execute.
  - Run `python3 -m compileall -q src tests scripts`.
  - Run `./.venv/bin/python -m unittest discover tests -p 'test_upload_validator.py' -v`.
  - Run `./.venv/bin/python -m unittest discover tests -v`.
  - Run `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode`.
  - Do not implement Phase 7 or additional upload hardening.
- Persistent artifacts read/updated:
  - Read: `.ai/task-state.md`, `.ai/handoff.md`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
  - Plan-owned artifacts remain missing and were not created by build: `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, `.ai/review-checklist.md`
- Changed files:
  - `src/dashboard/app.py` — added per-model `삭제 확인` checkbox and disables delete until checked
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python -m unittest discover tests -p 'test_upload_validator.py' -v` — 6 tests OK
  - `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
  - `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` — passed
- Known risks:
  - Runtime upload still writes `config/model_profiles.yaml` via PyYAML, so comment/format preservation is not guaranteed.
  - ONNX Runtime emitted a GPU device discovery warning during verification, but execution continued with `CPUExecutionProvider` and verification passed.
  - Plan-owned `.ai` artifacts are still missing by design in build context.
- Questions for acceptance: Are the two Should-fix items now acceptable, with YAML formatting preservation left as optional/future work?
- Current phase: summarize
- Completed: Phase 6 review Should-fix resolution.
- Blocked: None for this bounded milestone; Plan-owned artifact creation remains intentionally out of scope for build.
- Next recommended phase: review
- Stop condition: Stop after these two Should-fix items; do not start Phase 7 or optional upload hardening without a new plan/build instruction.

## Suggested review prompt
Review the current diff for acceptance.

Check only the Phase 6 review Should-fix resolution:
1. Uploaded model deletion requires explicit confirmation before delete executes.
2. Compile, focused upload tests, full unittest suite, and policy-mode smoke all passed.
If accepted, state whether the next step should be done, plan, or a new bounded build milestone.

---

## Phase 6 — User Model Upload foundation ✅

Implemented one bounded milestone from the Build Handoff: secure ONNX upload validation, shape validation, isolated disabled-by-default registration, admin-gated dashboard entry point, audit logging, and focused tests.

## Review Handoff
- User intent: Implement Phase 6 User Model Upload as one bounded milestone without broadening scope.
- Plan summary: Add a safe ONNX upload validator, store uploaded models in isolated directories, register them disabled-by-default, expose dashboard model upload/activate/deactivate/delete controls only to authenticated admins, and cover allow/deny/security cases with focused tests.
- Acceptance criteria:
  - ONNX only; `.pt`, `.pkl`, `.py`, executables/non-ONNX blocked by extension.
  - Filename/path traversal does not control storage path.
  - File size is checked before ONNX Runtime validation.
  - ONNX load + static input H/W shape validation + synthetic inference smoke test run with bounded timeout.
  - Uploaded models are stored under `models/uploaded/{model_id}/model.onnx`.
  - Uploaded profiles are registered as `enabled: false` by default.
  - Upload/activate/deactivate/delete are admin-gated and audit logged in dashboard.
  - Focused upload validator tests pass.
- Persistent artifacts read/updated:
  - Read: `.ai/task-state.md`, `.ai/handoff.md`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
  - Plan-owned artifacts remain missing and were not created by build: `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, `.ai/review-checklist.md`
- Changed files:
  - `src/models/__init__.py`
  - `src/models/upload_validator.py`
  - `src/dashboard/app.py`
  - `tests/test_upload_validator.py`
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python -m unittest discover tests -p 'test_upload_validator.py' -v` — 6 tests OK
- Known risks:
  - Full unit suite and E2E smoke were not rerun; verification was intentionally scoped to the smallest relevant checks.
  - Dashboard delete action is admin-gated/audited but has no second confirmation step.
  - Runtime upload writes to `config/model_profiles.yaml` with PyYAML and may rewrite comments/formatting.
  - Actual untrusted ONNX production use still deserves acceptance/security review.
  - Plan-owned `.ai` artifacts are still missing by design in build context.
- Questions for acceptance: Is Phase 6 foundation acceptable, and should review require a delete confirmation or config-format preservation before approval?
- Current phase: summarize
- Completed: Phase 6 User Model Upload foundation.
- Blocked: None for this bounded milestone; Plan-owned artifact creation remains intentionally out of scope for build.
- Next recommended phase: review
- Stop condition: Stop after Phase 6 foundation; do not begin Phase 7 or additional upload hardening without review/plan decision.

## Suggested review prompt
Review the current diff for acceptance.

Check whether it satisfies the original request and is ready to deliver.
Focus especially on the Phase 6 security-review conditions: ONNX-only validation, path traversal mitigation, isolated storage, file-size limit, ONNX load/smoke validation with timeout, disabled-by-default registration, admin authorization, and audit logging.
If not, separate findings into Must-fix, Should-fix, and Optional.
Also state whether the next step should be build or plan.

---

## Phase 5 — Must-fix Resolution ✅

All 2 Must-fix items from review have been resolved:

| Item | Description | Status |
|---|---|---|
| M1 | Added `Any` to typing imports (`from typing import Any, Dict, List`) | ✅ Fixed |
| M2 | Renamed duplicate `init_session_state()` back to `require_dashboard_auth()` | ✅ Fixed |

**Verification after fixes:**
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — **60 tests OK** (unchanged)

## Review Handoff
- User intent: Implement Phase 5 Dashboard Settings UI — camera/domain/event/model selection.
- Plan summary: Add helper functions for profile loading, new "⚙️ 카메라 설정" tab with domain selector + event checkboxes + model status display, apply profiles to pipeline at runtime, add alert_level display in event cards.
- Acceptance criteria: 
  - Dashboard shows camera settings UI (domain/events/models).
  - Settings applied via `set_camera_profile()` when pipeline starts.
  - Model active/failed status displayed.
  - Existing dashboard login, events, VLM display unchanged.
  - compileall + unittest + smoke test pass.
- Persistent artifacts read/updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `src/dashboard/app.py` — Phase 5 features added (helpers, settings tab, alert_level display)
- Verification run:
  - `python3 -m compileall -q src tests scripts` — passed
  - `./.venv/bin/python -m unittest discover tests -v` — **60 tests OK** (unchanged)
  - `scripts/e2e_smoke_test.py --policy-mode` — passed
- Known risks:
  - Plan-owned artifacts still missing (`.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, `.ai/review-checklist.md`) — build permission limitation.
  - Settings are runtime-only; no persistent save to config files yet.
  - AlertManager lacks full `alert_level` SMTP filtering (future Phase).
- Questions for acceptance: Phase 5 Dashboard Settings UI complete. Review로 넘길까요, 아니면 다음 bounded build milestone(Phase 6 User Model Upload)을 계획할까요?
- Current phase: summarize
- Completed: Phase 5 Dashboard Settings UI.
- Blocked: Plan-owned artifact creation (by design).
- Next recommended phase: review
- Stop condition: Stop after Phase 5; do not start Phase 6 without a new bounded build instruction or planning decision.

## Suggested review prompt
Review the current diff for acceptance.

Check whether it satisfies the original request and is ready to deliver.
If not, separate findings into Must-fix, Should-fix, and Optional.
Also state whether the next step should be build or plan.

---

# Planning Handoff (from previous planning phase)

## Summary
`.ai/` planning artifact 4개(project-brief, plan, decisions, review-checklist) 생성이 권한 제한으로 인해 **차단**되었습니다. 현재 편집 가능한 파일은 `task-state.md`와 `handoff.md`만 가능합니다.

## Artifacts NOT created (permission denied)
1. `.ai/project-brief.md` — project brief content designed but not written
2. `.ai/plan.md` — plan content designed but not written  
3. `.ai/decisions.md` — decisions content designed but not written
4. `.ai/review-checklist.md` — checklist content designed but not written

## Resolution needed
- Build 모드에서 plan-owned artifact 생성이 권한으로 차단됨
- `plan` agent 또는 permission 조정 후 생성 필요
- 기존 `docs/` 파일들은 이미 충분한 정보를 포함하고 있음

## Phase 5 implementation status
- ✅ Dashboard Settings UI tab 추가 (도메인 선택, 이벤트 체크박스, 모델 상태)
- ✅ Helper functions for profile loading
- ✅ Runtime profile application via `set_camera_profile()`
- ✅ alert_level display in event cards
- ✅ Policy metadata display (source_models, rule_name, vlm_needed)
- ✅ 60 tests OK, smoke test passed

- User intent: Implement one bounded milestone to clarify `.ai` planning artifact ownership in the OpenCode harness, without broadening build permissions or starting Phase 5.
- Plan summary: Keep build blocked from Plan-owned `.ai` files, clarify global/agent instructions so build records blockers and returns to plan, and plan directly creates missing Plan-owned artifacts.
- Acceptance criteria:
  - Do not grant build permission to edit `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, or `.ai/review-checklist.md`.
  - Clarify ownership/handoff rules in global harness instructions.
  - Run `python3 ~/.config/opencode/harness-check.py` successfully.
  - Do not implement Phase 5 Dashboard UI.
- Persistent artifacts read/updated:
  - Read: `.ai/task-state.md`, `.ai/handoff.md`, `/home/ksi/.config/opencode/AGENTS.md`, `/home/ksi/.config/opencode/agent/build.md`, `/home/ksi/.config/opencode/agent/plan.md`
  - Updated: `.ai/task-state.md`, `.ai/handoff.md`
- Changed files:
  - `/home/ksi/.config/opencode/AGENTS.md`
  - `/home/ksi/.config/opencode/agent/build.md`
  - `/home/ksi/.config/opencode/agent/plan.md`
  - `.ai/task-state.md`
  - `.ai/handoff.md`
- Verification run:
  - `python3 /home/ksi/.config/opencode/harness-check.py` — passed, 124 checks, 0 warnings, 0 failures
- Known risks:
  - Plan-owned artifacts are still missing in this project:
    - `.ai/project-brief.md`
    - `.ai/plan.md`
    - `.ai/decisions.md`
    - `.ai/review-checklist.md`
  - This is intentional for build. The next `plan` phase should create them directly because plan owns those files and has `.ai/**` write permission.
  - OpenCode must be restarted for global instruction/agent changes to fully apply to future sessions.
- Questions for acceptance: Is the harness ownership clarification acceptable, and should the next phase be `plan` to create the four missing Plan-owned `.ai` artifacts?
- Current phase: summarize
- Completed:
  - Clarified build/plan artifact ownership in global AGENTS.md.
  - Clarified build behavior when asked to create Plan-owned artifacts.
  - Clarified plan responsibility to create missing Plan-owned artifacts directly.
  - Verified harness with `harness-check.py`.
- Blocked:
  - Build did not create `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, or `.ai/review-checklist.md` by design.
- Next recommended phase: plan
- Stop condition: Stop after harness clarification; do not begin Phase 5 implementation until `.ai` planning artifacts are created and accepted.

## Suggested review prompt
Review the harness ownership clarification milestone for acceptance.

Check that build permissions were not broadened, ownership rules are clearer, harness-check passed, and missing Plan-owned artifacts are correctly handed off to plan.
If accepted, the next step should be plan to create `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, and `.ai/review-checklist.md`.
