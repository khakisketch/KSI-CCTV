# Offline Ubuntu Appliance

`installer/ksi_cctv_appliance.py` uses the fully resolved, hash-locked ARM64 host dependencies in `installer/requirements.txt`: `cryptography` for signatures/backups, PyYAML for duplicate-safe semantic Compose validation, and `license-expression` for SPDX validation. The approved wheelhouse must match `installer/wheelhouse.sha256` exactly. Provision it into the fixed host installer virtual environment and install the venv-bound launcher before invoking the CLI:

```bash
python3 -m venv /opt/ksi-cctv/installer-venv
(cd /media/approved-host-wheels && sha256sum --check /path/to/installer/wheelhouse.sha256)
/opt/ksi-cctv/installer-venv/bin/python -m pip install --require-hashes --no-index --find-links /media/approved-host-wheels -r installer/requirements.txt
installer/install_host_tools.sh
ksi-cctv-appliance doctor
```

The CLI does not use `sudo`; grant the invoking operator the required local filesystem, Docker, and systemd permissions during provisioning. The host wheelhouse is a separately approved provisioning input and is not copied into an Appliance release bundle.

## Layout

| Path | Purpose |
|---|---|
| `/opt/ksi-cctv/releases/<version>` | Immutable staged releases |
| `/opt/ksi-cctv/current` | Atomically switched release symlink |
| `/opt/ksi-cctv/transaction.json` | Durable install/upgrade/rollback/restore phase journal, outside restored trees |
| `/etc/ksi-cctv` | Site environment, release trust key, appliance state, and rollback metadata (mode 0700; files mode 0600) |
| `/run/lock/ksi-cctv-appliance.lock` | Host-local mutation lock, outside backup/restore targets |
| `/var/lib/ksi-cctv` | Engine-writable `config`, encrypted camera vault, outbox, captures, events, and uploaded models |
| `/var/log/ksi-cctv` | Appliance logs |
| `/var/backups/ksi-cctv` | External-key-encrypted pre-upgrade/operator backups (always mode 0700; archives mode 0600) |

Use `installer/systemd/ksi-cctv-compose.service` with `installer/compose.appliance.yaml.template`. The template defines engine, web, MediaMTX, and Caddy HTTPS services with digest-pinned ARM64 images. Engine and web run as their image UIDs. Only Caddy ports 80/443 are LAN-facing; web 3000 and MediaMTX RTSP/HLS/control stay inside the Compose network. The first Appliance release supports HLS only; WebRTC remains disabled pending separate WHEP/ICE validation. VLM/Ollama is also disabled until a separately signed offline runtime/model bundle is qualified. Caddy redirects HTTP and issues a site certificate from its persistent internal CA. The engine mounts stable config/data/upload/log paths, uses `KSI_CCTV_CAMERA_DIR=/app/data/cameras`, and synchronizes live-view paths through `KSI_CCTV_MEDIAMTX_API_URL=http://mediamtx:9997`.

## Signed Offline Release

`installer/offline-bundle-manifest.schema.json` is a schema reference, not a usable bundle. Generate manifests with `installer/release_builder.py`. A release directory has this layout:

```text
release/
  bin/activation_health.py
  bin/transaction_guard.py
  Caddyfile
  compose.yaml
  mediamtx.yml
  systemd/ksi-cctv-compose.service
  defaults/config/<allowlisted baseline files>
  images/*.tar
  sbom/*.spdx.json
  evidence/release-attestation.json{,.sig}
  evidence/bundle-attestation.json{,.sig}
  THIRD_PARTY_NOTICES
  model-inventory.json
  license-approvals.json
  release-contract.json
  release-manifest.json
  release-manifest.sig
```

Schema v3 additionally requires exactly four image archives, one image-bound SPDX 2.3 SBOM per image, immutable source revisions, model inventory, notices, structured external approval records, persistent-state readers, a signed strict software-gate attestation, and a CI-signed bundle statement covering the exact version, profiles, contract, local image config IDs, archive hashes, and payload hashes. `release-manifest.json` contains these contracts plus the fixed `healthcheck.command` `/usr/bin/python3 /opt/ksi-cctv/current/bin/activation_health.py`.

Provide two distinct trusted public keys separately, never in the release: the release-signing key and CI-attestation key. Initial install reads each key once from an owner-controlled path and atomically persists the pair under `/etc/ksi-cctv/trust-roots/`. In-place trust-key rotation is deliberately unsupported in v1 so rollback verification cannot lose its old trust root. `--allow-unsigned` is read-only for normal CLI use; mutating custom-root tests must inject a non-host command backend.

The manifest maps every `sha256:<config-id>` Compose image to a checksum-declared `images/*.tar` archive. Each archive must be a tagless, single-image Linux ARM64 Docker save; if it contains an OCI `index.json`, that index must have exactly one manifest bound to the same config ID. All four archives are validated before any daemon mutation. Activation then runs `docker image load` and requires machine-readable `docker image inspect` output to resolve the exact config ID and `arm64` architecture before switching releases. Compose must not contain `build:`. Models and Python wheels must be baked into the signed engine image; the builder rejects separate inert `models/` or `python-wheels/` payloads. The template is intentionally not buildable until real ARM64 config IDs replace all placeholders.

## Build A Release

The release pipeline needs distinct CI-attestation and release-signing Ed25519 identities. Run the strict gate with no signing credential on a clean frozen commit. In a separate trusted signing job/identity, run `release_attestation.py sign` against that unchanged clean checkout and finalized evidence; candidate tests and browser subprocesses must never receive the private-key path or bytes. Copy the attestation, detached signature, and evidence into `release/evidence`, create the schema-v3 SBOM/model/license/persistent-state `release-contract.json`, then run `scripts/bundle_attestation.py` with the CI private key. Shipped defaults are accepted only when their bytes match the clean checkout's signed `config_digests`; `cameras.json` is separately required to be empty. Only after that may `release_builder.py` verify the external CI public key and sign the canonical release manifest with the distinct release private key.

```bash
openssl genpkey -algorithm ED25519 -out /secure/ksi-cctv-release-signing.pem
openssl pkey -in /secure/ksi-cctv-release-signing.pem -pubout -out /secure/ksi-cctv-release-signing.pub
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out /secure/ksi-cctv-backup-recipient.pem
openssl pkey -in /secure/ksi-cctv-backup-recipient.pem -pubout -out /secure/ksi-cctv-backup-recipient.pub
RELEASE=/secure/ksi-cctv-release-staging/release
install -d -m 0700 "$RELEASE" "$RELEASE"/{images,defaults/config,defaults/config/prompts}
install -m 0644 installer/compose.appliance.yaml.template "$RELEASE/compose.yaml"
install -d -m 0755 "$RELEASE/bin" "$RELEASE/systemd"
install -m 0644 installer/activation_health.py installer/transaction_guard.py "$RELEASE/bin/"
install -m 0644 installer/systemd/ksi-cctv-compose.service "$RELEASE/systemd/"
install -m 0644 installer/Caddyfile "$RELEASE/Caddyfile"
install -m 0644 config/mediamtx.yml "$RELEASE/mediamtx.yml"
install -m 0644 config/{alert_preferences,camera_profiles,default,event_policies,model_profiles,proximity.example,zones.example}.yaml "$RELEASE/defaults/config/"
install -m 0644 config/mediamtx.yml "$RELEASE/defaults/config/mediamtx.yml"
install -m 0644 config/prompts/{__init__.py,construction.yaml,factory.yaml,general.yaml,parking.yaml,school.yaml,warehouse.yaml} "$RELEASE/defaults/config/prompts/"
# Appliance cameras are added in the web wizard, not shipped as sample records.
printf '[]\n' > "$RELEASE/defaults/config/cameras.json"
# Save each qualified source by its daemon identity so the archive is tagless, then derive the
# signed config ID from the archive bytes rather than assuming a Docker storage backend.
docker save --output "$RELEASE/images/ksi-cctv-engine.tar" "$(docker image inspect --format '{{.Id}}' registry.example/ksi-cctv-engine:<qualified-tag>)"
docker save --output "$RELEASE/images/ksi-cctv-web.tar" "$(docker image inspect --format '{{.Id}}' registry.example/ksi-cctv-web:<qualified-tag>)"
docker save --output "$RELEASE/images/mediamtx.tar" "$(docker image inspect --format '{{.Id}}' registry.example/mediamtx:<qualified-tag>)"
docker save --output "$RELEASE/images/caddy.tar" "$(docker image inspect --format '{{.Id}}' caddy:<qualified-tag>)"
ENGINE_ID=$(python3 scripts/image_archive_identity.py --archive "$RELEASE/images/ksi-cctv-engine.tar")
WEB_ID=$(python3 scripts/image_archive_identity.py --archive "$RELEASE/images/ksi-cctv-web.tar")
MEDIAMTX_ID=$(python3 scripts/image_archive_identity.py --archive "$RELEASE/images/mediamtx.tar")
CADDY_ID=$(python3 scripts/image_archive_identity.py --archive "$RELEASE/images/caddy.tar")
sed -i "s/REPLACE_ENGINE_ARM64_DIGEST/${ENGINE_ID#sha256:}/;s/REPLACE_WEB_ARM64_DIGEST/${WEB_ID#sha256:}/;s/REPLACE_MEDIAMTX_ARM64_DIGEST/${MEDIAMTX_ID#sha256:}/;s/REPLACE_CADDY_ARM64_DIGEST/${CADDY_ID#sha256:}/" "$RELEASE/compose.yaml"
python3 scripts/bundle_attestation.py --release-dir "$RELEASE" --version 2026.07.18 --profile dgx-spark-arm64 --private-key /secure/ci-attestation.pem --image-archive "$ENGINE_ID=images/ksi-cctv-engine.tar" --image-archive "$WEB_ID=images/ksi-cctv-web.tar" --image-archive "$MEDIAMTX_ID=images/mediamtx.tar" --image-archive "$CADDY_ID=images/caddy.tar"
python3 installer/release_builder.py --release-dir "$RELEASE" --version 2026.07.18 --profile dgx-spark-arm64 --private-key /secure/ksi-cctv-release-signing.pem --attestation-public-key /secure/ci-attestation.pub --image-archive "$ENGINE_ID=images/ksi-cctv-engine.tar" --image-archive "$WEB_ID=images/ksi-cctv-web.tar" --image-archive "$MEDIAMTX_ID=images/mediamtx.tar" --image-archive "$CADDY_ID=images/caddy.tar" --health-command /usr/bin/python3 /opt/ksi-cctv/current/bin/activation_health.py
install -d -m 0700 /media/usb/release
cp -a "$RELEASE"/. /media/usb/release/
```

`docker save` preserves the locally available image, so builders must first build or obtain and verify real ARM64 images containing the approved runtime, models, and optional ONVIF dependencies. This tooling does not build, cross-build, or qualify those images.

## Provisioning And Recovery Commands

다음 명령은 권한 있는 Appliance service engineer의 최초 provisioning 및 host-level recovery 전용입니다.
일반 현장 설치업자는 첫 기동 후 HTTPS 웹 콘솔을 사용합니다. 현재 host release upgrade/rollback과 전체
backup/restore는 web/engine container에 Docker socket 또는 root 권한을 넘기지 않기 위해 의도적으로 UI에
노출하지 않습니다. 이를 no-CLI 유지보수로 전환하려면 별도 최소권한 management service와 signed request,
물리적 복구 확인 절차가 release에 포함되어야 합니다.

Create an owner-only (0600) site environment before install. `KSI_CCTV_API_KEY`, `KSI_CCTV_WEB_SECRET`, and `KSI_CCTV_CAPTURE_KEY` must be distinct generated 32-byte URL-safe keys; `KSI_CCTV_WEB_PASSWORD` must be strong and distinct. `KSI_CCTV_SITE_ADDRESS` is the LAN hostname or usable IPv4 address used to open the console. Unknown variables are rejected. The installer stores only the site address in `ksi-cctv.env`, gives the engine API/capture secrets through `engine.env`, and gives the web service API/web secrets through `web.env`, all mode 0600. Do not put the source site environment or any private key on release media.

```bash
ksi-cctv-appliance status
ksi-cctv-appliance doctor --profile dgx-spark-arm64
ksi-cctv-appliance install --release-dir /media/usb/release --site-env /secure/ksi-cctv.env --profile dgx-spark-arm64 --public-key /secure/ksi-cctv-release-signing.pub --attestation-public-key /secure/ci-attestation.pub --backup-recipient-public-key /secure/ksi-cctv-backup-recipient.pub --confirm
ksi-cctv-appliance upgrade --release-dir /media/usb/release --profile dgx-spark-arm64 --public-key /etc/ksi-cctv/trust-roots/release-signing.pub --confirm
ksi-cctv-appliance rollback --confirm
ksi-cctv-appliance recover --confirm  # interrupted transaction before candidate activation
ksi-cctv-appliance backup --confirm
ksi-cctv-appliance restore --archive /var/backups/ksi-cctv/ksi-cctv-*.tar.gz.enc --backup-recipient-private-key /secure/ksi-cctv-backup-recipient.pem --validate-only
ksi-cctv-appliance restore --archive /var/backups/ksi-cctv/ksi-cctv-*.tar.gz.enc --backup-recipient-private-key /secure/ksi-cctv-backup-recipient.pem --confirm
```

`status`, `doctor`, and `restore --validate-only` do not modify durable Appliance state; validation decrypts only beneath the production tmpfs `/run` and removes that staging before returning. `install`, `start`, `stop`, `restart`, `backup`, state-changing `restore`, `upgrade`, `rollback`, and `lab` require `--confirm` before decryption or mutation. Status invokes read-only `systemctl is-active`; service actions invoke `systemctl` without a shell. Command output masks conventional password, secret, token, and API-key assignments.

Staged release trees are owner-controlled, read-only, fsynced, and payload checksums are repeated immediately before privileged use. A host operation lock serializes mutations, and install/upgrade/rollback/recovery write a durable phase journal before staging or service changes. Upgrade and rollback stop the service for a coherent encrypted backup. Rollback selects prior code but does not automatically restore data. If failed candidate activation changed persistent bytes, prior code is selected but the service remains stopped; inspect the backup, explicitly restore if needed, then use `recover --accept-live-data` only when retaining those live bytes is an informed operator decision. Activation also extracts the inert engine container filesystem without starting it and requires all `.onnx`, `.pt`, and `.engine` files under `/app/models` to match the signed inventory exactly.

Support bundles contain a manifest signed by the Appliance-local audit Ed25519 key plus the public key and per-file SHA-256 hashes. Record that public-key fingerprint during commissioning, then verify a received bundle before analysis:

```bash
python3 scripts/verify_support_bundle.py --bundle /secure/ksi-cctv-support.zip --expected-audit-key-fingerprint <commissioning-sha256>
```

Systemd also runs signed `activation_health.py` through `ExecStartPost` after every boot. Release and persistent state schema are both versioned; this first release supports state schema 1 and rejects incompatible state rather than starting without a migration. Dynamic MediaMTX paths are reconciled by the engine periodically after gateway restarts.

After first start, install Caddy's local root CA from `/etc/ksi-cctv/caddy-data/caddy/pki/authorities/local/root.crt` into each operator device's trust store, then open `https://<KSI_CCTV_SITE_ADDRESS>`. For managed corporate devices, distribute this CA through MDM. Never bypass the browser certificate warning for production use.

Backups are always encrypted with AES-256-GCM under the external RSA recipient public key supplied at install and signed by an Appliance-local Ed25519 identity stored outside restored trees. The signed provenance binds the exact ciphertext, Appliance identity, monotonic sequence, recipient key, and release/CI trust-root fingerprints. The Appliance stores only the RSA public recipient key; the private key remains with the operator and is required for validation or restore. Cross-Appliance, forged, trust-mismatched, and replayed archives fail closed. A deliberate same-Appliance replay requires the service engineer to add `--allow-backup-replay --confirm` and record the recovery rationale. Record the SHA-256 fingerprint of `/opt/ksi-cctv/backup-identity/backup-signing.ed25519.pub` during commissioning. Plaintext legacy archives are intentionally rejected. Treat archives, provenance sidecars, and the external private key as confidential.

If either persisted Ed25519 trust root or `/opt/ksi-cctv/backup-identity` is lost or suspected compromised, stop rollout and the Appliance service. The built-in restore command is intentionally limited to the same Appliance identity and matching trust roots; it cannot import an old backup into a newly keyed reprovisioned Appliance. Preserve the encrypted archive, provenance sidecars, external RSA key, old Appliance backup-signing public key, and trust fingerprints for an organization-approved offline recovery/extraction process. Fully reprovision from media signed by newly approved distinct keys, then re-onboard recovered site data only through a separately approved migration procedure. Do not overwrite trust or backup-identity files in place, and do not use the unsigned development path.

`doctor` reports hard failures for Docker, Compose, systemd, architecture/profile, and a corrupt installed release. It separately reports warnings for low/unmeasurable storage and NVIDIA Docker runtime/provider detection. The first-release `dgx-spark-arm64` profile requires ARM64 and compares the device-tree model when available. Remaining qualification gates are physical GPU/container runtime validation, DGX driver compatibility, actual camera/PoE/NVR interoperability, and thermal/storage endurance testing. Optional lab delegation is available only when `/opt/ksi-cctv/lab/bin/ksi-cctv-lab` is installed, and is passed arguments without a shell.

`--allow-unsigned` is forbidden for host-backed provisioning, upgrade, rollback, and recovery. It is accepted by `verify`, while mutating developer fixtures must inject a non-host command backend.
The Appliance installer also rejects truthy `KSI_CCTV_ALLOW_LOOPBACK_CAMERA`,
`KSI_CCTV_DEV_AUTH_BYPASS`, and `KSI_CCTV_WEB_DEV` values in the site environment.

For isolated tests or image construction, pass `--root /path/to/root`; the same layout is created below that root.
