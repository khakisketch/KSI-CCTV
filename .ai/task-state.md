# Build Task State

## Current milestone

Phase 10 M1 완료 (탐지 클래스 정합성 + Phase A 잔여 결함 수정, 2026-06-14).  
현재 기준선: **전체 통과, 1 skipped** (캐시클린 pytest; VLM opt-in skip), policy-mode 기본 실행.  
다음 우선순위: Phase B (실 ONNX+VLM 종단 자동검증 게이트) → Phase C (DGX CUDA·실 RTSP) → Phase D (커스텀 모델 학습).

## Completed

### Phase 3 — EventPolicyEngine independent foundation
- Added `src/policy/` package.
- Implemented `RuleEvaluatorRegistry` with:
  - `direct_detection`
  - `count_threshold`
  - `safety_gear_overlap`
  - `zone_intrusion`
  - `proximity`
  - `person_proximity`
  - `pose_anomaly`
- Implemented `EventPolicyEngine` with:
  - YAML config loading via `ConfigLoader`
  - profile policy evaluation
  - trigger model/class/min-confidence checks
  - rule evaluator execution
  - VLM routing policies: `never`, `always`, `on_ambiguous`, `on_high_confidence`, `on_low_confidence`
  - `PolicyDecision` output and temporary dynamic `SafetyEvent` metadata fields
- Added independent unit tests in `tests/test_policy.py`.
- Self-review fix: applied `min_confidence` filtering consistently across count/gear/zone/proximity/person proximity/pose rules.

### Phase 4 — Pipeline Integration (현재 완료)
- Expanded `SafetyEvent` dataclass with policy fields:
  - `alert_level: str = "low"`
  - `cooldown_sec: float = 30.0`
  - `vlm_needed: bool = False`
  - `source_models: List[str] = field(default_factory=list)`
  - `rule_name: str = ""`
  - Updated `to_dict()` to include new fields
- Refactored `SafetyPipeline`:
  - Added optional `model_registry`, `policy_engine` parameters to `__init__`
  - Added `set_camera_profile(camera_id, profile)` method
  - Split `_process_camera()` into:
    - `_process_frame_with_policy()` — MultiModelRegistry + EventPolicyEngine path
    - `_process_frame_legacy()` — existing single detector mode (backward compatible)
- Updated `EventPolicyEngine`:
  - Removed `_attach_metadata()` static method
  - Policy fields now set directly in `SafetyEvent` constructor
- Added integration tests:
  - `test_safety_event_has_policy_fields`
  - `test_safety_event_to_dict_includes_policy_fields`
  - `test_pipeline_policy_mode_flag`
  - `test_pipeline_legacy_mode_flag`
  - `test_pipeline_set_camera_profile`
  - `test_policy_engine_creates_event_with_correct_fields`
  - `test_policy_engine_fire_and_crowding_both_triggered`
  - `test_legacy_pipeline_emit_event_still_works`
- Updated smoke test:
  - Added `--policy-mode` flag to `scripts/e2e_smoke_test.py`
  - Policy mode tests MultiModelRegistry + EventPolicyEngine end-to-end
  - Legacy mode continues to work unchanged

## Changed files

### Phase 3
- `src/policy/__init__.py`
- `src/policy/rule_evaluators.py`
- `src/policy/event_policy_engine.py`
- `tests/test_policy.py`
- `docs/development-roadmap.md` (Phase 3 status)
- `docs/event-policy-engine.md`

### Phase 4
- `src/pipeline/pipeline.py` — SafetyEvent expansion, SafetyPipeline refactoring
- `src/policy/event_policy_engine.py` — Removed `_attach_metadata()`, direct field setting
- `tests/test_pipeline.py` — Added TestPolicyPipelineIntegration (8 tests)
- `scripts/e2e_smoke_test.py` — Added `--policy-mode` option and `_run_policy_mode()`
- `docs/development-roadmap.md` — Phase 4 status updated

### Harness ownership clarification
- `/home/ksi/.config/opencode/AGENTS.md` — clarified that build must not create/update Plan-owned artifacts and should hand off to plan.
- `/home/ksi/.config/opencode/agent/build.md` — added explicit blocker/handoff rule for Plan-owned artifact requests.
- `/home/ksi/.config/opencode/agent/plan.md` — clarified that plan should directly create missing Plan-owned artifacts under project `.ai/` and not defer to build.

## Verification

### Compile
- `python3 -m compileall -q src tests scripts` — passed

### Unit Tests
- `./.venv/bin/python -m unittest discover tests -v` — **60 tests OK** (was 52, +8 new)

### Smoke Tests
- Legacy mode: `scripts/e2e_smoke_test.py` — passed
- Policy mode: `scripts/e2e_smoke_test.py --policy-mode` — passed

### Harness
- `python3 /home/ksi/.config/opencode/harness-check.py` — passed, 124 checks, 0 warnings, 0 failures

## Blockers

- Plan-owned artifact creation remains intentionally blocked for build:
  - `.ai/project-brief.md`
  - `.ai/plan.md`
  - `.ai/decisions.md`
  - `.ai/review-checklist.md`
- This is by design; the next `plan` phase should create these files because plan already has `.ai/**` write permission.

## Phase 5 — Dashboard Settings UI (완료 ✅)

### Completed
- `src/dashboard/app.py`에 Phase 5 기능 추가:
  - `load_event_policies()` — event_policies.yaml 로드 헬퍼
  - `get_available_domains()` — 도메인 목록 추출
  - `get_events_for_domain()` — 도메인별 이벤트 목록 추출
  - `get_model_status()` — MultiModelRegistry 또는 config 기반 모델 상태 조회
  - `get_alert_level_icon()`, `get_vlm_policy_label()` — UI 헬퍼
  - `render_camera_settings()` — 새 탭: 도메인 선택, 이벤트 체크박스, 모델 상태 표시
  - 파이프라인 시작 시 `set_camera_profile()` 자동 호출
  - 이벤트 카드에 `alert_level` 아이콘/텍스트 추가
  - 이벤트 카드에 policy metadata(source_models, rule_name, vlm_needed) 표시
- 세션 상태 확장: `selected_domain`, `enabled_events`, `camera_profiles`

### Changed files (Phase 5)
- `src/dashboard/app.py` — Phase 5 기능 전체 추가

### Verification (Phase 5)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — **60 tests OK** (unchanged)
- `scripts/e2e_smoke_test.py --policy-mode` — passed

## Phase 5 — Must-fix Resolution ✅

### M1 — Missing `Any` import fixed ✅
- Changed `from typing import Dict, List` to `from typing import Any, Dict, List` in `src/dashboard/app.py`.

### M2 — Duplicate `init_session_state()` renamed back to `require_dashboard_auth()` ✅
- The function at line 222 that contained auth code was accidentally named `init_session_state()`, overwriting the real session initialization. Renamed back to `require_dashboard_auth()`.

## Remaining risks / follow-ups

- `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, `.ai/review-checklist.md` 생성이 권한 제한으로 차단됨 (build 모드에서 plan-owned artifact 생성 불가).
- 실제 RTSP 카메라 또는 사람 영상이 없어 E2E 검증은 합성 샘플 영상 기준.
- `ppe_yolo`, `fire_smoke_yolo`, `pose_fall_model`은 아직 disabled 상태.

## Must-fix review feedback (resolved)

### M1 — Unused import removed ✅
- Removed `from src.detection.model_registry import MultiModelRegistry` from inside `_process_frame_with_policy()` in `src/pipeline/pipeline.py`.

### M2 — getattr() replaced with direct attribute access ✅
- Replaced `getattr(event, "cooldown_sec", 30.0)` → `event.cooldown_sec`
- Replaced `getattr(event, "vlm_needed", False)` → `event.vlm_needed`
- Replaced `getattr(event, 'alert_level', 'low')` → `event.alert_level`

### M3 — Dead code removed ✅
- Removed unused `_run_yolo()` function from `scripts/e2e_smoke_test.py`.

## Phase 6 — User Model Upload foundation (완료 ✅)

### Completed
- Added `src/models/upload_validator.py` with the security-reviewed upload workflow:
  - ONNX-only extension validation.
  - In-memory file size limit before disk write.
  - Filename/path traversal mitigation via safe basename metadata and fixed isolated storage name.
  - Isolated storage under `models/uploaded/{model_id}/model.onnx`.
  - ONNX Runtime load validation.
  - Static ONNX input H/W shape validation against `input_size` metadata when available.
  - Bounded synthetic inference smoke test with timeout.
  - Uploaded model profile registration in `config/model_profiles.yaml` as `enabled: false` by default.
  - Explicit activate/deactivate/delete helper functions for admin-gated UI actions.
- Added `src/models/__init__.py`.
- Added dashboard "📦 모델 업로드" tab in `src/dashboard/app.py`:
  - Requires a non-default `KSI_CCTV_DASHBOARD_PASSWORD` and an authenticated session.
  - Uploads ONNX models through the validator.
  - Records audit log entries for upload success/failure, activation/deactivation, and deletion.
  - Keeps uploaded models disabled until an explicit admin activation action.
- Added focused tests in `tests/test_upload_validator.py`:
  - Reject non-ONNX uploads before writing.
  - Reject oversized uploads before runtime validation.
  - Successful uploads are isolated and disabled by default.
  - Invalid metadata is rejected.
  - Shape metadata mismatch is rejected before inference smoke test.
  - Activate/deactivate/delete update profile state and cleanup files.

### Changed files (Phase 6)
- `src/models/__init__.py`
- `src/models/upload_validator.py`
- `src/dashboard/app.py`
- `tests/test_upload_validator.py`

### Verification (Phase 6)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -p 'test_upload_validator.py' -v` — 6 tests OK

### Remaining risks / follow-ups (Phase 6)
- Full unit suite and E2E smoke were not rerun; only smallest relevant verification was run for this bounded milestone.
- Dashboard delete action is admin-gated and audited but currently has no second confirmation step.
- Upload action writes to `config/model_profiles.yaml` at runtime and may rewrite YAML formatting/comments via PyYAML.
- Actual untrusted ONNX model behavior should receive acceptance/security review before production use.
- Plan-owned `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, `.ai/review-checklist.md` remain intentionally missing/blocked for build.

## Phase 6 — Review Should-fix resolution (완료 ✅)

### Completed
- Resolved review Should-fix #1 in `src/dashboard/app.py`:
  - Added a per-model `삭제 확인` checkbox.
  - The `삭제` button is disabled until the checkbox for that model row is checked.
- Resolved review Should-fix #2 by running the requested broader verification.

### Changed files (Should-fix)
- `src/dashboard/app.py`
- `.ai/task-state.md`
- `.ai/handoff.md`

### Verification (Should-fix)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -p 'test_upload_validator.py' -v` — 6 tests OK
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` — passed

### Remaining risks / follow-ups after Should-fix
- YAML formatting/comment rewrite risk remains if runtime upload writes `config/model_profiles.yaml` via PyYAML.
- ONNX Runtime emitted a GPU device discovery warning during verification, but execution continued with `CPUExecutionProvider` and tests/smoke passed.
- Plan-owned `.ai/project-brief.md`, `.ai/plan.md`, `.ai/decisions.md`, `.ai/review-checklist.md` remain intentionally missing/blocked for build.

## Plan-owned artifact creation request (blocked in build) ⚠️

### Requested
- Create `.ai/project-brief.md`
- Create `.ai/plan.md`
- Create `.ai/decisions.md`
- Create `.ai/review-checklist.md`
- Update `.ai/handoff.md`
- Verify file existence

### Result
- The four requested files are Plan-owned artifacts under the project workflow contract.
- Current build role must not create or update Plan-owned artifacts, even when a handoff includes their contents.
- Verified current `.ai/` contents remain only:
  - `.ai/task-state.md`
  - `.ai/handoff.md`

### Changed files for this blocker record
- `.ai/task-state.md`
- `.ai/handoff.md`

### Verification
- `.ai/*` listing confirmed the four Plan-owned files are still absent.

### Next required phase
- Return to `plan` with write capability for Plan-owned artifacts, or adjust permissions so `plan` directly writes the four files.

## OpenCode harness improvement for plan `.ai/**` writes (완료 ✅)

### Completed
- Updated global OpenCode harness files so future `plan` sessions more clearly treat `.ai/**` as the only writable planning-artifact exception:
  - `/home/ksi/.config/opencode/agent/plan.md`
    - changed description to "source-read-only but .ai planning-artifact-writable"
    - added explicit guidance that generic Plan Mode read-only reminders should not block repo-local `.ai/**` planning artifact writes when the agent permission block allows them
  - `/home/ksi/.config/opencode/opencode.jsonc`
    - updated `/plan-work` template to say `.ai/` planning artifact writes are allowed in plan and must not be deferred to build
  - `/home/ksi/.config/opencode/AGENTS.md`
    - added global ownership clarification for plan `.ai/**` writes despite generic read-only reminders
  - `/home/ksi/.config/opencode/harness-check.py`
    - added checks for the new planning-artifact-writable guidance

### Verification
- `python3 /home/ksi/.config/opencode/harness-check.py` — passed, 172 checks, 0 warnings, 0 failures

### Remaining limits
- Running OpenCode sessions keep already-loaded config/instructions. Restart OpenCode before expecting future `plan` sessions to use the updated harness.
- If OpenCode core injects a hard system-level read-only constraint that overrides agent permissions, config cannot fully remove that core behavior; the updated harness makes the intended `.ai/**` exception explicit and verifiable.

## Phase 7 — Operations/Deployment scaffold (완료 ✅)

### Completed
- Added `scripts/install.sh`:
  - Creates/reuses `.venv`.
  - Installs `requirements.txt`.
  - Ensures `logs`, `data/captures`, and `models/uploaded` runtime directories.
  - Does not overwrite `.env`.
  - Warns if `models/yolo26.onnx` is missing.
  - Runs `scripts/healthcheck.py` and prints manual systemd install/rollback commands without running `sudo`.
- Added `systemd/ksi-cctv.service`:
  - Template service for Streamlit dashboard via `scripts/run_dashboard.sh`.
  - Uses `/etc/ksi-cctv/ksi-cctv.env` as optional environment file.
  - Adds restart policy and minimal hardening.
  - Allows runtime writes to logs/data/uploaded models/config for current dashboard/model upload behavior.
- Added `scripts/healthcheck.py`:
  - Checks env loading without printing secret values.
  - Checks required Python imports.
  - Parses default/cameras/model profile configs.
  - Verifies runtime directory writability.
  - Checks ONNX Runtime providers and YOLO load with CPU fallback.
  - Checks retention settings.
  - Checks enabled camera URL resolution/file existence without printing raw RTSP URLs.
  - Supports `--json`, `--require-gpu`, `--check-vlm`, and `--check-cameras`.
- Updated `.env.example` comments only:
  - Clarified production/systemd env handling.
  - Warned against committing credentials and using `change-me` in production.

### Changed files (Phase 7)
- `scripts/install.sh`
- `scripts/healthcheck.py`
- `systemd/ksi-cctv.service`
- `.env.example`
- `.ai/task-state.md`
- `.ai/handoff.md`

### Verification (Phase 7)
- `bash -n scripts/install.sh` — passed
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python scripts/healthcheck.py --json` — passed (`ok: true`, 12 passes, 1 warning, 0 failures)
  - Warning: CUDA provider unavailable; CPU fallback used successfully.
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` — passed
- Optional: `systemd-analyze verify systemd/ksi-cctv.service`
  - Initial unit-specific issues were fixed (`Documentation=README.md`, direct `/opt/.../.venv/bin/python` check).
  - Final command still reported unrelated local system permission issue: `dgx-dashboard-admin.service: Failed to open /etc/systemd/system/dgx-dashboard-admin.service: 허가 거부`.

### Remaining risks / follow-ups (Phase 7)
- This milestone only created operations scaffold; it did not install, enable, or start systemd service.
- `systemd/ksi-cctv.service` uses template user/path values (`ksi-cctv`, `/opt/ksi-cctv`) and must be reviewed per deployment site.
- CUDA provider is unavailable in current local verification; CPU fallback works, but DGX Spark GPU provider still needs hardware validation.
- Healthcheck created/ensured runtime directories, including `models/uploaded`.
- Actual RTSP connectivity and VLM availability checks are optional and were not run by default.

## Phase 7 — Site-specific deployment hardening (완료 ✅)

### Completed
- Hardened `systemd/ksi-cctv.service`:
  - Added explicit comments that `User`, `Group`, and `WorkingDirectory` are site-specific deployment values.
  - Added `PYTHONDONTWRITEBYTECODE=1` to avoid bytecode writes under a hardened service.
  - Clarified `/etc/ksi-cctv/ksi-cctv.env` should be owner-readable and not committed.
  - Narrowed `ReadWritePaths` from the whole `config` directory to `config/model_profiles.yaml` while retaining required writes for logs, data/captures, and uploaded models.
- Hardened `scripts/healthcheck.py`:
  - Default runtime directory check is now read-only; it does not create directories by default.
  - Added `--create-dirs` / `--repair-dirs` explicit opt-in for directory creation.
  - Replaced temp-file write probing with existence/writability checks via `os.access`.
  - Added defensive redaction for URLs and common secret assignments in arbitrary detail/error strings.
- `scripts/install.sh` remains responsible for creating runtime directories before running healthcheck; no `sudo`, service install, or service start was added.

### Changed files (site-specific hardening)
- `systemd/ksi-cctv.service`
- `scripts/healthcheck.py`
- `.ai/task-state.md`
- `.ai/handoff.md`

### Verification (site-specific hardening)
- `bash -n scripts/install.sh` — passed
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python scripts/healthcheck.py --json` — passed (`ok: true`, 12 passes, 1 warning, 0 failures)
  - Warning: CUDA provider unavailable; CPU fallback used successfully.
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` — passed
- Optional: `systemd-analyze verify systemd/ksi-cctv.service`
  - Still blocked by unrelated local system permission issue: `dgx-dashboard-admin.service: Failed to open /etc/systemd/system/dgx-dashboard-admin.service: 허가 거부`.

### Remaining risks / follow-ups after hardening
- No systemd service was installed/enabled/started; deployment remains site-specific.
- DGX Spark CUDA provider validation still requires real hardware.
- `config/model_profiles.yaml` remains writable because Phase 6 uploaded model registration currently writes there.
- RTSP/VLM probes remain opt-in and were not run by default.
- Review Should-fix #1 applied: healthcheck now suggests `--create-dirs` when a runtime directory is missing.

## Phase 8 — Milestone 1: 사전학습 모델 통합 (진행 중 ⏳)

### Completed
- `src/detection/class_adapter.py` 신규 작성:
  - 공개 모델 출력 클래스 → KSI-CCTV 표준 클래스 매핑
  - `DEFAULT_CLASS_MAPS`: ppe_yolo, fire_smoke_yolo 기본 매핑 정의
  - `adapt_detections()`: Detection 리스트 클래스 이름 변환
  - `get_class_map_for_profile()`: model_profiles.yaml profile에서 class_map 추출
- `src/detection/model_registry.py` 수정:
  - `detect_model()`에서 class_map 적용하여 클래스 이름 변환 후 반환
  - `to_status_dict()`에 class_map 필드 추가
- `scripts/fetch_safety_models.py` 신규 작성:
  - Hugging Face Hub에서 사전학습 모델 자동 다운로드
  - Ultralytics를 이용한 ONNX export
  - ONNX shape/입출력 검증
  - `ppe_yolo`: `keremberke/yolov8n-hard-hat-detection`
  - `fire_smoke_yolo`: `keremberke/yolov8n-fire-detection`
- `config/model_profiles.yaml` 수정:
  - `ppe_yolo`: `enabled: true`, `source: pre_trained`, `class_map` 추가
  - `fire_smoke_yolo`: `enabled: true`, `source: pre_trained`, `class_map` 추가
- `config/event_policies.yaml` 수정:
  - `smoke` 이벤트 trigger를 `general_yolo`로 fallback (fire_smoke 모델이 smoke 미지원)

### Changed files (Phase 8 M1)
- `src/detection/class_adapter.py` (신규)
- `src/detection/model_registry.py`
- `scripts/fetch_safety_models.py` (신규)
- `config/model_profiles.yaml`
- `config/event_policies.yaml`

### Verification (Phase 8 M1)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` — passed (ppe_yolo, fire_smoke_yolo는 모델 파일 부재로 로드 실패, non-blocking)
- `./.venv/bin/python scripts/healthcheck.py --json` — passed (12 passes, 1 warning, 0 failures)

### Remaining risks / follow-ups (Phase 8 M1)
- 사전학습 ONNX 모델 파일 (`models/yolo26_ppe.onnx`, `models/yolo26_fire_smoke.onnx`)이 아직 없어 실제 추론은 불가
- `scripts/fetch_safety_models.py` 실행 시 인터넷 연결 및 huggingface_hub/ultralytics 필요
- `fire_smoke_yolo`는 fire 클래스만 지원하므로 smoke 이벤트는 `general_yolo` + VLM fallback
- 실제 모델 다운로드/검증은 추후 네트워크 연결 시 `scripts/fetch_safety_models.py` 실행
- Track B (데이터셋 인프라)는 Milestone 2에서 진행 예정
- 실제 모델 학습/파인튜닝은 다른 프로젝트 학습 완료 후 Milestone 3에서 진행

## Phase 8 — Milestone 2: 데이터셋 인프라 구축 (완료 ✅)

### Completed
- `scripts/download_datasets.py` 신규 작성:
  - Kaggle API (`kagglehub`) 지원
  - Roboflow API (`roboflow`) 지원
  - 직접 URL 다운로드 지원 (ZIP 포함)
  - 다운로드 진행률 표시
  - `dataset.yaml` 자동 생성
  - `--list`로 사용 가능한 데이터셋 목록 출력
  - `--json`으로 JSON 요약 출력
  - PPE 데이터셋 2개, Fire/Smoke 데이터셋 2개 사전 구성
- `scripts/convert_to_yolo.py` 신규 작성:
  - Pascal VOC (XML) → YOLO 변환
  - COCO (JSON) → YOLO 변환
  - CSV → YOLO 변환
  - Labelme (JSON) → YOLO 변환
  - 기존 YOLO 형식 검증/복사
  - `--format auto`로 자동 형식 감지
- `scripts/validate_dataset.py` 신규 작성:
  - 이미지 무결성 검증 (OpenCV로 읽기 가능 여부)
  - 라벨 파일 형식 검증 (5개 값, normalized [0,1])
  - 클래스 분포 통계
  - 이미지/라벨 쌍 일치 여부 확인
  - 손상된 이미지 감지
  - `--report`로 상세 리포트 생성
  - `--json`으로 JSON 요약 출력
- `scripts/generate_dataset_yaml.py` 신규 작성:
  - Ultralytics YOLO 학습용 `dataset.yaml` 자동 생성
  - `train/val/test` split 감지
  - 클래스 이름 목록 기반 `names:` 섹션 생성

### Changed files (Phase 8 M2)
- `scripts/download_datasets.py` (신규)
- `scripts/convert_to_yolo.py` (신규)
- `scripts/validate_dataset.py` (신규)
- `scripts/generate_dataset_yaml.py` (신규)

### Verification (Phase 8 M2)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- 실제 데이터셋 다운로드는 네트워크/인터넷 연결 필요
- `scripts/download_datasets.py --list`로 사용 가능한 데이터셋 목록 확인 가능

### Remaining risks / follow-ups (Phase 8 M2)
- 실제 데이터셋 다운로드는 네트워크 연결 및 API 키 필요
- 데이터셋 변환/검증은 실제 다운로드 후 실행
- 학습 스크립트 (`scripts/train_ppe.py`, `scripts/train_fire_smoke.py`)는 Milestone 3에서 작성
- 실제 모델 학습은 다른 프로젝트 학습 완료 후 실행

## Phase 8 — Milestone 4: 알림/내보내기/로그 뷰어 (완료 ✅)

### Completed
- 이메일 알림 강화 (`src/alerts/alert_manager.py`):
  - **HTML 이메일 포맷** — alert_level 색상 뱃지 포함 테이블 형식 HTML 본문
  - **alert_level 필터링** — `medium` 이상만 이메일 발송, `low`는 무시
  - **MIME multipart** — plain text + HTML 동시 포함
- 이벤트 내보내기 (`src/dashboard/app.py`):
  - **CSV 내보내기** — 필터링된 이벤트를 CSV로 다운로드 (timestamp, camera_id, event_type, confidence, alert_level, description)
  - **JSON 내보내기** — 필터링된 이벤트를 JSON으로 다운로드 (rule_name, source_models 포함)
  - **날짜 자동 포함** — 파일명에 `events_YYYYMMDD_HHMMSS` 타임스탬프
- 이벤트 로그 뷰어 (`src/dashboard/app.py`):
  - **"📝 이벤트 로그" 신규 탭** — `logs/alerts.log` JSONL 파싱하여 히스토리 표시
  - **필터** — 이벤트 유형, 카메라, 날짜 범위
  - **페이지네이션** — 최신 100개만 표시 (expander로 상세 JSON)
- SMTP 테스트 버튼 (`src/dashboard/app.py`):
  - **"🔧 시스템 상태" 탭에 SMTP 테스트 추가** — 연결/인증/발송 테스트
  - **설정 표시** — SMTP 호스트, 사용자, 수신자 정보 표시

### Changed files (Phase 8 M4)
- `src/alerts/alert_manager.py` — `_send_email()` HTML 포맷, alert_level 필터 추가
- `src/dashboard/app.py` — 이벤트 내보내기 버튼, 이벤트 로그 탭, SMTP 테스트 버튼 추가

### Verification (Phase 8 M4)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- 대시보드 Streamlit 실행은 `bash scripts/run_dashboard.sh`로 확인 (수동)

### Remaining risks / follow-ups (Phase 8 M4)
- 실제 SMTP 서버 연결 및 이메일 발송은 외부 SMTP 설정 필요
- 이벤트 로그 뷰어는 `logs/alerts.log` 파일이 있을 때만 동작
- 실제 데이터셋 다운로드/학습은 다른 프로젝트 완료 후 Milestone 5에서 실행

## Phase 8 — Milestone 3: 대시보드 개선 및 문서화 (완료 ✅)

### Completed
- 대시보드 개선 (src/dashboard/app.py):
  - **감사 로그 뷰어 탭** 추가 — `_load_audit_log()`로 JSONL 파싱, 이벤트 유형 필터, pandas 또는 수동 테이블 표시
  - **시스템 상태 탭** 추가 — ONNX Runtime provider 정보, psutil 기반 CPU/메모리/디스크 사용량, 네트워크 I/O
  - **모델 상세 상태** — `MultiModelRegistry.to_status_dict()` 활용, class_map, source_type, 실패 원인 표시
  - **이벤트 필터 개선** — alert_level 필터 추가, 시간 범위 필터 (최근 1시간/24시간/7일)
- 문서화:
  - `docs/deployment-guide.md` — DGX Spark/일반 배포, systemd, healthcheck, env 설정, 문제 해결
  - `docs/custom-model-training.md` — 데이터셋 다운로드→변환→검증→학습→ONNX export 전체 가이드
  - `docs/development-roadmap.md` 갱신 — Phase 7 완료, Phase 8 진행 중으로 업데이트

### Changed files (Phase 8 M3)
- `src/dashboard/app.py` — 탭 추가 (감사 로그, 시스템 상태), 이벤트 필터 개선
- `docs/deployment-guide.md` (신규)
- `docs/custom-model-training.md` (신규)
- `docs/development-roadmap.md` (갱신)

### Verification (Phase 8 M3)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- 대시보드 Streamlit 실행은 `bash scripts/run_dashboard.sh`로 확인 (수동)

### Remaining risks / follow-ups (Phase 8 M3)
- Streamlit 대시보드는 실제 실행 시 브라우저에서 UI 확인 필요
- psutil 미설치 시 시스템 상태 탭이 기본 정보만 표시
- 감사 로그 뷰어는 pandas 미설치 시 수동 테이블로 폴백
- 실제 데이터셋 다운로드/학습은 다른 프로젝트 완료 후 Milestone 4에서 실행

## Phase 8 — Milestone 5.1: 대시보드/성능/보안 개선 (1순위) (완료 ✅)

### Completed
- **D1 자동 새로고침** (`src/dashboard/app.py`):
  - `init_session_state()`에 `auto_refresh_enabled`, `auto_refresh_interval` 상태 추가 (기본값 5초)
  - `render_sidebar()`에 "새로고침" 설정 섹션 추가 — 체크박스 on/off + 슬라이더로 간격 조정 (1~30초)
  - `main()` 끝에 `time.sleep(interval)` + `st.rerun()`으로 자동 새로고침 루프 추가
- **S1 로그인 속도 제한** (`src/dashboard/app.py`):
  - `_is_rate_limited()` — 60초 내 5회 실패 시 60초 잠금, 남은 시간 표시
  - `_record_login_failure()` — 실패 시도 기록, 5회 도달 시 `login_locked_until` 설정
  - `require_dashboard_auth()`에 rate limiting 통합, 잠금 시 로그인 폼 숨김
- **S2 세션 타임아웃** (`src/dashboard/app.py`):
  - 30분 무활동 시 자동 로그아웃 (`last_activity` 타임스탬프 추적)
  - 타임아웃 시 `session_timeout` 감사 로그 기록, `st.warning()` 후 `st.rerun()`
  - 성공 로그인 시 `login_attempts` 초기화 + `last_activity` 갱신

### Changed files (Phase 8 M5.1)
- `src/dashboard/app.py` — 자동 새로고침, rate limiting, session timeout 추가

### Verification (Phase 8 M5.1)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK

### Remaining risks / follow-ups (Phase 8 M5.1)
- Streamlit 대시보드는 실제 실행 시 브라우저에서 UI 확인 필요
- 실제 데이터셋 다운로드/학습은 다른 프로젝트 완료 후

## Phase 8 — Milestone 5.2: 대시보드/성능/보안 개선 (2순위) (완료 ✅)

### Completed
- **D2 알림 설정 UI 연결** (`src/dashboard/app.py`):
  - `_load_alert_preferences()` / `_save_alert_preferences()` 헬퍼 추가 — `config/alert_preferences.yaml` 읽기/쓰기
  - `render_alert_settings()`에 영구 저장 기능 추가 — "💾 설정 저장" 버튼이 YAML로 저장, "🔄 기본값으로 되돌리기" 버튼 추가
  - 저장 시 `alert_preferences_saved` 감사 로그 기록
  - `config/alert_preferences.yaml`에 `email_alert_level_threshold` + `per_event_email_enabled` 저장
- **D2 AlertManager 연동** (`src/alerts/alert_manager.py`):
  - `_load_alert_preferences()` — AlertManager 초기화 시 YAML 로드
  - `_should_send_email(event)` — alert_level 임계값 + per-event 설정 기반 발송 결정
  - `_send_email()` / `_send_batch_email()`에 `_should_send_email()` 적용
- **P2 알람 배치 처리** (`src/alerts/alert_manager.py`):
  - `AlertConfig`에 `batch_flush_interval` (기본 0 = 비활성), `batch_max_size` (기본 100) 추가
  - `handle_event()` — `batch_flush_interval > 0`이면 버퍼에 추가 후 `_maybe_flush_batch()` 호출, 아니면 기존 즉시 처리
  - `_maybe_flush_batch()` — 크기 또는 시간 도달 시 flush (첫 이벤트에서 타이머 시작)
  - `_flush_batch()` — 버퍼 비우기 + 타이머 리셋, `_log_batch()` + `_send_batch_email()` 호출
  - `_log_batch()` — N건 이벤트 일괄 로깅 + 콘솔 출력
  - `_send_batch_email()` — medium+ 이벤트만 필터링 후 단일 이메일로 일괄 발송 (HTML 테이블)

### Changed files (Phase 8 M5.2)
- `src/dashboard/app.py` — 알림 설정 UI 영구 저장, 기본값 복구
- `src/alerts/alert_manager.py` — 배치 처리 로직, alert_preferences 연동
- `config/alert_preferences.yaml` (신규) — 알림 환경설정 저장 파일

### Verification (Phase 8 M5.2)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK

### Remaining risks / follow-ups (Phase 8 M5.2)
- 대시보드에서 설정 저장 후 AlertManager가 실시간으로 reload하지 않음 — 파이프라인 재시작 시 적용됨
- 배치 이메일 발송 시 개별 이벤트 이미지는 첨부되지 않음 (HTML 본문만)
- 실제 SMTP 서버 연결 및 배치 발송은 외부 SMTP 설정 필요

## Phase 8 — Milestone 5.3: 대시보드/성능/보안 개선 (3순위) (완료 ✅)

### Completed
- **D3 카메라별 통계** (`src/dashboard/app.py`):
  - `render_statistics()` 전면 개편 — 요약 메트릭 4종 (총 이벤트, 활성 카메라, High+Critical, 최근 이벤트)
  - 카메라 선택 필터 (전체/특정 카메라) 추가
  - 카메라별 이벤트 유형 분포, alert_level 분포, 시간대별 알람, 최근 24시간 추이 (라인 차트)
  - 전체 선택 시 카메라별 비교 차트 (pandas DataFrame)
  - 카메라별 요약 테이블 (pandas 또는 수동 폴백)
- **P1 동적 프레임 skip** (`src/pipeline/pipeline.py`):
  - `SafetyPipeline.__init__`에 `dynamic_skip`, `motion_threshold`, `static_skip_multiplier` 파라미터 추가
  - `_detect_motion(camera_id, image)` — 그레이스케일 + 리사이즈(160x120) + absdiff 기반 모션 감지
  - `_process_camera()`에서 모션이 없을 때 `effective_skip = skip_frames * static_skip_multiplier`로 CPU 사용량 절감
  - 첫 프레임은 안전하게 모션 있다고 가정
- **S3 로그 URL 마스킹** (`src/utils/config.py`, `src/alerts/alert_manager.py`):
  - `mask_url(url)` — `rtsp://user:pw@host` → `rtsp://***:***@host` 변환
  - `mask_text(text)` — 임의의 텍스트에서 RTSP/HTTP URL 패턴을 찾아 마스킹
  - `alert_manager.py`의 `_log_event()` / `_log_batch()`에 `mask_text()` 적용
  - `rtsp_capture.py`의 기존 `_safe_stream_url`은 유지

### Changed files (Phase 8 M5.3)
- `src/dashboard/app.py` — `render_statistics()` 카메라별 통계로 확장
- `src/pipeline/pipeline.py` — `__init__`에 dynamic_skip 파라미터, `_process_camera`에 모션 기반 스킵, `_detect_motion()` 추가
- `src/utils/config.py` — `mask_url()`, `mask_text()` 유틸리티 추가
- `src/alerts/alert_manager.py` — `mask_text` import, `_log_event()` / `_log_batch()`에 마스킹 적용

### Verification (Phase 8 M5.3)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK

### Remaining risks / follow-ups (Phase 8 M5.3)
- `dynamic_skip`은 기본값 `False` — 명시적으로 활성화해야 동작
- `mask_text`는 RTSP/HTTP URL 패턴만 마스킹 — file:// 등 다른 스킴은 미처리
- 카메라별 통계는 세션 내 이벤트만 집계 — 영구 저장은 미구현

## Phase 8 완료 ✅
- M1~M5.3 모두 완료. 사전학습 모델 다운로드/추론 검증과 커스텀 학습은 다른 프로젝트 학습 완료 후 별도 진행.

## Phase 9 — M4: 사전학습 모델 다운로드 + 추론 검증 (완료 ✅)

### Completed
- **모델 다운로드** (`scripts/fetch_safety_models.py`)
  - `models/yolo26_ppe.onnx` (5.9MB) — keremberke/yolov8n-hard-hat-detection
  - `models/yolo26_fire_smoke.onnx` (22MB) — touati-kamel/yolov8s-forest-fire-detection (원본 keremberke 401 → 대체)
  - `--all` 옵션 추가, fire 모델 docstring에 대체 repo 사유 명시
- **재현성** (`requirements.txt`)
  - `huggingface_hub>=0.20.0` 추가
- **추론 검증** (`scripts/e2e_smoke_test.py --policy-mode`)
  - 3개 모델 모두 로드/추론 성공, `failed_models={}`
  - `ppe_yolo` 추론 성공 — 실제 클래스는 Hardhat/NO-Hardhat (person 클래스 없음, 오기였음; config/model_profiles.yaml 및 class_adapter 참조)

### Verification (Phase 9 M4)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` — `failed_models={}`
- `./.venv/bin/python scripts/fetch_safety_models.py --all --json` — 두 모델 모두 OK

## Phase 9 — M3: 비디오 파일 기반 통합 테스트 (완료 ✅)

### Completed
- **`scripts/stress_test.py` 신규 작성**
  - 비디오 파일 반복 재생으로 sample 비디오 기반 통합 안정성 검증
  - 컴포넌트 1회 생성/재사용 패턴 (`_create_pipeline()`)
  - `cam_id` 실제 `engine.evaluate(camera_id=cam_id)` 연결
  - `--cameras` help: "single-thread: camera_id만 분산"
  - `--json-out` 옵션으로 결과 JSON 저장
  - `PolicyDecision.event_type`이 `SafetyEvent` 객체가 아닌 문자열로 기록
  - `registry.active_model_names` 속성 사용

### Verification (Phase 9 M3)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- `./.venv/bin/python scripts/stress_test.py --duration 30 --policy-mode --json-out reports/stress_phase9.json`
  - `failed_frames=0`
  - `events_by_type: {fire: 15}`
  - `events_by_alert_level: {critical: 15}`
  - 20프레임 처리, 31.8초, avg inference 1588.9ms
- `--cameras 2` 시뮬레이션도 정상 작동

## Phase 9 — M1: VLM observability + timestamp cache (완료 ✅)

### Completed
- **`OllamaProvider` 메트릭** (`src/vlm/ollama_provider.py`)
  - thread-safe metrics: `total_calls`, `success_count`, `failure_count`, `total_latency_ms`, `max_latency_ms`, `last_latency_ms`, `last_error`, `last_risk_level`
  - `get_metrics()` / `reset_metrics()` 공개 메서드
  - `analyze()` 성공/실패 시 메트릭 자동 기록
- **`SafetyPipeline` VLM cache** (`src/pipeline/pipeline.py`)
  - `vlm_cache_ttl_sec` (기본 30초), `vlm_cache_bypass_levels` (기본 critical/high) 파라미터
  - `_analyze_with_vlm`에서 `(camera_id, event_type)` 키로 TTL cache 검사
  - `get_vlm_cache_stats()` 공개 메서드 (hits/misses/bypasses)
- **대시보드 VLM 섹션** (`src/dashboard/app.py`)
  - 시스템 상태 탭에 VLM 연결/메트릭(총 호출, 성공/실패, 평균/최대 레이턴시, 성공률)
  - Cache 통계 표시 (hits/misses/bypasses/hit_rate)
  - 메트릭 + cache 리셋 버튼

### Verification (Phase 9 M1)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- Ollama 미가동 환경에서는 `available=False` 표시 + 메트릭 0 유지. 실제 호출 시 자동 기록됨.

## Phase 9 — M2: 성능 프로파일링 도구 (완료 ✅)

### Completed
- **`scripts/profile_pipeline.py` 신규 작성**
  - cProfile + tracemalloc 기반 프로파일링
  - 개별 모델별 레이턴시 (avg/min/max/p50/p95) + 피크 메모리 측정
  - 전체 파이프라인 (detect + policy) 레이턴시 + 메모리 측정
  - `--cprofile` 옵션으로 cProfile 상세 출력 지원
  - `--json-out` 옵션으로 결과 JSON 저장
  - 합성 프레임 생성 또는 비디오 파일 로드 지원
  - VLM 캐시 통계 포함

### Verification (Phase 9 M2)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- `./.venv/bin/python scripts/profile_pipeline.py --frames 5 --iterations 1 --pipeline-iterations 1 --json-out reports/profile_phase9.json` — passed
  - 3개 모델 모두 프로파일링 완료
  - general_yolo: avg 637ms, ppe_yolo: avg 858ms, fire_smoke_yolo: avg 1337ms
  - 전체 파이프라인: avg 2890ms
  - 평균 피크 메모리: 11.72MB

## Phase 9 — Should-fix Resolution (완료 ✅)

### Completed
- **pipeline.py에 `reset_vlm_cache()` 공개 메서드 추가** (`src/pipeline/pipeline.py` line 244-249)
  - `_vlm_cache.clear()` + `_vlm_cache_stats` 초기화
  - logger.info로 초기화 기록
- **대시보드에서 공개 메서드 사용** (`src/dashboard/app.py` line 1489-1493)
  - `hasattr(pipeline, "reset_vlm_cache")` 확인 후 호출
  - private attribute 직접 접근 제거

### Verification (Should-fix)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — 66 tests OK
- `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` — passed
- `./.venv/bin/python scripts/stress_test.py --duration 10 --policy-mode` — `failed_frames=0`

## Phase 9 — 다음 마일스톤 (예정)
- M3.5: RTSP 재연결 시뮬레이션 (장비 부재로 보류)
- M5: 커스텀 모델 학습 (다른 프로젝트 학습 완료 후)

## Phase 9 — 알려진 한계
- 웹캠/RTSP 장비 없음 → 단일 카메라(single-threaded) 시뮬레이션
- 실제 ONNX GPU provider 검증은 DGX Spark 환경에서 별도
- `--cameras`는 camera_id 분산일 뿐 실제 멀티스레드 처리 아님

## Phase 10 M0 — Policy-mode 실행 경로 복구 + 통합 정확성 정리 (완료 ✅)

### Completed
- `src/dashboard/app.py`의 `start_pipeline()` 기본 실행 경로를 legacy single-detector에서 policy-mode로 전환:
  - `MultiModelRegistry.from_config("./config/model_profiles.yaml")` 생성
  - `EventPolicyEngine.from_config("./config/event_policies.yaml")` 생성
  - `SafetyPipeline(model_registry=..., policy_engine=...)` 주입
  - `pipeline.use_policy_mode == True`가 되도록 구성
  - 활성 모델이 없으면 조용한 legacy fallback 없이 사용자에게 오류 표시
  - 일부 모델 로드 실패는 `st.warning()`으로 노출
- 대시보드 시작 시 카메라별 policy profile 적용:
  - `load_camera_profiles()` 추가
  - `build_camera_policy_profile()` 추가
  - `domain`, `events`/`enabled_events`, `zones`, `detection_type`를 `pipeline.set_camera_profile()`로 적용
  - 카메라 설정 탭에서 선택한 이벤트/도메인을 session profile에 저장 후 실행 중 파이프라인에도 반영
- policy-mode 최신 탐지 결과 표시 경로 정리:
  - `SafetyPipeline._process_frame_with_policy()`가 `latest_detections[camera_id]`에 다중 모델 결과를 flatten 저장
  - 대시보드 스트림 오버레이가 policy-mode에서도 사용 가능한 detector/model drawer로 bbox 표시 가능
- `src/detection/model_registry.py`:
  - `detect_all()`에도 `detect_model()`과 동일하게 `class_map`/`adapt_detections()` 적용
- `src/pipeline/pipeline.py`:
  - `_should_submit_vlm()` 추가: `(camera_id, event_type)` TTL cache hit/miss 판단
  - `_submit_vlm_analysis()` 추가: 실제 VLM 제출 전에 cache 적용
  - `critical/high` alert level은 cache bypass로 항상 제출
  - policy-mode와 legacy `_handle_event()` VLM 제출 경로 모두 새 helper 사용
- `src/alerts/alert_manager.py` / `src/dashboard/app.py`:
  - `parse_alert_log_line()` 추가
  - 실제 formatter 출력(`timestamp | level | EVENT | {json}`) 기준으로 이벤트 로그 파싱 수정
  - `AlertManager.get_event_log()`와 대시보드 이벤트 로그 뷰어가 같은 parser 사용
  - alert logger handler 교체 시 기존 handler close 처리
- `.gitignore`:
  - `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `htmlcov/`, `.coverage*` ignore 추가
  - 모델 파일은 ignore하지 않고 `.gitattributes` LFS tracking 정책 유지
- Focused tests 추가:
  - `test_detect_all_applies_class_map`
  - `test_vlm_cache_skips_duplicate_non_critical_events`
  - `test_vlm_cache_bypasses_high_priority_events`
  - `test_alert_log_parser_reads_actual_formatter_line`
  - `test_get_event_log_reads_written_event`

### Changed files (M0)
- `.gitignore`
- `src/dashboard/app.py`
- `src/detection/model_registry.py`
- `src/pipeline/pipeline.py`
- `src/alerts/alert_manager.py`
- `tests/test_model_registry.py`
- `tests/test_pipeline.py`
- `.ai/task-state.md`
- `.ai/handoff.md`

### Verification (M0)
- `python3 -m compileall -q src tests scripts` — passed
- `./.venv/bin/python -m unittest discover tests -v` — **101 tests OK**
- `./.venv/bin/python scripts/e2e_smoke_test.py --policy-mode` — passed (`ok: true`, active models: `general_yolo`, `ppe_yolo`, `fire_smoke_yolo`, `failed_models={}`)
- `./.venv/bin/python scripts/stress_test.py --duration 10 --policy-mode` — passed (`ok: true`, `failed_frames=0`, `events_by_type={fire: 1}`)
- `./.venv/bin/python scripts/healthcheck.py --json` — passed (`ok: true`, 12 passes, 1 warning, 0 failures)
  - Warning: CUDA provider unavailable; CPU fallback used successfully.
- `git status --short && git lfs ls-files` — git status checked; `.venv/` no longer appears. `git-lfs` command unavailable.
- `git diff --check -- .gitignore src/alerts/alert_manager.py src/dashboard/app.py src/detection/model_registry.py src/pipeline/pipeline.py tests/test_model_registry.py tests/test_pipeline.py` — passed

### Blockers / remaining risks
- `git-lfs` is not installed in the current environment (`git: 'lfs' is not a git command`). Build did not install system packages by design.
- CUDA provider remains unavailable in ONNX Runtime; CPU fallback works.
- 대시보드 UI는 코드/테스트/healthcheck 기준으로 검증했지만 `bash scripts/run_dashboard.sh` 브라우저 수동 확인은 이번 milestone에서 실행하지 않음.
- `git status --short`에는 이번 M0 외에도 이전 미커밋 변경/신규 파일(`Dockerfile`, scripts, utils, models/data 등)이 남아 있음. `.venv/` noise는 제거됨.
- policy-mode는 CPU fallback 환경에서 3개 모델을 모두 실행하므로 latency가 높음(stress avg inference 약 1.68s/frame).

## Phase 10 M1 — 탐지 클래스 정합성 + Phase A 결함 수정 (완료 ✅, 2026-06-14)

### 탐지 클래스 정합성 (근본 수정)
- **문제**: detector가 ONNX 메타데이터를 읽지 않고 config `classes`를 인덱스로 사용 → config가 모델 실제 출력과 어긋나 silent 오라벨. 실측: ppe_yolo=`{0:Hardhat,1:NO-Hardhat}`(2), fire_smoke_yolo=`{0:fire-smoke,1:fog,2:sol,3:fire,4:factory-smoke}`(5) — config(각각 3·2개)와 불일치.
- `src/detection/yolo_detector.py`: `_extract_onnx_class_names()`(onnxruntime get_modelmeta `names` 파싱) 추가, `__init__`에서 `_init_session` 먼저 호출 후 `_resolve_classes(classes, profile, onnx_names)` 우선순위 재설계(메타데이터 1차, config는 override이되 개수 불일치 시 메타데이터 신뢰). 기존 dead code(gear_center_x/y, pw) 제거.
- `config/model_profiles.yaml`: ppe/fire `classes: null`(메타데이터 자동 인식), class_map 정정.
- `config/event_policies.yaml`: no_helmet overlap→direct_detection, no_vest 비활성(모델 미지원), smoke를 general_yolo→fire_smoke_yolo로 라우팅(dead config 수정).
- `tests/test_class_resolution.py`(신규 13건): 우선순위 + 실모델 통합(틀린 config 자동 교정 포함).

### Phase A — 잔여 정확성·안정성 결함
- `src/policy/event_policy_engine.py`: enabled_events=[] 논리 반전 수정, 룰 평가를 trigger.models 범위로 스코핑(크로스모델 중복 카운트 방지).
- `src/pipeline/pipeline.py`: `_state_lock` 추가(공유 상태 보호), `_record_event()`로 히스토리 일원화 + 이미지 최근 50건만 보존(메모리 ~6GB→~300MB).
- `src/alerts/alert_manager.py`: `cleanup_old_images()`를 handle_event에서 6시간 throttle 호출.
- `src/vlm/vlm_inference.py`: `analyze_with_image_array` RGB→BGR 변환(미사용 헬퍼 계약 정정).
- `docker-compose.yml`: config/models 볼륨 :ro→rw. `src/dashboard/app.py`: dead code `render_system_status`를 13번째 탭으로 연결(모델 상태는 실행 중 레지스트리 우선). `ruff.toml`: line-length top-level 이동(lint 훅 복구).
- `tests/test_policy.py`(+2): enabled_events=[] / 스코핑 회귀.
- 감사 오탐 확인: profile_pipeline.py:187(런타임 버그 아님 — 정리만), VLM 색공간(활성 경로 정상).

### Verification (캐시 클린)
- `./.venv/bin/python -B -m pytest tests/ -p no:cacheprovider -q` → **116 passed** / `-B -m unittest discover tests` → **116 OK**
- 실모델·실영상 종단: sample.mp4 → ONNX detect → policy → 콜백으로 fire/no_helmet 실제 발생, 이미지 해제 확인
- `scripts/e2e_smoke_test.py --policy-mode` → ok:true, failed_models={}
- 터치 파일 ruff F/E9 0건

### Remaining (장비/데이터 필요)
- Phase B(실 ONNX+VLM 종단 자동검증 게이트, 장비 불필요) → Phase C(DGX CUDA·실 RTSP) → Phase D(커스텀 모델 학습).
