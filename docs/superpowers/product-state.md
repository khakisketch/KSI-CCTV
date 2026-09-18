# Product State - KSI-CCTV

Updated: 2026-09-18

## Goal

DGX Spark ARM64 오프라인 Appliance에서 CCTV 영상을 분석해 안전장비 미착용·화재/연기·침입/배회를 감지·알림하는 설치형 AI 안전관리 솔루션. `config/*`(camera_profiles·event_policies·model_profiles)로 현장별 커스터마이징하고, Next.js 단일 웹 콘솔로 운영한다.

## Milestones

| id | milestone | status | evidence |
| --- | --- | --- | --- |
| M1 | Phase 1–10 골격 — 탐지 레지스트리·정책 엔진·SafetyPipeline·모델 업로드·운영도구 | done | `src/`, `tests/` (1021+ passed 기준선) |
| M2 | 웹 전환 — Streamlit 폐기 → Next.js 단일 콘솔(HLS/SSE/인증) | done | `frontend/`, `docs/architecture.md` |
| M3 | Release-hardening — appliance 번들·서명·healthcheck·게이트 | done | `docs/release-gate.md`, 커밋 `1b5219f` 이전 이력 |
| M4 | 코드 구조화 7-Unit (U1–U8) — SSOT 정리·legacy 격리·스토리지 단일진실·prune 수정 | done | 커밋 `c7c428f`, `9b18b80` |
| M5 | 구조 작업 트랙 (U9–U12) — http.server 제거·legacy opt-in·라우터 5모듈 분할 | done | 커밋 `37c9090`–`8d6c44f`, `routes/` 66 라우트 불변 |
| M6 | 후속 개선 — 레거시 cameras.json 이관 도구·문서 | done | 커밋 `62f647b`, `tests/test_migrate_legacy_cameras.py` |
| M7 | 출고 증거 — ARM64 실측(CUDA·실카메라/HLS)·soak·법률·번들 서명 provenance·strict gate | in_progress | strict gate PASS 반복 확인(attestation `passed:true`; evidence: `reports/release-candidate/`); 잔여: ARM64 실측·법률·서명 |

## Current slice

- Name: M7 출고 증거 — 소프트웨어 게이트 완료, 하드웨어·법률 증거 대기
- Acceptance (user-visible end-to-end): (1) strict release gate가 클린 커밋에서 통과 — 달성(attestation `passed:true`·evidence 5종, `reports/release-candidate/` 보관) (2) DGX Spark ARM64 실측(CUDA provider·실 RTSP/HLS·전원/복구·채널별 soak) (3) 법률 승인·4-image 서명 provenance
- Plan ledger: `docs/superpowers/plans/2026-09-13-ksi-code-hardening.md`

## Next slices

- [proposed] P1 RBAC/다중관리자 — 인증·세션·감사 actor 재설계 필요(설계 세션 선행, 코드-only 가능, 확장 1순위)
- [proposed] P2 알림 채널 확장(webhook/SMS) — 현재 email(outbox)만; 요구 확인 후 설계
- [proposed] P3 LLM 보고 — 이벤트/인시던트 데이터는 있으나 보고 서브시스템 없음; 요구 정의 필요
- [data/legal] theft/weapons/smoking·production open-vocab — 모델·라이선스·정확도 자격 의존(코드-only 불가)

## Backlog

- [2026-09-16] mypy advisory 44건(게이트 비차단, 점진 해소 대상) - source(test.sh) - not blocking
- [2026-09-16] VLM `runtime_qualified=false` — qwen3-vl 214s/frame으로 timeout 초과(소형/양자화 모델 또는 비동기 경로 선행 필요) - source(release-gate) - not blocking
- [2026-09-16] 라우터 분할 잔여 미세 정리(모듈 내부 헬퍼 결합, `_build_checker`/`_runtime_event_catalog` 주입 구조) - source(review) - not blocking
- [2026-09-18] release gate 로그 라벨 불일치 — `[8/9]` 헤더 없이 `[9/9]`로 점프(표기 문제, 기능 영향 없음) - source(gate) - not blocking
