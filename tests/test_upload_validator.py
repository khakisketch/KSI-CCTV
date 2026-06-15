"""User model upload validator tests."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import tempfile
import unittest
from unittest.mock import patch

import yaml

from src.models import upload_validator as uv


class TestUploadValidator(unittest.TestCase):
    def test_rejects_non_onnx_extensions_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            result = uv.upload_model(
                b"not a model",
                "malicious.py",
                upload_base_dir=base,
                model_profiles_path=Path(tmp) / "model_profiles.yaml",
            )

            self.assertFalse(result.success)
            self.assertIn("ONNX", result.error)
            self.assertFalse(base.exists())

    def test_rejects_oversized_upload_before_runtime_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(uv, "validate_onnx_load") as load_mock:
                result = uv.upload_model(
                    b"12345",
                    "model.onnx",
                    max_file_size=4,
                    upload_base_dir=Path(tmp) / "uploaded",
                    model_profiles_path=Path(tmp) / "model_profiles.yaml",
                )

            self.assertFalse(result.success)
            self.assertIn("파일 크기", result.error)
            load_mock.assert_not_called()

    def test_successful_upload_is_isolated_and_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            profiles_path = Path(tmp) / "model_profiles.yaml"
            profiles_path.write_text("models: {}\n", encoding="utf-8")

            with patch.object(uv, "validate_onnx_load", return_value=(True, "")), patch.object(
                uv, "validate_onnx_shape_metadata", return_value=(True, "")
            ), patch.object(uv, "validate_onnx_inference", return_value=(True, "")):
                result = uv.upload_model(
                    b"fake onnx bytes",
                    "../dangerous.onnx",
                    metadata={"model_name": "helmet model", "classes": ["person", "helmet"], "input_size": [320, 320]},
                    upload_base_dir=base,
                    model_profiles_path=profiles_path,
                )

            self.assertTrue(result.success, result.error)
            model_dir = base / result.model_id
            self.assertTrue((model_dir / "model.onnx").exists())
            self.assertTrue((model_dir / "metadata.yaml").exists())

            metadata = yaml.safe_load((model_dir / "metadata.yaml").read_text(encoding="utf-8"))
            self.assertEqual(metadata["original_filename"], "dangerous.onnx")
            self.assertFalse(metadata["enabled"])

            profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
            profile = profiles["models"][result.profile_name]
            self.assertFalse(profile["enabled"])
            self.assertEqual(profile["source"], "uploaded")
            self.assertTrue(profile["model_path"].endswith(f"{result.model_id}/model.onnx"))

    def test_invalid_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = uv.upload_model(
                b"fake",
                "model.onnx",
                metadata={"classes": "person,helmet"},
                upload_base_dir=Path(tmp) / "uploaded",
                model_profiles_path=Path(tmp) / "model_profiles.yaml",
            )

            self.assertFalse(result.success)
            self.assertIn("classes", result.error)

    def test_shape_metadata_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(uv, "validate_onnx_load", return_value=(True, "")), patch.object(
                uv, "validate_onnx_shape_metadata", return_value=(False, "shape mismatch")
            ), patch.object(uv, "validate_onnx_inference") as inference_mock:
                result = uv.upload_model(
                    b"fake",
                    "model.onnx",
                    upload_base_dir=Path(tmp) / "uploaded",
                    model_profiles_path=Path(tmp) / "model_profiles.yaml",
                )

            self.assertFalse(result.success)
            self.assertEqual(result.error, "shape mismatch")
            inference_mock.assert_not_called()

    def test_activate_deactivate_and_delete_uploaded_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            profiles_path = Path(tmp) / "model_profiles.yaml"
            profiles_path.write_text("models: {}\n", encoding="utf-8")

            with patch.object(uv, "validate_onnx_load", return_value=(True, "")), patch.object(
                uv, "validate_onnx_shape_metadata", return_value=(True, "")
            ), patch.object(uv, "validate_onnx_inference", return_value=(True, "")):
                result = uv.upload_model(
                    b"fake",
                    "model.onnx",
                    upload_base_dir=base,
                    model_profiles_path=profiles_path,
                )

            ok, err = uv.activate_uploaded_model(result.model_id, profiles_path, base)
            self.assertTrue(ok, err)
            profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
            self.assertTrue(profiles["models"][result.profile_name]["enabled"])

            ok, err = uv.deactivate_uploaded_model(result.model_id, profiles_path, base)
            self.assertTrue(ok, err)
            profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
            self.assertFalse(profiles["models"][result.profile_name]["enabled"])

            ok, err = uv.delete_uploaded_model(result.model_id, upload_base_dir=base, model_profiles_path=profiles_path)
            self.assertTrue(ok, err)
            self.assertFalse((base / result.model_id).exists())
            profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
            self.assertNotIn(result.profile_name, profiles.get("models", {}))


class TestValidateMetadataThresholds(unittest.TestCase):
    """_validate_metadata의 conf/iou threshold 검증 보강 테스트.

    브리프 Finding: float() 무조건 변환으로 인한 영문 raw ValueError 전파 방지.
    비수치 문자열이 들어오면 한국어 에러 메시지를 ValueError로 반환해야 한다.
    """

    def _call(self, meta: dict) -> dict:
        return uv._validate_metadata(meta)

    # --- conf_threshold 정상 케이스 ---
    def test_conf_threshold_default_applied(self):
        """conf_threshold 미지정 시 기본값 0.25 적용."""
        result = self._call({"classes": ["a"], "input_size": [640, 640]})
        self.assertAlmostEqual(result["conf_threshold"], 0.25)

    def test_conf_threshold_int_accepted(self):
        """conf_threshold에 정수 0 허용(경계값)."""
        result = self._call({"classes": ["a"], "input_size": [640, 640], "conf_threshold": 0})
        self.assertAlmostEqual(result["conf_threshold"], 0.0)

    def test_conf_threshold_string_numeric_accepted(self):
        """conf_threshold에 수치 문자열 '0.5' 허용 — float 변환 성공."""
        result = self._call({"classes": ["a"], "input_size": [640, 640], "conf_threshold": "0.5"})
        self.assertAlmostEqual(result["conf_threshold"], 0.5)

    # --- conf_threshold 오류 케이스 ---
    def test_conf_threshold_non_numeric_string_raises_korean_error(self):
        """비수치 문자열 conf_threshold → 한국어 에러 메시지, 영문 raw ValueError 아님.

        재현 경로: _validate_metadata({'classes':['a'],'conf_threshold':'abc'}) → CRASH.
        """
        with self.assertRaises(ValueError) as ctx:
            self._call({"classes": ["a"], "input_size": [640, 640], "conf_threshold": "abc"})
        error_msg = str(ctx.exception)
        self.assertIn("conf_threshold", error_msg)
        # 영문 raw Python 메시지("could not convert string to float")가 아닌지 확인
        self.assertNotIn("could not convert string to float", error_msg)
        # 한국어 안내 포함 확인
        self.assertIn("숫자", error_msg)

    def test_conf_threshold_out_of_range_raises_korean_error(self):
        """conf_threshold 범위 초과(1.5) → 한국어 에러 메시지."""
        with self.assertRaises(ValueError) as ctx:
            self._call({"classes": ["a"], "input_size": [640, 640], "conf_threshold": 1.5})
        error_msg = str(ctx.exception)
        self.assertIn("conf_threshold", error_msg)
        self.assertIn("사이", error_msg)

    def test_conf_threshold_negative_raises_korean_error(self):
        """conf_threshold 음수(-0.1) → 한국어 에러 메시지."""
        with self.assertRaises(ValueError) as ctx:
            self._call({"classes": ["a"], "input_size": [640, 640], "conf_threshold": -0.1})
        self.assertIn("conf_threshold", str(ctx.exception))

    def test_conf_threshold_bool_raises_error(self):
        """conf_threshold에 bool 값 전달 → 에러(int subclass인 bool 오용 방지)."""
        with self.assertRaises(ValueError):
            self._call({"classes": ["a"], "input_size": [640, 640], "conf_threshold": True})

    def test_conf_threshold_list_raises_error(self):
        """conf_threshold에 list 전달 → 에러."""
        with self.assertRaises(ValueError) as ctx:
            self._call({"classes": ["a"], "input_size": [640, 640], "conf_threshold": [0.5]})
        self.assertIn("conf_threshold", str(ctx.exception))

    # --- iou_threshold 정상 케이스 ---
    def test_iou_threshold_default_applied(self):
        """iou_threshold 미지정 시 기본값 0.45 적용."""
        result = self._call({"classes": ["a"], "input_size": [640, 640]})
        self.assertAlmostEqual(result["iou_threshold"], 0.45)

    def test_iou_threshold_string_numeric_accepted(self):
        """iou_threshold에 수치 문자열 '0.45' 허용."""
        result = self._call({"classes": ["a"], "input_size": [640, 640], "iou_threshold": "0.45"})
        self.assertAlmostEqual(result["iou_threshold"], 0.45)

    # --- iou_threshold 오류 케이스 ---
    def test_iou_threshold_non_numeric_string_raises_korean_error(self):
        """비수치 문자열 iou_threshold → 한국어 에러 메시지, 영문 raw ValueError 아님."""
        with self.assertRaises(ValueError) as ctx:
            self._call({"classes": ["a"], "input_size": [640, 640], "iou_threshold": "xyz"})
        error_msg = str(ctx.exception)
        self.assertIn("iou_threshold", error_msg)
        self.assertNotIn("could not convert string to float", error_msg)
        self.assertIn("숫자", error_msg)

    def test_iou_threshold_out_of_range_raises_korean_error(self):
        """iou_threshold 범위 초과(2.0) → 한국어 에러 메시지."""
        with self.assertRaises(ValueError) as ctx:
            self._call({"classes": ["a"], "input_size": [640, 640], "iou_threshold": 2.0})
        self.assertIn("iou_threshold", str(ctx.exception))

    def test_iou_threshold_bool_raises_error(self):
        """iou_threshold에 bool 값 전달 → 에러."""
        with self.assertRaises(ValueError):
            self._call({"classes": ["a"], "input_size": [640, 640], "iou_threshold": False})

    # --- upload_model 통합: ValueError가 한국어 메시지로 전달되는지 확인 ---
    def test_upload_model_conf_non_numeric_returns_korean_error(self):
        """upload_model에 비수치 conf_threshold → UploadResult.error가 한국어 메시지."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            result = uv.upload_model(
                b"fake",
                "model.onnx",
                metadata={"classes": ["a"], "conf_threshold": "not_a_number"},
                upload_base_dir=uv.Path(tmp) / "uploaded",
                model_profiles_path=uv.Path(tmp) / "model_profiles.yaml",
            )
        self.assertFalse(result.success)
        self.assertIn("conf_threshold", result.error)
        self.assertNotIn("could not convert string to float", result.error)
        self.assertIn("숫자", result.error)

    def test_upload_model_iou_non_numeric_returns_korean_error(self):
        """upload_model에 비수치 iou_threshold → UploadResult.error가 한국어 메시지."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            result = uv.upload_model(
                b"fake",
                "model.onnx",
                metadata={"classes": ["a"], "iou_threshold": "bad_value"},
                upload_base_dir=uv.Path(tmp) / "uploaded",
                model_profiles_path=uv.Path(tmp) / "model_profiles.yaml",
            )
        self.assertFalse(result.success)
        self.assertIn("iou_threshold", result.error)
        self.assertNotIn("could not convert string to float", result.error)


def _make_minimal_onnx_bytes(h: int = 320, w: int = 320) -> bytes:
    """실제 ONNX Runtime이 로드·추론 가능한 최소 ONNX 모델을 생성해 반환한다.

    NCHW float32 Identity 모델 (배치=1, 채널=3, H, W).
    onnx 패키지가 없으면 ImportError — 테스트를 skip해야 한다.
    """
    from onnx import TensorProto, helper

    input_info = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, h, w])
    output_info = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 3, h, w])
    node = helper.make_node("Identity", inputs=["input"], outputs=["output"])
    graph = helper.make_graph([node], "test_graph", [input_info], [output_info])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 11)])
    model.ir_version = 7
    return model.SerializeToString()


def _make_dynamic_onnx_bytes() -> bytes:
    """배치·H·W가 동적(symbolic)인 ONNX 모델 바이트 반환."""
    from onnx import TensorProto, helper

    # 동적 dim: None(=symbolic)으로 표현
    input_info = helper.make_tensor_value_info("input", TensorProto.FLOAT, [None, 3, None, None])
    output_info = helper.make_tensor_value_info("output", TensorProto.FLOAT, [None, 3, None, None])
    node = helper.make_node("Identity", inputs=["input"], outputs=["output"])
    graph = helper.make_graph([node], "dyn_graph", [input_info], [output_info])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 11)])
    model.ir_version = 7
    return model.SerializeToString()


try:
    import onnx as _onnx_check  # noqa: F401
    _HAS_ONNX_BUILDER = True
except ImportError:
    _HAS_ONNX_BUILDER = False

# ─────────────────────────────────────────────────────────────────────────────
# 실제 ONNX 파일 기반 종단(end-to-end) 상태 전이 테스트
# (미리 status 주입 없이 upload→activate→deactivate→delete 실제 흐름 검증)
# ─────────────────────────────────────────────────────────────────────────────


@unittest.skipUnless(_HAS_ONNX_BUILDER, "onnx 패키지 없음 — skip")
class TestEndToEndRealOnnx(unittest.TestCase):
    """실제 ONNX 파일을 사용한 종단 상태 전이 테스트.

    mock 없이 validate_onnx_load / validate_onnx_shape_metadata /
    validate_onnx_inference 를 실제 실행한다.
    """

    def _upload_real(self, base: "Path", profiles_path: "Path", h: int = 320, w: int = 320):
        """실제 ONNX 바이트로 upload_model 호출 후 UploadResult 반환."""
        onnx_bytes = _make_minimal_onnx_bytes(h, w)
        return uv.upload_model(
            onnx_bytes,
            "test_model.onnx",
            metadata={
                "model_name": "테스트 모델",
                "classes": ["person", "helmet", "vest"],
                "input_size": [h, w],
            },
            upload_base_dir=base,
            model_profiles_path=profiles_path,
        )

    def test_upload_validates_real_onnx_and_registers_disabled(self):
        """실제 ONNX를 업로드하면 검증 통과 후 disabled로 등록된다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            profiles_path = Path(tmp) / "model_profiles.yaml"
            profiles_path.write_text("models: {}\n", encoding="utf-8")

            result = self._upload_real(base, profiles_path)

            self.assertTrue(result.success, f"업로드 실패: {result.error}")
            # 파일 격리 확인
            model_file = base / result.model_id / "model.onnx"
            self.assertTrue(model_file.exists(), "model.onnx가 생성되지 않음")
            # 메타데이터 파일 확인
            meta_file = base / result.model_id / "metadata.yaml"
            self.assertTrue(meta_file.exists(), "metadata.yaml이 생성되지 않음")
            # disabled 상태 확인 — status 미리 주입 없이 파일 직접 검증
            meta = yaml.safe_load(meta_file.read_text(encoding="utf-8"))
            self.assertFalse(meta["enabled"], "업로드 직후 enabled=False여야 함")
            # model_profiles.yaml 반영 확인
            profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
            profile = profiles["models"][result.profile_name]
            self.assertFalse(profile["enabled"])
            self.assertEqual(profile["source"], "uploaded")

    def test_full_lifecycle_upload_activate_deactivate_delete(self):
        """실제 ONNX로 upload→activate→deactivate→delete 상태 전이 전 구간 검증.

        각 단계에서 실제 파일/config 상태를 확인한다 (status 직접 주입 없음).
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            profiles_path = Path(tmp) / "model_profiles.yaml"
            profiles_path.write_text("models: {}\n", encoding="utf-8")

            # 1. 업로드
            result = self._upload_real(base, profiles_path)
            self.assertTrue(result.success, f"업로드 실패: {result.error}")
            model_id = result.model_id
            profile_name = result.profile_name

            # 2. 활성화
            ok, err = uv.activate_uploaded_model(model_id, profiles_path, base)
            self.assertTrue(ok, f"활성화 실패: {err}")
            # config/registry 반영 확인
            profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
            self.assertTrue(profiles["models"][profile_name]["enabled"], "활성화 후 enabled=True 아님")
            # 메타데이터 파일 동기화 확인
            meta = yaml.safe_load((base / model_id / "metadata.yaml").read_text(encoding="utf-8"))
            self.assertTrue(meta["enabled"], "메타데이터 파일 enabled=True 미반영")

            # 3. 비활성화
            ok, err = uv.deactivate_uploaded_model(model_id, profiles_path, base)
            self.assertTrue(ok, f"비활성화 실패: {err}")
            profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
            self.assertFalse(profiles["models"][profile_name]["enabled"], "비활성화 후 enabled=False 아님")
            meta = yaml.safe_load((base / model_id / "metadata.yaml").read_text(encoding="utf-8"))
            self.assertFalse(meta["enabled"], "메타데이터 파일 enabled=False 미반영")

            # 4. 삭제
            ok, err = uv.delete_uploaded_model(model_id, upload_base_dir=base, model_profiles_path=profiles_path)
            self.assertTrue(ok, f"삭제 실패: {err}")
            # 파일 정리 확인
            self.assertFalse((base / model_id).exists(), "삭제 후 모델 디렉토리 잔존")
            # config 정리 확인
            profiles = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
            self.assertNotIn(profile_name, profiles.get("models", {}), "삭제 후 profile 잔존")

    def test_invalid_onnx_bytes_are_rejected(self):
        """손상된 바이트를 업로드하면 validate_onnx_load 단계에서 실패한다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            profiles_path = Path(tmp) / "model_profiles.yaml"
            profiles_path.write_text("models: {}\n", encoding="utf-8")

            result = uv.upload_model(
                b"not a valid onnx binary content XYZ",
                "bad.onnx",
                metadata={"classes": ["a"], "input_size": [320, 320]},
                upload_base_dir=base,
                model_profiles_path=profiles_path,
            )

            self.assertFalse(result.success)
            self.assertIn("ONNX", result.error, f"에러에 'ONNX' 미포함: {result.error}")
            # 업로드 실패 시 파일이 남지 않아야 함
            if base.exists():
                model_dirs = [d for d in base.iterdir() if d.is_dir() and d.name != "tmp"]
                self.assertEqual(len(model_dirs), 0, "실패 후 모델 디렉토리 잔존")

    def test_shape_mismatch_real_onnx_rejected(self):
        """ONNX 모델 실제 shape(320x320)이 metadata(640x640)와 다르면 거부된다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            profiles_path = Path(tmp) / "model_profiles.yaml"
            profiles_path.write_text("models: {}\n", encoding="utf-8")

            # 모델은 320x320이지만 metadata에는 640x640으로 선언
            onnx_bytes = _make_minimal_onnx_bytes(h=320, w=320)
            result = uv.upload_model(
                onnx_bytes,
                "model.onnx",
                metadata={"classes": ["person"], "input_size": [640, 640]},
                upload_base_dir=base,
                model_profiles_path=profiles_path,
            )

            self.assertFalse(result.success)
            self.assertIn("input_size", result.error, f"shape 불일치 에러 미포함: {result.error}")

    def test_dynamic_shape_onnx_passes_shape_check(self):
        """동적 shape ONNX 모델은 shape 불일치 검사를 통과해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            profiles_path = Path(tmp) / "model_profiles.yaml"
            profiles_path.write_text("models: {}\n", encoding="utf-8")

            onnx_bytes = _make_dynamic_onnx_bytes()
            result = uv.upload_model(
                onnx_bytes,
                "dynamic_model.onnx",
                metadata={"classes": ["person"], "input_size": [640, 640]},
                upload_base_dir=base,
                model_profiles_path=profiles_path,
            )

            self.assertTrue(result.success, f"동적 shape 모델 업로드 실패: {result.error}")

    def test_get_uploaded_models_returns_registered_entries(self):
        """get_uploaded_models는 업로드된 모델 목록을 반환한다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            profiles_path = Path(tmp) / "model_profiles.yaml"
            profiles_path.write_text("models: {}\n", encoding="utf-8")

            # 초기 빈 상태
            self.assertEqual(uv.get_uploaded_models(base), [])

            # 2개 업로드
            result1 = self._upload_real(base, profiles_path)
            result2 = self._upload_real(base, profiles_path)
            self.assertTrue(result1.success)
            self.assertTrue(result2.success)

            models = uv.get_uploaded_models(base)
            self.assertEqual(len(models), 2)
            ids = {m["model_id"] for m in models}
            self.assertIn(result1.model_id, ids)
            self.assertIn(result2.model_id, ids)

    def test_activate_nonexistent_model_returns_error(self):
        """존재하지 않는 model_id 활성화 시 에러 반환 (crash 없음)."""
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "model_profiles.yaml"
            profiles_path.write_text("models: {}\n", encoding="utf-8")
            base = Path(tmp) / "uploaded"

            ok, err = uv.activate_uploaded_model("nonexistent_id", profiles_path, base)
            self.assertFalse(ok)
            self.assertIn("찾을 수 없", err, f"한국어 에러 미포함: {err}")

    def test_delete_nonexistent_model_returns_success(self):
        """존재하지 않는 model_id 삭제 시 에러 없이 성공 반환 (멱등성)."""
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "model_profiles.yaml"
            profiles_path.write_text("models: {}\n", encoding="utf-8")
            base = Path(tmp) / "uploaded"

            ok, err = uv.delete_uploaded_model(
                "nonexistent_id",
                upload_base_dir=base,
                model_profiles_path=profiles_path,
            )
            # 존재하지 않는 경우: 파일도 없고, config에도 없음 → 에러 없이 True
            self.assertTrue(ok, f"멱등 삭제 실패: {err}")


# ─────────────────────────────────────────────────────────────────────────────
# 단위 레벨: 이전에 미커버된 경로 보강
# ─────────────────────────────────────────────────────────────────────────────


class TestSanitizeFilename(unittest.TestCase):
    def test_valid_onnx_returns_model_onnx(self):
        self.assertEqual(uv.sanitize_filename("my_model.onnx"), "model.onnx")

    def test_invalid_extension_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            uv.sanitize_filename("malware.exe")
        self.assertIn("ONNX", str(ctx.exception))


class TestValidateMetadataEdgeCases(unittest.TestCase):
    def test_classes_none_treated_as_empty_list(self):
        """classes=None이면 빈 리스트로 처리한다."""
        # classes=None은 빈 리스트로 정규화되어야 하므로 에러 없이 통과
        result = uv._validate_metadata({"classes": None, "input_size": [640, 640]})
        self.assertEqual(result["classes"], [])

    def test_classes_with_empty_string_item_raises(self):
        """클래스 리스트에 빈 문자열이 포함되면 ValueError."""
        with self.assertRaises(ValueError):
            uv._validate_metadata({"classes": ["person", ""], "input_size": [640, 640]})

    def test_classes_with_non_string_item_raises(self):
        """클래스 리스트에 정수가 포함되면 ValueError."""
        with self.assertRaises(ValueError):
            uv._validate_metadata({"classes": ["person", 123], "input_size": [640, 640]})

    def test_input_size_wrong_length_raises(self):
        """input_size가 2-element 리스트가 아니면 ValueError (line 82)."""
        with self.assertRaises(ValueError) as ctx:
            uv._validate_metadata({"classes": [], "input_size": [640]})
        self.assertIn("input_size", str(ctx.exception))

    def test_input_size_negative_raises(self):
        """input_size에 음수 값이 있으면 ValueError."""
        with self.assertRaises(ValueError):
            uv._validate_metadata({"classes": [], "input_size": [640, -1]})

    def test_input_size_not_list_raises(self):
        """input_size가 리스트가 아니면 ValueError."""
        with self.assertRaises(ValueError):
            uv._validate_metadata({"classes": [], "input_size": "640x640"})


class TestValidateFileSizePath(unittest.TestCase):
    def test_file_size_ok_for_existing_file(self):
        with tempfile.NamedTemporaryFile(suffix=".onnx") as f:
            f.write(b"x" * 100)
            f.flush()
            self.assertTrue(uv.validate_file_size(Path(f.name)))

    def test_file_size_false_for_nonexistent(self):
        self.assertFalse(uv.validate_file_size(Path("/nonexistent/path/model.onnx")))


class TestResolveDim(unittest.TestCase):
    def test_static_positive_dim_returned_as_is(self):
        self.assertEqual(uv._resolve_dim(640, 2), 640)

    def test_batch_index_returns_1(self):
        self.assertEqual(uv._resolve_dim("batch", 0), 1)

    def test_hw_index_returns_640(self):
        self.assertEqual(uv._resolve_dim(None, 2), 640)
        self.assertEqual(uv._resolve_dim(None, 3), 640)

    def test_channel_index_returns_3(self):
        self.assertEqual(uv._resolve_dim(None, 1), 3)


class TestNpDtype(unittest.TestCase):
    def test_float32_default(self):
        import numpy as np
        self.assertEqual(uv._np_dtype("tensor(float)"), np.float32)

    def test_float16(self):
        import numpy as np
        self.assertEqual(uv._np_dtype("tensor(float16)"), np.float16)

    def test_int64(self):
        import numpy as np
        self.assertEqual(uv._np_dtype("tensor(int64)"), np.int64)

    def test_int32(self):
        import numpy as np
        self.assertEqual(uv._np_dtype("tensor(int32)"), np.int32)


class TestLoadYamlMissing(unittest.TestCase):
    def test_load_yaml_returns_empty_dict_when_file_missing(self):
        result = uv._load_yaml(Path("/nonexistent/path/config.yaml"))
        self.assertEqual(result, {})


class TestGetUploadedModelsEdgeCases(unittest.TestCase):
    def test_skips_tmp_dir(self):
        """tmp 디렉토리는 get_uploaded_models에서 제외된다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            base.mkdir()
            tmp_dir = base / "tmp"
            tmp_dir.mkdir()
            (tmp_dir / "leftover.onnx").write_bytes(b"x")
            result = uv.get_uploaded_models(base)
            self.assertEqual(result, [])

    def test_skips_non_dir_entries(self):
        """파일 항목은 건너뛴다."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "uploaded"
            base.mkdir()
            (base / "stray_file.txt").write_text("x")
            result = uv.get_uploaded_models(base)
            self.assertEqual(result, [])

    def test_returns_empty_when_base_missing(self):
        self.assertEqual(uv.get_uploaded_models(Path("/nonexistent/uploaded")), [])


class TestSetProfileEnabledWrongSource(unittest.TestCase):
    def test_non_uploaded_profile_is_rejected(self):
        """source != 'uploaded'인 프로필을 활성화하려 하면 에러 반환."""
        with tempfile.TemporaryDirectory() as tmp:
            profiles_path = Path(tmp) / "model_profiles.yaml"
            base = Path(tmp) / "uploaded"
            # 외부 모델 (source가 uploaded 아님)
            profiles_path.write_text(
                "models:\n  uploaded_fakeid:\n    enabled: false\n    source: builtin\n",
                encoding="utf-8",
            )
            ok, err = uv.activate_uploaded_model("fakeid", profiles_path, base)
            self.assertFalse(ok)
            self.assertIn("찾을 수 없", err)


if __name__ == "__main__":
    unittest.main()
