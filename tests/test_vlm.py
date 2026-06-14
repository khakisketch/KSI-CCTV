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


class TestVLMAnalysisResultNewFields(unittest.TestCase):
    """VLMAnalysisResult 신규 필드 테스트 (Phase 2)"""

    def test_parse_success_default(self):
        from src.vlm.vlm_inference import VLMAnalysisResult
        result = VLMAnalysisResult()
        self.assertTrue(result.parse_success)
        self.assertEqual(result.raw_response, "")

    def test_parse_failure(self):
        from src.vlm.vlm_inference import VLMAnalysisResult
        result = VLMAnalysisResult(
            description="파싱 실패 원본",
            parse_success=False,
            raw_response="not a valid json {"
        )
        self.assertFalse(result.parse_success)
        self.assertEqual(result.raw_response, "not a valid json {")
        self.assertEqual(result.description, "파싱 실패 원본")

    def test_to_dict_includes_new_fields(self):
        from src.vlm.vlm_inference import VLMAnalysisResult
        result = VLMAnalysisResult(
            description="test",
            risk_level="medium",
            confidence=0.8,
            parse_success=True,
        )
        d = result.to_dict()
        self.assertIn("parse_success", d)
        self.assertIn("description", d)
        self.assertTrue(d["parse_success"])


class TestOllamaParserNewFields(unittest.TestCase):
    """OllamaProvider 파싱 시 신규 필드 포함 테스트 (Phase 2)"""

    def _make_provider(self):
        from src.vlm.ollama_provider import OllamaProvider
        return OllamaProvider(base_url="http://localhost:99999", model="test")

    def test_parse_success_includes_raw_response(self):
        provider = self._make_provider()
        content = json.dumps({
            "description": "test",
            "risk_level": "low",
            "detected_events": [],
        })
        result = provider._parse_response(content)
        self.assertTrue(result.parse_success)
        self.assertIn("test", result.raw_response)

    def test_parse_failure_includes_raw_response(self):
        provider = self._make_provider()
        content = "이건 JSON이 아닙니다."
        result = provider._parse_response(content)
        self.assertFalse(result.parse_success)
        self.assertIn("JSON", result.raw_response)


class TestPromptSelector(unittest.TestCase):
    """PromptSelector 자동 프롬프트 선택 테스트 (Phase 2)"""

    def test_get_builder_construction(self):
        from src.vlm.prompt_selector import PromptSelector
        selector = PromptSelector(prompts_dir="./config/prompts")
        builder = selector.get_builder("construction")
        self.assertIsNotNone(builder)

    def test_get_builder_school(self):
        from src.vlm.prompt_selector import PromptSelector
        selector = PromptSelector(prompts_dir="./config/prompts")
        builder = selector.get_builder("school")
        self.assertIsNotNone(builder)

    def test_get_builder_caches(self):
        from src.vlm.prompt_selector import PromptSelector
        selector = PromptSelector(prompts_dir="./config/prompts")
        b1 = selector.get_builder("construction")
        b2 = selector.get_builder("construction")
        self.assertIs(b1, b2)  # 같은 인스턴스 반환

    def test_get_builder_unknown_type_falls_back(self):
        from src.vlm.prompt_selector import PromptSelector
        selector = PromptSelector(prompts_dir="./config/prompts")
        builder = selector.get_builder("unknown_type_xyz")
        self.assertIsNotNone(builder)  # default로 fallback

    def test_build_prompt_returns_string(self):
        from src.vlm.prompt_selector import PromptSelector
        selector = PromptSelector(prompts_dir="./config/prompts")
        prompt = selector.build_prompt("construction", "person(0.9)", "no events")
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 0)


if __name__ == "__main__":
    unittest.main()
