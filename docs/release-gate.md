# Release Gate

KSI-CCTV release approval has two independent gates: repeatable software evidence and
target-appliance evidence. Passing CI does **not** approve an ARM64 image, a camera
capacity claim, model accuracy, or a customer deployment by itself.

## Authoritative Advanced-AI v1 Scope

The v1 release target is a DGX Spark ARM64 offline Appliance with HLS and a single-admin
console. Implemented baseline capability is COCO/PPE/fire-smoke policy detection plus
ByteTrack `line_crossing` and `loitering` tracking. Pose/ST-GCN fall/fight and bounded local
VLM verification are in development, not release-approved capabilities.

VLM verification must fail open: provider, timeout, or parsing failure creates and retains an
`unverified` alert; it must never silently suppress the candidate event. WebRTC, cloud/native
apps, RBAC, production-default open-vocab, LLM reporting, automatic few-shot/GNN workflows,
and theft/weapons/smoking are outside v1.

No ARM64 CUDA artifact, field-data accuracy approval, real-camera/HLS soak, recovery record,
or concurrent-channel capacity has been accepted by this document. In particular, 16-channel
operation is a required evidence target only, not a proven or approved claim.

## Release Unit

The release unit is a signed schema-v3 offline Appliance bundle containing four digest-pinned
ARM64 images, image-bound SPDX SBOMs, model/notices/approval inventory, persistent-state
contract, strict gate evidence, a CI-signed exact-payload statement, configuration defaults,
`activation_health.py`, and the separately release-signed manifest. Site secrets, camera source
URLs, trust keys, and signing private keys are never release artifacts.

| Gate | Owner | Required evidence | Pass rule |
|---|---|---|---|
| Software CI | Engineering | Internal runner gate log, Python test output, browser report | `bash scripts/verify_release_candidate.sh --strict` passes; no unexplained skips |
| Bundle | Release engineer | Signed manifest verification and image/archive mapping | Signature, hashes, image digests, and activation contract pass |
| Browser console | Frontend owner | Playwright HTML report | Login boundary, protected dashboard, LiveWall navigation, logout pass |
| Target soak | Appliance engineer | Per-channel JSON reports and operator notes | Defined camera, availability, FPS/drop, GPU, thermal, disk, reconnect checks pass |
| Recovery | Appliance engineer | Upgrade, code-only rollback, encrypted restore, reboot/power recovery records | Unchanged data permits healthy code recovery; changed data leaves service stopped until operator validation/restore |
| Model/legal/AI | Product + legal | Field-video accuracy report, license approval, pose/ST-GCN and VLM failure-mode evidence when enabled | Explicit approval for each deployed model, operating domain, and `unverified` alert behavior |

## Automated Software Gate

The runner-neutral gate documented in [internal-ci.md](internal-ci.md) runs the full Python suite,
signed-Appliance contract tests, lint, shell syntax/ShellCheck, Compose rendering, frontend unit
tests, standalone checks, build, Python/runtime and production npm dependency audits, and Chromium Playwright coverage. Browser fixtures cover auth,
camera registration/actions, events export, model controls, and commissioning/handoff, but do not
prove real camera video or inference.

Run the complete release candidate gate on the internal runner:

```bash
bash scripts/verify_release_candidate.sh --strict
```

The gate runs without CI signing credentials. Sign its finalized attestation afterward with the
`release_attestation.py sign` command in a separate trusted signer job; do not expose that key to
Python tests, npm, Playwright, the checkout, or the release payload.

Keep the internal runner browser report with the release record. A failed test is a failed gate; do not
waive it by rerunning against a locally modified bundle.

## Signed Bundle Gate

The real ARM64 image archives and immutable digests are supplied by the release engineer.
The engine image build additionally requires a reviewed ARM64 ONNX Runtime CUDA-provider
wheel, supplied as immutable `ONNXRUNTIME_GPU_WHEEL_URL` and
`ONNXRUNTIME_GPU_WHEEL_SHA256` build arguments. PyPI does not publish this artifact for ARM64.
Do not use a CPU wheel, an unpinned URL, or a URL containing credentials; the image build
verifies the wheel hash and that `CUDAExecutionProvider` is compiled in before it can complete.

Build a candidate CUDA 13 wheel only on a DGX Spark runner, then retain the command output and
SHA-256 with the release record before publishing it to the approved internal artifact store:

```bash
bash scripts/build_onnxruntime_wheel.sh /secure-release/onnxruntime
```

The script builds pinned ONNX Runtime `v1.28.0`, uses a CUDA 13 ARM64 image, and proves
CUDA-only inference for every required built-in YOLO model on the target GPU. Retain the wheel
manifest and qualification JSON with the release record.
Generate the bundle as documented in [offline-appliance.md](offline-appliance.md), then
verify it on a clean staging host before delivery:

```bash
bash scripts/verify_appliance_bundle.sh \
  --release-dir /media/usb/release \
  --profile dgx-spark-arm64 \
  --public-key /etc/ksi-cctv/release-signing.pub \
  --attestation-public-key /etc/ksi-cctv/ci-attestation.pub
```

Record the manifest version, manifest SHA-256, signing-key fingerprint, exact image digests,
and verifier result. Do not record the private key, site environment, camera credentials, or
raw archive contents. `--allow-unsigned` is never acceptable outside isolated developer
fixtures.

## Target Soak Gate

Run a separate steady-state soak for each camera count for which approval is sought (for example,
1, 4, 8, or 16). No listed count is implied to have passed.
The sequential `scripts/stress_test.py --cameras` mode is not concurrent-camera evidence.
Use real RTSP cameras, the target ARM64 appliance, and the intended model/runtime.

```bash
export KSI_CCTV_API_KEY='from-approved-secret-store'
python3 scripts/appliance_soak.py \
  --engine-url https://cctv.example.internal \
  --ca-file /etc/ksi-cctv/caddy-data/caddy/pki/authorities/local/root.crt \
  --duration 1800 \
  --interval 30 \
  --expected-cameras 4 \
  --max-cpu-percent 85 \
  --max-memory-percent 85 \
  --require-gpu-metrics \
  --min-camera-fps 5 \
  --max-camera-drop-rate 0.05 \
  --json-out reports/appliance-soak-4cam.json
```

The report deliberately hashes camera identifiers and excludes source URLs, API keys, health
messages, request bodies, and model identifiers. Disk is a default gate and fails when unavailable.
CPU and memory are optional gates: use `--require-cpu-metrics` or `--require-memory-metrics` to
require availability, and `--max-cpu-percent` or `--max-memory-percent` to set site-approved
limits. Optional `--max-model-error-rate`, `--max-model-consecutive-errors`, and
`--max-vlm-latency-ms` gates fail if their authenticated `/api/health` metrics are unavailable;
they do not infer activity or capability when no usable model/VLM values are exposed. Set approved
FPS, drop, temperature, and disk thresholds for the specific site before running; the defaults are
safety baselines, not a universal capacity claim.

For an operator-approved service recovery test, use a separate run with an explicit local
command and a tolerated transient readiness rate. The command is never run without both flags:

```bash
python3 scripts/appliance_soak.py \
  --engine-url https://127.0.0.1 \
  --insecure-tls \
  --duration 900 --interval 15 --expected-cameras 4 \
  --restart-command 'systemctl restart ksi-cctv-compose' \
  --restart-after-sec 120 --allow-restart --require-recovery \
  --require-post-restart-recovery \
  --min-readiness-pass-rate 0.80 \
  --json-out reports/appliance-recovery.json
```

Use `--insecure-tls` only on the appliance loopback address. Camera power/PoE interruption and
full appliance power-loss recovery remain physical procedures: record the operator, time,
affected cameras, recovery time, `activation_health.py` result, and post-recovery soak report.
The restart report records only the local program basename, execution result, and a separate
post-restart healthy-and-ready evidence signal; it does not retain command output.

## Approval And Rollback

1. Release engineer confirms internal runner, signed-bundle evidence, and all required approvals before media leaves controlled storage.
2. Appliance engineer installs the candidate using the signed offline procedure and saves activation, soak, upgrade, rollback, and reboot evidence under the release record.
3. If activation, camera readiness, thermal/disk, recovery, or model acceptance fails, stop rollout. `rollback --confirm` changes code only and first preserves current data in a new encrypted backup. If candidate-written data is detected, keep the service stopped and validate or explicitly restore data before `recover`; retain all failed evidence.
4. Product and legal approve field-video accuracy and model licensing before customer enablement. No automated test can substitute for this gate.

Retain CI links, reports, manifests, signatures, hashes, and approval decisions according to the
customer retention policy. Treat reports and backups as operationally sensitive even though the
collector redacts credentials.
