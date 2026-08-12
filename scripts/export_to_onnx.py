#!/usr/bin/env python3
"""
KSI-CCTV YOLO → ONNX 변환 스크립트
=====================================
Ultralytics YOLO .pt 파일을 KSI-CCTV가 사용하는 ONNX 포맷으로 변환한다.

하드웨어 전제:
  - DGX Spark (GB10, sm_121, CUDA 13 / cu130, ARM64)
  - PyTorch cu130 휠로 설치 (requirements.txt / scripts/install.sh 참조)
  - ONNX 추론 시 KSI-CCTV는 onnxruntime (CPU 기본, DGX에서는 onnxruntime-gpu로 교체)를 사용

사용 예:
  # PPE 모델 변환
  python3 scripts/export_to_onnx.py \\
      --weights runs/train_ppe/exp/weights/best.pt \\
      --output models/yolo26_ppe_custom.onnx \\
      --imgsz 640

  # 화재/연기 모델 변환
  python3 scripts/export_to_onnx.py \\
      --weights runs/train_fire_smoke/exp/weights/best.pt \\
      --output models/yolo26_fire_smoke_custom.onnx \\
      --imgsz 640

  # FP16(반정밀) — DGX GPU 추론 속도 향상, onnxruntime-gpu 필요
  python3 scripts/export_to_onnx.py \\
      --weights best.pt \\
      --output models/model_fp16.onnx \\
      --imgsz 640 \\
      --half

변환 후:
  1. 출력 .onnx 파일을 models/ 디렉터리에 복사
  2. config/model_profiles.yaml 에 새 모델 항목 추가
     (model_path, class_profile, class_map 등 설정)
  3. 기존 모델과 교체 시 config/model_profiles.yaml model_path 수정
"""

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YOLO .pt → ONNX 변환 (KSI-CCTV 배포용)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # --- 필수 인자 ---
    parser.add_argument(
        "--weights",
        required=True,
        type=Path,
        help="변환할 YOLO .pt 파일 경로 (e.g. runs/train_ppe/exp/weights/best.pt)",
    )

    # --- 출력 ---
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="출력 ONNX 파일 경로. 미지정 시 --weights 와 같은 디렉터리에 .onnx 확장자로 저장.",
    )

    # --- 변환 옵션 ---
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="입력 이미지 크기 (정사각형). KSI-CCTV 표준: 640. 기본값: 640",
    )
    parser.add_argument(
        "--half",
        action="store_true",
        help="FP16 반정밀 export. DGX GPU 추론 속도 향상, onnxruntime-gpu 필요. "
             "CPU 전용 환경에서는 사용하지 말 것.",
    )
    parser.add_argument(
        "--dynamic",
        action="store_true",
        help="동적 배치 크기 활성화. KSI-CCTV는 고정 입력을 권장하므로 기본 비활성.",
    )
    parser.add_argument(
        "--simplify",
        action="store_true",
        default=True,
        help="onnx-simplifier로 그래프 단순화 (기본 활성). onnxsim 패키지 필요.",
    )
    parser.add_argument(
        "--no-simplify",
        dest="simplify",
        action="store_false",
        help="그래프 단순화 비활성화.",
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=18,
        help="ONNX opset 버전. qualified onnxruntime 1.28+ 는 opset 18 지원. 기본값: 18",
    )
    parser.add_argument(
        "--device",
        default="0",
        help="변환 디바이스 ('0' 또는 'cpu'). GPU가 없으면 cpu 자동 사용. 기본값: '0'",
    )

    # --- 검증 옵션 ---
    parser.add_argument(
        "--verify",
        action="store_true",
        default=True,
        help="변환 후 ONNX 모델 그래프 유효성 검사 (기본 활성).",
    )
    parser.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="변환 후 ONNX 검증 비활성화.",
    )

    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    """인자 유효성 검사."""
    errors: list[str] = []

    if not args.weights.exists():
        errors.append(
            f"[오류] 가중치 파일이 없습니다: {args.weights}\n"
            "       train_ppe.py 또는 train_fire_smoke.py 로 학습 후 생성된 best.pt 경로를 지정하세요."
        )

    if args.weights.suffix.lower() not in (".pt", ".pth"):
        errors.append(
            f"[오류] Ultralytics YOLO .pt / .pth 파일이 필요합니다 (입력: {args.weights.name})."
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

    # 권장 패키지 안내 (없어도 진행)
    optional_missing: list[str] = []
    try:
        import onnx  # noqa: F401
    except ImportError:
        optional_missing.append("onnx (pip install onnx) — 변환 검증 필요")
    try:
        import onnxsim  # noqa: F401
    except ImportError:
        optional_missing.append("onnxsim (pip install onnxsim) — 그래프 단순화용 (--simplify)")

    if optional_missing:
        print(
            "[안내] 선택 패키지가 없습니다 (없어도 기본 변환은 진행됩니다):\n"
            + "\n".join(f"  - {m}" for m in optional_missing)
        )


def _resolve_output(args: argparse.Namespace) -> Path:
    """출력 경로 결정."""
    if args.output is not None:
        return args.output
    # 미지정 시 weights 파일과 같은 디렉터리에 .onnx 확장자
    return args.weights.with_suffix(".onnx")


def _verify_onnx(onnx_path: Path) -> None:
    """ONNX 그래프 유효성 검사."""
    try:
        import onnx  # noqa: PLC0415
        model = onnx.load(str(onnx_path))
        onnx.checker.check_model(model)
        print(f"[검증 통과] ONNX 그래프가 유효합니다: {onnx_path}")
        # 클래스 메타데이터 출력 (KSI-CCTV model_profiles.yaml 작성 참고용)
        meta = {p.key: p.value for p in model.metadata_props}
        if "names" in meta:
            print(f"  클래스 메타데이터 (model_profiles.yaml class_map 참조): {meta['names']}")
        elif "names" in str(meta):
            print(f"  메타데이터 전체: {meta}")
    except ImportError:
        print("[안내] onnx 패키지가 없어 검증을 건너뜁니다. (pip install onnx)")
    except Exception as e:
        print(f"[경고] ONNX 검증 중 오류: {e}", file=sys.stderr)


def main() -> None:
    args = parse_args()
    _validate_args(args)
    _check_dependencies()

    output_path = _resolve_output(args)

    # 출력 디렉터리 생성
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 의존성 import는 유효성 검사 통과 후에만 수행
    from ultralytics import YOLO  # noqa: PLC0415

    print("=" * 60)
    print("KSI-CCTV YOLO → ONNX 변환")
    print("=" * 60)
    print(f"  입력 가중치 : {args.weights}")
    print(f"  출력 경로   : {output_path}")
    print(f"  이미지 크기 : {args.imgsz}")
    print(f"  FP16 반정밀 : {'활성' if args.half else '비활성'}")
    print(f"  동적 배치   : {'활성' if args.dynamic else '비활성 (고정 입력)'}")
    print(f"  그래프 단순화: {'활성' if args.simplify else '비활성'}")
    print(f"  ONNX opset  : {args.opset}")
    print("=" * 60)

    # 모델 로드
    model = YOLO(str(args.weights))

    # ONNX export
    # KSI-CCTV 표준: imgsz=640, opset=18 (PyTorch 2.13 exporter compatible)
    # batch 미지정 → 기본 1 (단일 프레임 추론, 고정 입력)
    exported = model.export(
        format="onnx",
        imgsz=args.imgsz,
        half=args.half,
        dynamic=args.dynamic,
        simplify=args.simplify,
        opset=args.opset,
        device=args.device,
    )

    # Ultralytics가 반환하는 경로가 output_path 와 다를 수 있으므로 이동
    exported_path = Path(str(exported))
    if exported_path.resolve() != output_path.resolve():
        import shutil
        shutil.move(str(exported_path), str(output_path))
        print(f"[이동] {exported_path} → {output_path}")

    print(f"\n[변환 완료] {output_path}")
    print(f"  파일 크기: {output_path.stat().st_size / 1_048_576:.1f} MB")

    # 검증
    if args.verify:
        _verify_onnx(output_path)

    print("\n다음 단계:")
    print(f"  1. 파일 복사: cp {output_path} models/")
    print("  2. config/model_profiles.yaml 수정:")
    print("       model_path: ./models/<파일명>.onnx")
    print("       class_profile: safety_custom  # 또는 custom")
    print("       class_map:  # 위 클래스 메타데이터 참조")
    print("  3. 파이프라인 재기동 후 MultiModelRegistry 로그에서 클래스 자동 인식 확인")


if __name__ == "__main__":
    main()
