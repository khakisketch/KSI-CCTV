# KSI 코드 구조화·중앙화·일관성 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 설정·카메라·파이프라인·스토리지·API의 SSOT 분산과 이중 경로를 순차적으로 해소하고, pose/VLM/open-vocab 확장을 기본 OFF 격리로 배선한다.

**Architecture:** 구조 안정화(U1–U5) → 확장 배선(U6) → 게이트·문서 마감(U7). 각 Unit은 독립 테스트 가능한 산출물로 끝나며 순서를 고정한다.

**Tech Stack:** Python 3.10 · pytest(`-B -p no:cacheprovider`) · ruff · mypy · FastAPI · Next.js

**Spec:** Plan-mode 확정 내용(2026-09-13 chat: 전체·단위 계획). SSOT 근거: `docs/release-gate.md`(v1 범위), `docs/architecture.md`, `src/release_capabilities.py`(불변 실험 게이팅).

## Global Constraints

- 테스트는 `python3 -B -m pytest tests/ -p no:cacheprovider`로 실행한다 (`src/**/__pycache__` 생성 금지).
- VLM 실패는 후보 이벤트를 억제하지 않고 `unverified` alert로 보존한다 (silent suppression 금지).
- U6 확장분은 기본 OFF + 별도 qualification 게이트로 격리하고 v1 출고선을 깨지 않는다.
- 한 공유 워크트리당 작성자 1명. 커밋·푸시·배포는 명시적 승인 후에만.
- 한국어로 응답한다. 기술 용어는 원어 유지.

---

### Task U1: 설정 SSOT 중앙화 (주석·문서 수준, 행위 변경 없음)

**Files:**
- Modify: `config/default.yaml` (cameras/models/detection 섹션 주석만)
- Test: `tests/test_config_ssot.py` (신규)

**Interfaces:**
- Consumes: 기존 5개 YAML/JSON + env (로드 동작 변경 없음)
- Produces: `default.yaml` 내 SSOT 포인터 명시 (모델→`model_profiles.yaml`, 정책→`event_policies.yaml`, 카메라는 `cameras.json` legacy + managed 이관 예고, `target_classes`/`vlm_trigger_classes`는 legacy 전용 표기)

- [ ] **Step 1: Write the failing test**

```python
"""설정 SSOT 소유권 회귀 테스트 (U1). 행위 변경 없이 문서 포인터를 고정한다."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def test_default_yaml_points_to_model_profiles_ssot():
    assert "config/model_profiles.yaml" in _read("config/default.yaml")


def test_default_yaml_points_to_event_policies_ssot():
    assert "config/event_policies.yaml" in _read("config/default.yaml")


def test_default_yaml_camera_source_matches_loader():
    text = _read("config/default.yaml")
    assert "config/cameras.json" in text
    assert "config/cameras.yaml에서 관리" not in text


def test_detection_target_classes_marked_legacy():
    assert "legacy" in _read("config/default.yaml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -B -m pytest tests/test_config_ssot.py -p no:cacheprovider -q`
Expected: FAIL (SSOT 포인터·legacy 표기 없음)

- [ ] **Step 3: Write minimal implementation**

`config/default.yaml` 주석만 수정: cameras 섹션의 `cameras.yaml` 표기를 `cameras.json(legacy)` + managed 이관 예고로 교체, `models.yolo`에 런타임 SSOT=`model_profiles.yaml` 명시, `detection.target_classes`/`vlm_trigger_classes`에 legacy 전용 + policy-mode SSOT=`event_policies.yaml` 명시.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -B -m pytest tests/test_config_ssot.py tests/test_model_registry.py tests/test_policy.py -p no:cacheprovider -q`
Expected: PASS

- [ ] **Step 5: Static check**

Run: `git diff --check`
Expected: PASS

### Task U2: 카메라 managed 정본 이관

**Files:**
- Modify: `src/run.py`, `src/api/app.py`, `src/cameras/repository.py`
- Test: `tests/test_camera_ssot.py`

**Interfaces:**
- Consumes: `CameraRepository(config/cameras/)` 정본
- Produces: legacy `cameras.json`은 읽기전용 import, `managed_profile{domain, enabled_events, zones}` 단일형, legacy 경로에도 MediaMTX sync 적용 또는 명시적 skip+경고

- [ ] **Step 1: Write the failing test** — legacy-only 카메라의 MediaMTX sync 누락 재현 + `detection_type`/`domain`/`profile` 혼용 시 단일형 정규화 assert (구체 코드는 실행 시 작성, 플레이스홀더 금지)
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Write minimal implementation** — 명칭을 `domain`+`enabled_events`로 통일(`detection_type`은 읽기호환 별칭), 빈 `config/cameras/` 시드 마이그레이션
- [ ] **Step 4: Run test to verify it passes** — `pytest tests/test_camera_ssot.py` + 카메라 등록·reconcile 관련 기존 테스트
- [ ] **Step 5: Static check** — `git diff --check`

### Task U3: 파이프라인 단일경로 (legacy 격리)

**Files:**
- Modify: `src/pipeline/pipeline.py`, `src/policy/rule_evaluators.py`, `src/tracking/`
- Test: `tests/test_pipeline_singlepath.py`

**Interfaces:**
- Consumes: `MultiModelRegistry + EventPolicyEngine + ByteTrackManager`
- Produces: policy 단일 실행, tracking 룰(`track_line_crossing`/`track_loitering`)의 evaluator 승격, legacy는 `LEGACY_FALLBACK=1` 명시 opt-in

- [ ] **Step 1: Write the failing test** — 동일 입력에 policy/legacy 이벤트명(`fire` vs `fire_detected`) 불일치 증명
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Write minimal implementation**
- [ ] **Step 4: Run test to verify it passes** — 기존 `test_pipeline.py` 포함
- [ ] **Step 5: Static check** — `git diff --check`

### Task U4: 스토리지·알람 단일진실

**Files:**
- Modify: `src/alerts/alert_manager.py`, `src/api/service.py`, `src/events/models.py`, `src/storage/`
- Test: `tests/test_storage_truth.py`

**Interfaces:**
- Consumes: `SafetyEvent`
- Produces: SQLite=진실, `alerts.log`=명시 폴백, `IncidentStore`=projection 명시, `events/models.py`↔`SafetyEvent` 매핑 고정

- [ ] **Step 1: Write the failing test** — repo 없는 `AlertManager`의 incident 스킵 + 이중쓰기 순서 assert
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Write minimal implementation** — 영속 실패 시 fail-closed 또는 명시 폴백
- [ ] **Step 4: Run test to verify it passes**
- [ ] **Step 5: Static check** — `git diff --check`

### Task U5: API 분해·계약 고정

**Files:**
- Modify: `src/api/app.py`, `src/api/service.py`, `src/utils/health.py`
- Test: `tests/test_api_contract.py` + frontend Vitest/Playwright 기존

**Interfaces:**
- Consumes: pipeline/repo/store 주입
- Produces: `routes/cameras, events, models, system` 분리, OpenAPI 스냅샷, 구 `http.server` 헬스 삭제 후 compat 라우트만 유지, `getEvent` 프록시 통일

- [ ] **Step 1: Write the failing test** — 무주입 `create_app()` 폴백 + WS/HTTP 인증 경로 불균일 증명
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Write minimal implementation** — 이동 위주, 행위 변경 최소
- [ ] **Step 4: Run test to verify it passes** — `pytest` + `cd frontend && npm run build`
- [ ] **Step 5: Static check** — `git diff --check`

### Task U6: 확장 배선 완성 (기본 OFF)

**Files:**
- Modify: `config/model_profiles.yaml`, `config/event_policies.yaml`, `config/default.yaml`, `src/detection/pose_detector.py`, `src/detection/stgcn_detector.py`, `src/detection/openvocab_detector.py`, `src/pipeline/pipeline.py`
- Test: `tests/test_pose_behavior.py`, `tests/test_openvocab.py` 확장

**Interfaces:**
- Consumes: `pose_onnx`/`stgcn`/`open_vocab` feature flag
- Produces: `behavior.enabled=true`일 때만 ST-GCN 경로, VLM 실패→`unverified` 유지, `enabled:true`+불변 experimental 충돌의 UI 선택불가 사유 노출

- [ ] **Step 1: Write the failing test** — flag OFF 혼합 실행 정의
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Write minimal implementation** — 3-프레임 투표·비디오VLM·LLM통보는 별도 flag 격리
- [ ] **Step 4: Run test to verify it passes**
- [ ] **Step 5: Static check** — `git diff --check`

### Task U7: 품질게이트·문서부채 정리

**Files:**
- Modify: `scripts/verify_release_candidate.sh`, `scripts/test.sh`, `pyproject.toml`, `docs/troubleshooting.md`, `src/api/service.py` (감사 명명)
- Test: 전체 게이트

- [ ] **Step 1: Write the failing test/docs check** — 커버리지 게이트 주장 vs 실측 불일치 명시
- [ ] **Step 2: Run to verify** — 현행 스크립트 동작 확인
- [ ] **Step 3: Minimal implementation** — 게이트 단일화 둘 중 하나 선택, `dashboard_audit`→`console_audit`改名(호환 별칭), streamlit 잔존 주석 정리
- [ ] **Step 4: Run full gate** — `bash scripts/test.sh` + 가능 범위 `verify_release_candidate.sh`
- [ ] **Step 5: Static check** — `git diff --check`

### Task U8: retention sweep 첫-record 즉시 발동 수정 (기존 실패 5건, 실측 확정)

**Files:**
- Modify: `src/storage/sqlite_observations.py:59`, `src/storage/sqlite_events.py:123`
- Create: `tests/test_storage_prune_deferral.py`
- 대상 테스트: `tests/test_observations.py::test_sqlite_observations_survive_restart_and_merge_current_bucket`, `tests/test_observations.py::test_sqlite_write_failure_rolls_back_in_memory_bucket`, `tests/test_sqlite_events.py::TestSQLiteEventRepository::test_filters_pagination_and_all_lifecycle_revisions`, `tests/test_sqlite_events.py::TestSQLiteEventRepository::test_migrates_pre_normalization_timestamp_rows`, `tests/test_api.py::TestServiceMaskingAndEvents::test_events_endpoint_uses_durable_repository_when_supplied`

**Root cause (실측 2026-09-16):** 두 스토어의 `_last_prune = 0.0` + `_prune_if_due()`(`time.monotonic() - _last_prune >= prune_interval_sec`, 기본 21600) 조합. 호스트 monotonic ≈ 952210s(11일)이라 첫 `record()`가 즉시 sweep → 2026-07 픽스처(오늘 기준 30일 초과) 삭제. 직접 증거: `rows_after_1 []`, `last_prune_after 952210.565`. 기존 테스트 3곳이 deferral 의도를 방증: `tests/test_sqlite_events.py:129-130`("자동 prune을 잠시 미뤄"), `tests/test_observations.py:103,121`(prune_interval=1e12).

**Fix (최소):** 두 스토어 `__init__`의 `self._last_prune = 0.0` → `self._last_prune = time.monotonic()`. 그 외 로직 변경 금지.

**Regression test:** 두 스토어 각각 `time.monotonic`을 prune_interval 초과 값으로 고정해 생성 → retention 초과 timestamp record → 보존 assert(수정 전 FAIL); 이어서 +21601로 record → 오래된 행만 sweep assert(eventual sweep 유지).

**Verification:** (a) 5개 테스트 0 failed (b) `tests/test_storage_prune_deferral.py tests/test_observations.py tests/test_sqlite_events.py tests/test_api.py tests/test_storage_truth.py` 0 failed (c) `bash scripts/test.sh` 0 failed + coverage (d) ruff clean (e) `git diff --check`.

**Forbidden:** 기존 테스트 3파일 수정(완화·skip·기대값 변경 금지), Allowed 외 경로 전부. **커밋 금지(검증 전 정지).**

**Developer 위임 계약(복사용 — 라벨은 각 줄 시작):**

Objective: 두 스토어의 첫 record()에서 자동 retention sweep이 즉시 발동하는 근본 원인을 최소 수정하고 결정적 회귀 테스트를 추가한다.
Allowed write paths: src/storage/sqlite_observations.py; src/storage/sqlite_events.py; tests/test_storage_prune_deferral.py (신규)
Forbidden shared files: tests/test_observations.py; tests/test_sqlite_events.py; tests/test_api.py (기존 테스트 완화 금지); src/·config/·scripts/·frontend/·docs/·installer/·.gitignore 중 Allowed 외 전부; 커밋 금지
Acceptance criteria: 5개 대상 테스트가 수정 전 실패 재현·수정 후 전부 통과(보고에 오류 텍스트 포함); 신규 회귀 테스트가 수정 전 FAIL → 수정 후 PASS이며 deferral + eventual sweep 두 의미 고정; `bash scripts/test.sh` 0 failed + coverage 하한 통과; diff가 Allowed 3경로 한정
Targeted verification: 5개 테스트 단독(0 failed) → `tests/test_storage_prune_deferral.py tests/test_observations.py tests/test_sqlite_events.py tests/test_api.py tests/test_storage_truth.py`(0 failed) → `bash scripts/test.sh` → `ruff check src/storage tests/test_storage_prune_deferral.py` → `git diff --check` (실행은 `./.venv/bin/python -B -m pytest ... -p no:cacheprovider`)
Escalate if: 수정 후에도 실패가 남거나 새 실패 발생 시 추가 수정 없이 원인 diff·재현 명령·출력과 함께 중단 보고; 기존 테스트 픽스처·기대값 수정이 필요하다고 판단되면 수정 없이 보고; 두 스토어 외 제품 코드 변경 필요 시 보고 후 대기

---

## 구조 작업 트랙 (U9–U12, 사용자 선택 2026-09-16)

### Task U9: 구 http.server 헬스 잔재 제거

**Files:** Modify `src/utils/health.py`(`from http.server` import :24, `HealthHTTPHandler` :613, `start_health_server` :667, CLI `--serve`, docstring :13), `tests/test_health.py`(`TestHealthHTTPHandler` :667-679, `TestStartHealthServer` :682-692 삭제), `docs/configuration.md:296`, `docs/operations.md:87`
**근거:** 프로덕션은 FastAPI compat `/health*`(app.py :763-786)·compose healthcheck(:8081)로 대체됨. http.server 경로는 테스트만 참조.
**Scope:** http.server transport(import/클래스/함수/`--serve` CLI) 제거, `--once` CLI와 check 함수·HealthChecker는 유지. 문서 2곳을 엔진 FastAPI compat 안내로 갱신.
**Verify:** `./.venv/bin/python -B -m src.utils.health --once` 동작, `pytest tests/test_health.py tests/test_api.py -q -p no:cacheprovider`, `bash scripts/test.sh` 0 failed, ruff clean.
**Forbidden:** check 함수·HealthChecker·`--once` 제거 금지(추가 참조 없음 확인됨), 문서 2곳 외 파일 수정 금지.

### Task U10: legacy 단일-detector 경로 명시 opt-in (`KSI_CCTV_LEGACY_FALLBACK`)

**Files:** Modify `src/pipeline/pipeline.py`(`_process_camera` 디스패치 :1285-1296, `__init__`), Create `tests/test_legacy_optin.py`
**근거:** dispatch(:1285-1296)는 registry/engine 없는 파이프라인에서 무조건 legacy 실행; 프로덕션(run.py·profile_pipeline)은 항상 policy 모드. 테스트는 `_process_frame_legacy` 직접 호출(:test_pipeline_singlepath:55, test_observations:60)이라 메서드 유지 필요.
**Scope:** `__init__`에서 env `KSI_CCTV_LEGACY_FALLBACK`(1/true/yes)을 `legacy_fallback_enabled`로 읽음. 디스패치 분기를 `_dispatch_frame(...)` 헬퍼로 추출 후: fallback 비활성(기본) → 오류 로그 1회 + 스킵; 활성 → 기존 legacy 실행(기존 deprecation 경고 유지). `_process_frame_legacy` 직접 호출은 env와 무관하게 동작.
**Verify:** 신규 `tests/test_legacy_optin.py`(기본 스킵·env=1 실행·직접 호출 무영향), `pytest tests/test_pipeline.py tests/test_observations.py tests/test_pipeline_singlepath.py -q -p no:cacheprovider`, `bash scripts/test.sh` 0 failed.
**Forbidden:** `_process_frame_legacy` 제거·동작 변경 금지, 기존 테스트 수정 금지, env 외 설정 파일 추가 금지.

### Task U11: VLM 프롬프트의 canonical domain 사용

**Files:** Modify `src/pipeline/pipeline.py`(:555-558 프롬프트 도메인 선택 + 헬퍼 추가), Create `tests/test_prompt_domain.py`
**근거:** 프롬프트 선택이 `detection_type`만 읽어 policy의 `domain`과 다를 수 있음(run.py `_camera_policy_profile`은 둘을 별도 산출). 내부 소비자는 canonical `domain`을 우선해야 함.
**Scope:** `_prompt_domain_for_camera(camera_id)` 헬퍼 추가(`domain` → `detection_type` → "default" 순), `_analyze_with_vlm`이 헬퍼 사용. `detection_type`은 외부 호환 필드로 유지(public_dict·API·프론트 fallback 무변경).
**Verify:** 신규 테스트 4케이스(domain만/detection_type만/둘 다(domain 우선)/없음), `pytest tests/test_vlm.py tests/test_pipeline.py -q -p no:cacheprovider`, `bash scripts/test.sh` 0 failed.
**Forbidden:** `detection_type` 필드·API 응답·프론트 계약 변경 금지, 기존 테스트 수정 금지.

### Task U12: `src/api/app.py` 라우터 분할 (단계별, 각 단계 커밋·검증)

**Files:** Create `src/api/routes/__init__.py` + 그룹 모듈; Modify `src/api/app.py`(해당 핸들러 이동만)
**전략:** 각 그룹을 `src/api/routes/<group>.py`의 `register(app, auth, ...)` 팩토리(기존 클로저 스타일 유지, 동작 변경 없음)로 이동하고 `create_app`에서 호출. 테스트는 `create_app()` 계약만 사용하므로 무변경 원칙.
**단계:** (a) models/policies/settings/captures/audit/config/system (b) events/incidents/observations/SSE/WS (c) cameras/onvif/camera-profiles(`_serialized_camera_mutation` 포함) (d) commissioning/support-bundle/health
**각 단계 Verify:** `pytest tests/test_api.py tests/test_api_contract.py tests/test_cameras.py tests/test_commissioning.py -q -p no:cacheprovider`, `bash scripts/test.sh` 0 failed, `git diff --check`. OpenAPI 경로/태그 불변 확인(`/api/*` 목록 diff 없음).
**Forbidden:** 경로·상태코드·태그·의존성(auth) 변경, service.py 동작 변경, 테스트 수정.

**완료(2026-09-16):** U12a–e 5단계 실행 완료 — `app.py` 2,313→263줄(핸들러 0, 미들웨어·헬퍼·조립만), `routes/` 5모듈 총 66 라우트(단계별 OpenAPI diff empty). 커밋: fa46554(a system)·c270b8c(b events)·1a75c1e(c cameras)·a0bbb92(d commissioning)·7b02978(e ops). 각 단계 Test Runner 독립 검증(데코레이터 총계 66 불변), 최종 전체 스위트 1033 passed / 0 failed / 5 skipped.
