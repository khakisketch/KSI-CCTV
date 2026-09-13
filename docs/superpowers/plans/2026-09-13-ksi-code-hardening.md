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
