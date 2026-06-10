"""
VLM 모듈 테스트
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import unittest
import json
import tempfile
from unittest.mock import Mock, patch, MagicMock


class TestVLMAnalysisResult(unittest.TestCase):
    """VLM 분석 결과 dataclass 테스트"""

    def test_default_values(self):
        from src.vlm.vlm_inference import VLMAnalysisResult
        result = VLMAnalysisResult()
        self.assertEqual(result.description, "")
        self.assertEqual(result.risk_level, "low")
        self.assertEqual(result.detected_events, [])
        self.assertEqual(result.recommended_action, "")

    def test_custom_values(self):
        from src.vlm.vlm_inference import VLMAnalysisResult
        result = VLMAnalysisResult(
            description="안전모 미착용",
            risk_level="high",
            detected_events=["no_helmet"],
            recommended_action="즉시 조치 필요",
            confidence=0.95,
        )
        self.assertEqual(result.description, "안전모 미착용")
        self.assertEqual(len(result.detected_events), 1)


class TestOllamaParser(unittest.TestCase):
    """OllamaProvider JSON 파싱 로직 테스트"""

    def _make_provider(self):
        from src.vlm.ollama_provider import OllamaProvider
        return OllamaProvider(base_url="http://localhost:99999", model="test")

    def test_parse_plain_json(self):
        provider = self._make_provider()
        content = json.dumps({
            "description": "화재 감지",
            "risk_level": "critical",
            "detected_events": ["fire"],
            "recommended_action": "소방대 호출",
            "confidence": 0.98,
        })
        result = provider._parse_response(content)
        self.assertEqual(result.description, "화재 감지")
        self.assertEqual(result.risk_level, "critical")

    def test_parse_markdown_code_block(self):
        provider = self._make_provider()
        raw_json = json.dumps({
            "description": "연기 발생",
            "risk_level": "high",
            "detected_events": ["smoke"],
            "recommended_action": "대피 유도",
            "confidence": 0.85,
        })
        content = f"```json\n{raw_json}\n```"
        result = provider._parse_response(content)
        self.assertEqual(result.description, "연기 발생")

    def test_parse_fallback_on_invalid(self):
        provider = self._make_provider()
        content = "이것은 JSON이 아닙니다. 그냥 텍스트 설명."
        result = provider._parse_response(content)
        self.assertIn("JSON이 아닙니다", result.description)
        self.assertEqual(result.risk_level, "medium")


class TestContextBuilder(unittest.TestCase):
    """컨텍스트 빌더 테스트"""

    def test_format_detections_empty(self):
        from src.vlm.context_builder import ContextBuilder
        self.assertEqual(ContextBuilder.format_detections([]), "탐지 결과 없음")

    def test_format_detections_populated(self):
        from src.vlm.context_builder import ContextBuilder
        # Simple mock detection objects
        det1 = Mock()
        det1.class_name = "person"
        det1.confidence = 0.95
        det2 = Mock()
        det2.class_name = "fire"
        det2.confidence = 0.78

        summary = ContextBuilder.format_detections([det1, det2])
        self.assertIn("person", summary)
        self.assertIn("fire", summary)

    def test_format_events_empty(self):
        from src.vlm.context_builder import ContextBuilder
        self.assertEqual(ContextBuilder.format_events([]), "최근 이벤트 없음")

    def test_build_prompt_with_template(self):
        from src.vlm.context_builder import ContextBuilder

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write("system: 'Test system prompt'\n")
            f.write('context_template: "Detections: {detections_summary}\nEvents: {recent_events}"\n')
            tmpl_path = f.name

        try:
            builder = ContextBuilder(tmpl_path)
            prompt = builder.build(
                detections_summary="person(0.95)",
                recent_events="[12:00] fire_detected",
            )
            self.assertIn("Test system prompt", prompt)
            self.assertIn("person(0.95)", prompt)
            self.assertIn("[12:00]", prompt)
        finally:
            import os
            os.unlink(tmpl_path)

    def test_build_missing_template(self):
        from src.vlm.context_builder import ContextBuilder
        builder = ContextBuilder("/nonexistent/path.yaml")
        prompt = builder.build("test", "events")
        self.assertEqual(prompt, "")


class TestOllamaIsAvailable(unittest.TestCase):
    """OllamaProvider.is_available 테스트"""

    @patch("src.vlm.ollama_provider.requests.get")
    def test_available_true(self, mock_get):
        from src.vlm.ollama_provider import OllamaProvider
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [{"name": "qwen3-vl:latest"}]
        }
        mock_get.return_value = mock_resp

        provider = OllamaProvider(model="qwen3-vl:latest")
        self.assertTrue(provider.is_available())

    @patch("src.vlm.ollama_provider.requests.get")
    def test_available_false_wrong_model(self, mock_get):
        from src.vlm.ollama_provider import OllamaProvider
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [{"name": "llama3:latest"}]
        }
        mock_get.return_value = mock_resp

        provider = OllamaProvider(model="qwen3-vl:latest")
        self.assertFalse(provider.is_available())

    @patch("src.vlm.ollama_provider.requests.get")
    def test_available_false_connection_error(self, mock_get):
        from src.vlm.ollama_provider import OllamaProvider
        mock_get.side_effect = Exception("Connection refused")

        provider = OllamaProvider(model="qwen3-vl:latest")
        self.assertFalse(provider.is_available())


if __name__ == "__main__":
    unittest.main()
