# USB Camera Lab Source

This helper is for development benches only. KSI-CCTV first-release production input remains ONVIF discovery or manual RTSP from IP cameras/NVR channels. Do not add `/dev/video*` to Appliance Compose or advertise USB support.

## Preconditions

- A Linux UVC/V4L2 device such as `/dev/video0`.
- FFmpeg with the `v4l2` input device.
- A loopback MediaMTX lab publisher endpoint. Start the virtual lab first when using its default port:

```bash
python3 -m lab.cctv_lab start 1
python3 scripts/usb_camera_lab.py doctor
```

## Publish

Inspect the shell-free command before starting:

```bash
python3 scripts/usb_camera_lab.py publish --device /dev/video0 --video-size 1280x720 --fps 15 --dry-run
```

Publish frames to the isolated lab path:

```bash
python3 scripts/usb_camera_lab.py publish --device /dev/video0 --video-size 1280x720 --fps 15
```

The stable lab stream is `rtsp://127.0.0.1:8554/cam_usb_lab`. If the webcam only exposes MJPEG or another known format, pass a safe format token such as `--input-format mjpeg`.

For a host engine, set `KSI_CCTV_ALLOW_LOOPBACK_CAMERA=true` only for this lab session, then register the stream through manual RTSP or discover it as a Gateway stream. Stop the publisher with Ctrl+C and then run `python3 -m lab.cctv_lab stop`.

## Evidence And Limits

Use this source to validate first frame, HLS display, telemetry, event persistence, Incident Center handling, stale/recovery behavior, and USB unplug/replug behavior. Record negotiated settings and duration, but do not retain camera credentials or raw sensitive footage in reports.

It does not validate ONVIF, PoE, VLAN, NVR channels, camera vendor authentication, real IP-camera behavior, multi-camera capacity, field accuracy, or Appliance release qualification.
