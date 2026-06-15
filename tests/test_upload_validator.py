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


if __name__ == "__main__":
    unittest.main()
