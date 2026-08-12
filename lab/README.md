# 가상 CCTV 랩

카메라 장비 없이 `data/sample.mp4`를 반복 송출하는 독립 Docker Compose 랩이다. 기존 루트 `docker-compose.yml`과 별개이며, 기본 포트는 loopback에만 바인딩한다. 실제 촬영 샘플로 검증하려면 `LAB_SAMPLE_FILE=/absolute/path/video.mp4`를 지정한다.

## 시작

```bash
python3 lab/cctv_lab.py doctor
python3 lab/cctv_lab.py start 4
python3 lab/cctv_lab.py status
```

`start`의 카메라 수는 1~16이며 기본값은 1이다. 경로는 항상 `cam_001`부터 순서대로 할당된다. 브라우저 HLS 호환을 위해 샘플을 H.264 360p/5fps로 낮춰 반복 발행한다(`ultrafast`, 400 kbps). 16개 실행 시에도 하드웨어 가속은 사용하지 않는다.

## URL

카메라 N의 RTSP는 `rtsp://127.0.0.1:8554/cam_00N`이고, HLS는 `http://127.0.0.1:8888/cam_00N/index.m3u8`이다. 예를 들어 1번은 다음과 같다.

```text
Host RTSP:      rtsp://127.0.0.1:8554/cam_001
Host HLS:       http://127.0.0.1:8888/cam_001/index.m3u8
Host API:       http://127.0.0.1:9997/v3/paths/list
Container RTSP: rtsp://mediamtx:8554/cam_001
Container HLS:  http://mediamtx:8888/cam_001/index.m3u8
```

`cam_001`부터 `cam_016`까지가 미리 정의되어 있다. 실행하지 않은 카메라 경로에는 스트림이 없다.
`cam_usb_lab`은 `scripts/usb_camera_lab.py`만 사용하는 loopback USB bench path다.

## 제어

```bash
python3 lab/cctv_lab.py stop-camera 2
python3 lab/cctv_lab.py restart-camera 2
python3 lab/cctv_lab.py fault-cycle 2 --down-sec 35
python3 lab/cctv_lab.py reset 8
python3 lab/cctv_lab.py stop
```

`reset`은 랩 컨테이너와 랩 볼륨만 제거한 뒤 지정된 수로 재시작한다. 기존 애플리케이션 Compose, 소스, 데이터는 건드리지 않는다.
`fault-cycle`은 publisher를 지정 시간 동안 중지한 뒤 재기동한다. host engine에서는 이 시간 동안 stale/disconnected, 재기동 뒤에는 recovered 상태와 Incident Center 복구 감지를 확인한다.

## 설정과 제한

이미지와 바인드 주소 템플릿은 `lab/.env.example`에 있다. 다른 장비에서 접근해야 할 때만 `LAB_BIND_ADDRESS=0.0.0.0`으로 명시적으로 설정하고, 방화벽 또는 신뢰 가능한 사설망을 사용한다.

호스트 엔진에서 `rtsp://127.0.0.1:8554/cam_001`을 카메라 마법사로 검사할 때만
`KSI_CCTV_ALLOW_LOOPBACK_CAMERA=true`를 설정한다. 운영 환경에서는 SSRF 보호를 위해 이 값을
사용하지 않고 CCTV 전용 사설망 주소만 허용한다.

이미지 태그는 재현 가능한 버전으로 고정했지만 레지스트리 태그는 공급자가 변경할 수 있으므로, 완전한 공급망 불변성이 필요하면 조직의 검증된 digest로 `LAB_MEDIAMTX_IMAGE`와 `LAB_FFMPEG_IMAGE`를 재정의해야 한다.

## 테스트

기본 테스트는 Docker 데몬을 요구하지 않는다.

```bash
./.venv/bin/python -B -m pytest tests/test_virtual_cctv_lab.py -p no:cacheprovider -q
```

Compose 렌더링 검증은 명시적 opt-in이다.

```bash
KSI_CCTV_LAB_INTEGRATION=1 ./.venv/bin/python -B -m pytest tests/test_virtual_cctv_lab.py -p no:cacheprovider -q
```
