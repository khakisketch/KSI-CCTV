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


if __name__ == "__main__":
    unittest.main()
