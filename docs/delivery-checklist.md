# KSI-CCTV 납품 및 시연 체크리스트

이 문서는 현장 납품 전 설치 점검과 시연 진행 절차를 제공합니다.

자동 소프트웨어 게이트와 물리 검증은 분리한다. CI는 Ruff와 Python/프론트엔드 전체 테스트를 실행하고, sequential stress gate의 임계값 계산은 단위 테스트한다. `scripts/stress_test.py`의 camera ID 분산 실행은 정책 상태 격리 검증이며 1~16채널 처리 용량의 증거가 아니다. 실제 동시 채널, 카메라, 조도/IR, 네트워크, GPU 열 및 장기 soak은 현장 증적으로 별도 승인해야 한다. 이 문서는 물리 검증이 이미 수행되었다고 주장하지 않는다. 담당자·증적·합격 규칙은 [release-gate.md](release-gate.md)를 따른다.

Advanced-AI v1은 DGX Spark ARM64, HLS, single-admin만 지원한다. 구현된 Tracking 범위는 ByteTrack
`line_crossing`과 `loitering`이다. pose/ST-GCN fall/fight와 bounded local VLM verification은 개발 중이며
qualification 전에는 출고 capability가 아니다. VLM 실패는 반드시 `unverified` alert로 남고 silent
suppression이 허용되지 않는다. WebRTC, Jetson, cloud/native app, RBAC, production 기본 open-vocab,
LLM reporting, 자동 few-shot/GNN, theft/weapons/smoking은 v1 범위 밖이다.

---

## 파트 A: Ubuntu Appliance 납품 체크리스트

운영 납품은 저장소 clone, `.venv`, `scripts/install.sh`, `scripts/run_web.sh`, 일반 Docker Compose, 평문 HTTP 경로를 사용하지 않습니다.
서명된 오프라인 Appliance 절차만 사용하며 상세 명령은 `offline-appliance.md`를 따릅니다. 이는 유일한 운영 경로입니다.

### A1. 출고 승인

- [ ] 대상별 실제 ARM64 engine/web/MediaMTX/Caddy image tar와 immutable digest 준비
- [ ] distinct CI/release Ed25519 keys로 strict gate attestation, exact-payload bundle statement, manifest가 모두 검증됨
- [ ] image별 image-digest-bound SPDX 2.3 SBOM, notices, model inventory, structured license approval artifact 검증됨
- [ ] signed systemd unit이 release에 포함되고 target에서 daemon-reload/enable됨
- [ ] 엔진 이미지 내부 모델·ONVIF 의존성·SBOM·출처 확인
- [ ] YOLO 계열 가중치의 AGPL 준수 또는 Enterprise License 승인 완료
- [ ] `--allow-unsigned`를 사용하지 않음

### A2. 장비 및 Site Secret

- [ ] DGX Spark에서 `doctor --profile dgx-spark-arm64` hard failure 없음
- [ ] Docker Compose·NVIDIA runtime·저장공간·시간 동기화 확인
- [ ] 0600 site env에 강력하고 서로 다른 API key/web password/web secret/Fernet capture key 설정
- [ ] 외부 보관 RSA backup recipient public key 준비, private key는 Appliance에 저장하지 않음
- [ ] `KSI_CCTV_SITE_ADDRESS`를 현장 LAN hostname/IP로 설정
- [ ] CCTV VLAN과 운영자 단말 외 접근 차단

### A3. 설치 및 부팅

```bash
python3 installer/ksi_cctv_appliance.py install \
  --release-dir /media/usb/release \
  --site-env /secure/ksi-cctv.env \
  --profile dgx-spark-arm64 \
  --public-key /secure/ksi-cctv-release-signing.pub \
  --attestation-public-key /secure/ci-attestation.pub \
  --backup-recipient-public-key /secure/ksi-cctv-backup-recipient.pub \
  --confirm
```

- [ ] `status`가 설치 버전과 active service를 표시
- [ ] systemd `ExecStartPost` activation health 통과
- [ ] 재부팅 후 engine/web/gateway/HTTPS가 자동 복구
- [ ] Caddy site CA를 운영자 단말 trust store에 배포
- [ ] `https://<KSI_CCTV_SITE_ADDRESS>`에서 로그인 성공, 평문 web 3000 외부 노출 없음

### A4. 카메라 온보딩

- [ ] 웹 wizard에서 ONVIF 검색 또는 수동 RTSP로 승인된 실제 카메라 수만큼 등록
- [ ] 저장 전 각 카메라 first-frame 검사 성공
- [ ] 프로파일·활성 이벤트·normalized zone 설정 확인
- [ ] 카메라 재부팅/단선 후 자동 재연결과 MediaMTX live-view 복구 확인
- [ ] `/api/cameras/readiness`가 모든 활성 카메라를 healthy로 표시

### A5. 검증 및 복구

- [ ] 자동 소프트웨어 게이트: CI의 Ruff와 Python/프론트 전체 suite가 통과하고 skip 사유가 문서화됨
- [ ] RC 환경에서 `scripts/stress_test.py` 임계값 gate를 별도 실행하고 결과 JSON을 보관함(동시 채널 용량 증거로 사용하지 않음)
- [ ] 승인하려는 실제 동시 채널 수별 `scripts/appliance_soak.py` JSON 증적, GPU provider, FPS, drop, 온도, 디스크 합격 (16채널은 현재 미입증)
- [ ] synthetic event에서 직접 ONNX 탐지→정책→outbox→알림 경로 확인
- [ ] signed upgrade 성공; 강제 health 실패에서 data unchanged는 자동 code recovery, data changed는 service-stopped/operator restore 경로 검증
- [ ] backup/restore와 전원 차단 후 복구 rehearsal 성공
- [ ] support bundle manifest/hash/signature를 commissioning audit-key fingerprint와 함께 `verify_support_bundle.py`로 검증

---

## 파트 B: 시연용 체크리스트

시연 담당자가 시연 당일 사용하는 절차입니다.

### B1. 시연 전 준비

- [ ] 시연 장비에 KSI-CCTV Appliance 웹 콘솔이 기동 중임 확인 (`https://<site-address>`)
- [ ] 시연용 샘플 영상 준비 (`data/sample.mp4` 또는 실제 현장 영상)
  - 권장: 사람이 포함된 영상 (raw HLS와 별도 tracking overlay를 함께 확인)
  - 선택: 안전모 미착용/화재 장면 (커스텀 모델 사용 시)
- [ ] CCTV 카메라가 설치 업자 wizard에서 등록·first-frame 검증됨
- [ ] VLM verification을 시연할 경우, approved local runtime/model과 bounded queue를 사용하고 실패 시 `unverified` alert가 표시됨 확인; 미승인 상태면 v1 capability로 시연하지 않음
- [ ] (이메일 시연 시) SMTP 설정 완료 및 테스트 완료

### B2. 시연 동선

#### 2-1. 웹 콘솔 로그인

- [ ] site CA를 신뢰한 브라우저에서 `https://<site-address>` 접속
- [ ] 비밀번호 입력 후 로그인

#### 2-2. 파이프라인 시작

- [ ] 카메라 페이지에서 카메라 소스 선택 (영상 파일 또는 RTSP)
- [ ] "파이프라인 시작" 버튼 클릭
- [ ] 상태가 "실행 중"으로 전환됨 확인

#### 2-3. 실시간 영상 및 탐지 확인 ("실시간 모니터링" 탭)

- [ ] 실시간 프레임이 표시됨
- [ ] raw HLS 영상이 지연 없이 재생됨
- [ ] tracking overlay를 켠 경우, 허용된 tracking/detection 메타데이터가 영상 좌표와 일치함

#### 2-4. Observation 및 파일럿 사건 확인

- [ ] 정상 person 통행은 사건·알림을 만들지 않고 Observation 집계에만 반영됨
- [ ] 구성한 감지선 통과 또는 배회 조건이 충족되면 해당 사건이 이벤트 목록에 나타남
- [ ] Tracking 사건 상세에 track ID, zone, 방향 또는 체류시간이 표시됨
- [ ] 사건 metadata에는 전체 trajectory, zone 좌표, centroid가 없고 triggering detection bbox만 포함됨
- [ ] 얼굴 detector 또는 필수 암호화 키를 제거한 음성 테스트에서 AlertManager가 시작 실패함

#### 2-5. 알람 로그 확인 ("이벤트 로그" 탭)

- [ ] `logs/alerts.log`의 이벤트가 탭에 표시됨
- [ ] 이벤트 유형 필터 동작 확인

#### 2-6. 캡처 이미지 확인 ("갤러리" 탭)

- [ ] 저장된 캡처 이미지가 표시됨
- [ ] 얼굴 블러링이 적용된 이미지 확인
- [ ] (`encrypt_images: true` 시) `.enc` 파일이 복호화되어 표시됨

#### 2-7. 시스템 상태 확인 ("설정" 페이지)

- [ ] CPU/메모리/디스크 사용률 표시됨
- [ ] 활성 모델 목록이 `general_yolo`, `ppe_yolo`, `fire_smoke_yolo`로 표시됨
- [ ] 모델별 inference/error/latency 메트릭이 표시됨
- [ ] SMTP 테스트 버튼 클릭 시 성공 메시지 표시됨 (SMTP 설정 시)

#### 2-8. 모델 업로드 시연 (해당 시)

- [ ] "모델" 페이지에서 `.onnx` 파일 업로드
- [ ] smoke test 성공 후 `enabled: false` 상태로 등록됨 확인
- [ ] "활성화" 버튼 클릭 후 모델이 활성화됨 확인

#### 2-9. 이벤트 내보내기 시연

- [ ] "이벤트" 페이지에서 "CSV 내보내기" 클릭 후 파일 다운로드 확인
- [ ] "JSON 내보내기" 클릭 후 파일 다운로드 확인

### B3. COCO 모델 한계 설명 포인트

시연 중 반드시 안내해야 할 항목:

- 기본 YOLO26 모델은 COCO pretrained 모델입니다.
- 기본 모델만으로는 `helmet`, `vest`, `fire`, `smoke`, `fall` 클래스를 **직접 탐지하지 못합니다**.
- 웹 콘솔에 COCO 한계 경고 배지가 표시됩니다.
- 첫 release는 VLM 대신 승인된 PPE/화재·연기 safety 모델의 직접 결과를 후보로 기록합니다.
- 커스텀 모델 학습 방법: `docs/custom-model-training.md` 참조.

### B4. 시연 완료 후

- [ ] "파이프라인 중지" 클릭
- [ ] 불필요한 캡처 이미지 정리 (`data/captures/`)
- [ ] 시연용 테스트 데이터 정리

---

## 납품 완료 기준

아래 모든 항목이 통과되어야 납품 완료입니다.

| 항목 | 기준 |
|---|---|
| 자동 테스트 | 현재 Python/프론트 전체 suite 실패 없음, 모든 skip 사유 문서화 |
| E2E 스모크 테스트 | `ok: true`, `failed_models: {}` |
| 헬스체크 | `overall_healthy: true` |
| 웹 콘솔 기동 | Caddy HTTPS 접속·인증서 신뢰·로그인 성공, 내부 web/gateway 직접 노출 없음 |
| 실시간 탐지 | raw HLS 재생 및 별도 tracking overlay 좌표 일치 |
| VLM 분석 | enabled로 출고하려면 local runtime/model qualification 및 실패 시 `unverified` alert 증적이 필요; 미승인 기능은 출고 capability로 주장하지 않음 |
| 알람 로그 | `logs/alerts.log` 이벤트 기록 확인 |
| 캡처 이미지 | `data/captures/` 얼굴 블러 이미지 저장 확인 |

---

## 참고 문서

- [설정 레퍼런스](configuration.md)
- [운영 가이드](operations.md)
- [문제 해결 가이드](troubleshooting.md)
- [배포 가이드](deployment-guide.md)
- [오프라인 Appliance](offline-appliance.md)
- [카메라 설치 업자 가이드](installer-camera-onboarding.md)
- [아키텍처](architecture.md)
