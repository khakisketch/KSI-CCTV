#!/usr/bin/env python3
"""
KSI-CCTV 화재/연기 커스텀 모델 학습 스크립트
============================================
Fire / Smoke 2-class YOLO 모델을 학습한다.

하드웨어 전제:
  - DGX Spark (GB10, sm_121, CUDA 13 / cu130, ARM64)
  - PyTorch cu130 휠로 설치 (requirements.txt / scripts/install.sh 참조)

필수 조건 (이 스크립트는 아래 없이 실행되지 않는다):
  1. 화재/연기 데이터셋 — fire / smoke 라벨이 포함된
     YOLO 포맷 데이터셋 (images/ + labels/ + data.yaml)
  2. GPU — 학습 속도상 CUDA GPU 권장. CPU로도 실행은 되나 수시간 이상 소요.

사용 예:
  python3 scripts/train_fire_smoke.py \\
      --data /data/fire_smoke_dataset/data.yaml \\
      --model yolo11n.pt \\
      --epochs 80 \\
      --batch 16 \\
      --imgsz 640 \\
      --project runs/fire_smoke \\
      --name exp1

주의:
  --batch 는 반드시 양수 정수로 지정한다.
  절대로 -1(자동배치)을 사용하지 말 것 — DGX 통합메모리(UMA) OOM 발생.
"""

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KSI-CCTV 화재/연기 커스텀 YOLO 모델 학습 (fire/smoke)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # --- 필수 인자 ---
    parser.add_argument(
        "--data",
        required=True,
        type=Path,
        help="데이터셋 YAML 경로 (YOLO 포맷, e.g. /data/fire_smoke_dataset/data.yaml)",
    )

    # --- 모델 설정 ---
    parser.add_argument(
        "--model",
        default="yolo11n.pt",
        help="시작 모델 (Ultralytics YOLO .pt 파일 또는 'yolo11n.pt' 등 사전학습명). "
             "기본값: yolo11n.pt",
    )

    # --- 학습 하이퍼파라미터 ---
    # batch: DGX cu130 통합메모리(UMA) 환경에서 -1(자동) 사용 금지.
    # 기본값 16은 GPU VRAM 24GB 이상 환경 기준이다. VRAM이 부족하면 줄여서 사용.
    parser.add_argument(
        "--batch",
        type=int,
        default=16,
        help="배치 크기 (양수 정수 필수). -1 절대 금지 — DGX UMA OOM. 기본값: 16",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=80,
        help="학습 에폭 수. 기본값: 80",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="입력 이미지 크기 (정사각형 기준). 기본값: 640",
    )
    parser.add_argument(
        "--device",
        default="0",
        help="학습 디바이스 ('0', '0,1', 'cpu' 등). 기본값: '0' (첫 번째 GPU)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="데이터 로더 워커 수. 기본값: 4",
    )

    # --- 출력 설정 ---
    parser.add_argument(
        "--project",
        type=Path,
        default=Path("runs/train_fire_smoke"),
        help="결과 저장 루트 디렉터리. 기본값: runs/train_fire_smoke",
    )
    parser.add_argument(
        "--name",
        default="exp",
        help="실험 이름 (project/name/ 에 저장). 기본값: exp",
    )

    # --- 재개 옵션 ---
    parser.add_argument(
        "--resume",
        action="store_true",
        help="마지막 체크포인트에서 학습 재개",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=30,
        help="Early stopping patience. 기본값: 30",
    )

    # --- 화재/연기 특화 옵션 ---
    parser.add_argument(
        "--conf-threshold",
        type=float,
        default=0.25,
        help="학습 중 검증 conf 임계값. 기본값: 0.25",
    )

    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    """인자 유효성 검사. 문제 있으면 에러 메시지 출력 후 종료."""
    errors: list[str] = []

    # batch 양수 강제
    if args.batch <= 0:
        errors.append(
            f"[오류] --batch 는 반드시 양수여야 합니다 (입력값: {args.batch}).\n"
            "       DGX Spark 통합메모리 환경에서 -1(자동) 사용 시 OOM 발생.\n"
            "       권장: --batch 16 (VRAM 24GB+) 또는 --batch 8 (VRAM 부족 시)."
        )

    # 데이터셋 YAML 존재 여부
    if not args.resume and not args.data.exists():
        errors.append(
            f"[오류] 데이터셋 YAML 파일이 없습니다: {args.data}\n"
            "       화재/연기 데이터셋(fire/smoke 라벨)을 준비한 뒤 --data 경로를 지정하세요.\n"
            "       공개 데이터셋 예시:\n"
            "         - D-Fire Dataset: https://github.com/gaiasd/DFireDataset\n"
            "         - Roboflow Universe: Fire and Smoke Detection\n"
            "           https://universe.roboflow.com/school-ol4ea/fire-and-smoke-detection-v2\n"
            "         - FASDD: https://github.com/AIConnectomics/FASDD\n"
            "       다운로드 후 YOLO 포맷(YOLOv8)으로 export 하여 사용하세요."
        )

    if errors:
        print("\n".join(errors), file=sys.stderr)
        sys.exit(1)


def _check_dependencies() -> None:
    """필수 패키지 확인."""
    missing: list[str] = []
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        missing.append("ultralytics (pip install ultralytics)")

    if missing:
        print(
            "[오류] 필수 패키지가 설치되지 않았습니다:\n"
            + "\n".join(f"  - {m}" for m in missing),
            file=sys.stderr,
        )
        sys.exit(1)


def _check_gpu(device: str) -> None:
    """GPU 가용성 안내 (경고만, 종료 안 함)."""
    if device.lower() == "cpu":
        print(
            "[경고] CPU 모드로 학습합니다. 80 에폭 기준 수시간~수십 시간 소요될 수 있습니다.",
            file=sys.stderr,
        )
        return

    try:
        import torch
        if not torch.cuda.is_available():
            print(
                "[경고] CUDA GPU를 찾을 수 없습니다. CPU로 대체됩니다.\n"
                "       DGX cu130 환경이라면 PyTorch cu130 휠 설치 여부를 확인하세요:\n"
                "         pip install torch --index-url https://download.pytorch.org/whl/cu130",
                file=sys.stderr,
            )
    except ImportError:
        pass


def main() -> None:
    args = parse_args()
    _validate_args(args)
    _check_dependencies()

    # 의존성 import는 유효성 검사 통과 후에만 수행
    from ultralytics import YOLO  # noqa: PLC0415

    print("=" * 60)
    print("KSI-CCTV 화재/연기 커스텀 모델 학습")
    print("=" * 60)
    print(f"  데이터셋  : {args.data}")
    print(f"  기반 모델 : {args.model}")
    print(f"  에폭      : {args.epochs}")
    print(f"  배치      : {args.batch}  ※ -1(자동) 금지 — DGX UMA OOM")
    print(f"  이미지 크기: {args.imgsz}")
    print(f"  디바이스  : {args.device}")
    print(f"  저장 경로 : {args.project}/{args.name}")
    print("=" * 60)

    _check_gpu(args.device)

    # 모델 로드
    model = YOLO(str(args.model))

    # 학습 실행
    # batch 는 반드시 args.batch (양수 정수) — -1 절대 사용 금지 (DGX UMA OOM)
    results = model.train(
        data=str(args.data),
        epochs=args.epochs,
        batch=args.batch,           # ← -1 금지: DGX Spark UMA OOM 발생
        imgsz=args.imgsz,
        device=args.device,
        workers=args.workers,
        project=str(args.project),
        name=args.name,
        resume=args.resume,
        patience=args.patience,
        # 화재/연기 2-class 권장 하이퍼파라미터
        optimizer="AdamW",
        lr0=0.001,
        lrf=0.01,
        warmup_epochs=3,
        # 화재·연기는 색상이 핵심 특징 — HSV 증강 강화
        hsv_h=0.015,
        hsv_s=0.7,
        hsv_v=0.4,
        # 좌우 반전은 화재/연기에도 유효
        fliplr=0.5,
        # 야간·흐린 날씨 시나리오 대응
        degrees=10.0,
        translate=0.1,
        scale=0.5,
    )

    print("\n[완료] 학습이 종료되었습니다.")
    print(f"  결과 저장 위치: {results.save_dir}")
    print(f"  최고 모델:      {results.save_dir}/weights/best.pt")
    print("\n다음 단계:")
    print("  1. ONNX 변환: python3 scripts/export_to_onnx.py \\")
    print(f"       --weights {results.save_dir}/weights/best.pt \\")
    print("       --imgsz 640")
    print("  2. 변환된 .onnx 파일을 models/ 에 복사")
    print("  3. config/model_profiles.yaml 에 fire_smoke_yolo 항목 갱신")
    print("  4. config/event_policies.yaml 에서 fire/smoke 정책 enabled: true 확인")


if __name__ == "__main__":
    main()
