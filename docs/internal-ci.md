# Internal CI Gate

KSI-CCTV의 release candidate 검증은 GitHub Actions에 의존하지 않습니다. 사내 runner 또는
격리된 Ubuntu build host에서 아래 단일 blocking gate를 실행하고, 생성된 로그와 browser report를
release record에 보관합니다.

## Runner Requirements

- Ubuntu Linux build host, Python 3.12, Node.js 22, Docker Engine + Compose v2
- `shellcheck`, `mypy`, `ruff`, pytest 개발 의존성
- Chromium Playwright 의존성. runner image provisioning 시 한 번 실행:

```bash
cd frontend
npx playwright install --with-deps chromium
```

실제 ARM64 image build, image archive load, Appliance install은 이 runner가 아니라 승인된 ARM64
staging Appliance에서 실행합니다.

## Blocking Gate

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt -r requirements-onvif.txt -r requirements-dev.txt
bash scripts/verify_release_candidate.sh --strict
```

이 gate에는 CI signing key를 주입하지 않습니다. Finalized attestation은 candidate process가 끝난 뒤 별도
trusted signer identity/HSM job에서 `python3 scripts/release_attestation.py sign --attestation ... --root ...
--private-key ... --signature-out ...`로 서명합니다. Source checkout/artifact/release payload 내부 key는 거부됩니다.

이 명령은 Python lint/test, 인증/SSE·durable event·incident schema·API supervision·schema-v3 bundle core type check, shell syntax/ShellCheck,
Compose rendering, systemd unit contract, runtime/ONVIF `pip-audit`, production `npm audit` high/critical, frontend lint/unit/build/standalone,
Chromium Playwright를 차단형으로 실행합니다.
`--skip-browser`는 개발자 빠른 확인 전용이며 release evidence에 사용할 수 없습니다.

## Required Evidence

- command stdout/stderr 및 실행 host OS/arch, Python/Node/Docker 버전
- pytest 결과, `reports/release-candidate/pytest.xml`, `reports/release-candidate/skip-summary.json`
- `reports/release-candidate/npm-audit.json`, `reports/release-candidate/npm-audit-summary.json`
- `reports/release-candidate/pip-audit-runtime.json`, `reports/release-candidate/pip-audit-onvif.json`, `reports/release-candidate/pip-audit-summary.json`
- 허용된 외부 의존 skip만 포함된 결과: daemon 실 ONNX/sample 영상, opt-in VLM,
  opt-in YOLO-World/ultralytics, opt-in Docker virtual lab
- Playwright HTML report (`frontend/playwright-report/`, gate가 `CI=1`로 생성)
- production audit high/critical finding이 없음을 보이는 audit summary. 예외를 strict gate 우회로 사용하지 않습니다.
- signed strict gate attestation/signature와 exact-payload bundle attestation/signature
- `scripts/verify_appliance_bundle.sh` 결과, manifest SHA-256, release/CI public-key fingerprints
- target Appliance soak/recovery JSON 및 운영자 기록

`scripts/check_test_skips.py`는 위 목록 밖의 새 skip을 blocking failure로 처리합니다. 허용 목록 변경,
warning waiver, 또는 `--skip-browser` 사용은 release engineer의 명시적 승인 없이는 release gate
통과로 처리하지 않습니다.
