# Camera Installer Onboarding

This guide applies after the signed Offline Appliance is installed. The Appliance Caddy HTTPS endpoint is the only
production access path; `scripts/install.sh` and `scripts/run_web.sh` are development/lab tools, not installer tools.

## Supported first release

- Ubuntu on DGX Spark ARM64 only
- One local appliance; 1, 4, 8, and 16 concurrent channels are separate target-soak evidence points, not pre-approved capacity claims
- ONVIF WS-Discovery with optional authenticated Media Profile lookup
- Manual RTSP fallback
- Offline appliance media

## Hardware-free verification

For development/lab verification only, start one or more virtual cameras before physical equipment arrives:

```bash
python3 -m lab.cctv_lab doctor
python3 -m lab.cctv_lab start 4
```

The streams are `rtsp://127.0.0.1:8554/cam_001` through `cam_004`. A host engine
must set `KSI_CCTV_ALLOW_LOOPBACK_CAMERA=true` only for this lab. Stop the lab with:

```bash
python3 -m lab.cctv_lab stop
```

A locally attached USB webcam may be used only as a lab source after publishing it through the loopback RTSP bridge in [USB Camera Lab](usb-camera-lab.md). It remains a Gateway/RTSP test stream, not a supported USB camera type.

## Installer workflow

1. Open the web console and select **카메라 추가**.
2. Choose ONVIF discovery or manual RTSP.
3. Enter a camera read-only account. Passwords are encrypted and never returned by the API.
4. For ONVIF, load Media Profiles when the optional `requirements-onvif.txt` bundle is installed.
5. Run the stream test. Saving is blocked until the test reports success.
6. Select the site profile and enabled events.
7. Optionally draw a normalized restricted-zone polygon.
8. Review and register. A mounted daemon applies the camera without restarting other cameras.
9. Use the camera card to retest, reapply, disable, or delete an installer-managed camera.

When `KSI_CCTV_MEDIAMTX_API_URL` is configured, registration also creates an internal,
on-demand MediaMTX path for the authenticated web HLS proxy. MediaMTX HLS and
control ports must remain private to the Compose network; only the web console is exposed.

## Camera API

```text
GET    /api/cameras
POST   /api/cameras
GET    /api/cameras/{id}
PUT    /api/cameras/{id}
DELETE /api/cameras/{id}
POST   /api/cameras/test
POST   /api/cameras/{id}/test
POST   /api/cameras/{id}/apply
POST   /api/cameras/{id}/enable
POST   /api/cameras/{id}/disable
PUT    /api/cameras/{id}/zones
GET    /api/cameras/{id}/snapshot
GET    /api/cameras/readiness
GET    /api/cameras/health
GET    /api/cameras/plan
POST   /api/cameras/discover
POST   /api/onvif/profiles
```

All endpoints require the engine API key. Public or link-local probe targets are denied. Camera
credentials are stored separately under `KSI_CCTV_CAMERA_DIR` and files are owner-only.

## Physical handoff checklist

- Put cameras and the appliance on a dedicated CCTV VLAN.
- Open the console through the Appliance Caddy HTTPS endpoint and trust the site CA on operator devices.
- Create read-only camera accounts and disable vendor default credentials.
- Prefer H.264 sub-streams at 720p and 5-15 FPS for analysis.
- Synchronize the switch, NVR, cameras, and appliance to the same NTP source.
- Verify ONVIF discovery, RTSP first frame, reconnect after camera reboot, and NVR-channel URLs.
- Do not expose RTSP, ONVIF, MediaMTX, or the engine API through internet port forwarding.
