"""
VLM 모듈 테스트
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import json
import tempfile
import unittest
from unittest.mock import Mock, patch


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
            "verdict": "confirm",
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
            "verdict": "confirm",
        })
        content = f"```json\n{raw_json}\n```"
        result = provider._parse_response(content)
        self.assertEqual(result.description, "연기 발생")

    def test_parse_fallback_on_invalid(self):
        provider = self._make_provider()
        content = "이것은 JSON이 아닙니다. 그냥 텍스트 설명."
        result = provider._parse_response(content)
        self.assertIn("JSON이 아닙니다", result.description)
        # parse_success=False 시 risk_level은 'low'로 보존 (medium 하드코딩 제거)
        self.assertEqual(result.risk_level, "low")
        self.assertFalse(result.parse_success)

    def test_missing_verdict_is_schema_error(self):
        provider = self._make_provider()
        result = provider._parse_response(json.dumps({
            "description": "화재 감지",
            "risk_level": "critical",
            "confidence": 0.9,
        }))
        self.assertFalse(result.parse_success)
        self.assertEqual(result.verdict, "error")

    @patch("src.vlm.ollama_provider.requests.post")
    def test_zero_retries_still_attempts_once(self, mock_post):
        mock_post.side_effect = RuntimeError("offline")
        from src.vlm.ollama_provider import OllamaProvider

        provider = OllamaProvider(max_retries=0)
        result = provider.analyze(b"image", "prompt")

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(result.verdict, "error")

    @patch("src.vlm.ollama_provider.requests.post")
    def test_analyze_frames_keeps_independent_frame_verdicts(self, mock_post):
        from src.vlm.ollama_provider import OllamaProvider
        responses = []
        for verdict in ("confirm", "reject", "abstain"):
            response = Mock(status_code=200)
            response.json.return_value = {"message": {"content": json.dumps({
                "description": verdict, "risk_level": "low", "confidence": 0.8,
                "verdict": verdict,
            })}}
            responses.append(response)
        mock_post.side_effect = responses

        results = OllamaProvider(max_retries=1).analyze_frames([b"one", b"two", b"three"], "prompt")

        self.assertEqual([result.verdict for result in results], ["confirm", "reject", "abstain"])
        self.assertEqual(mock_post.call_count, 3)

    def test_shipped_prompts_require_verdict_and_confidence(self):
        for path in Path("config/prompts").glob("*.yaml"):
            content = path.read_text(encoding="utf-8")
            self.assertIn('"verdict"', content, path.name)
            self.assertIn('"confidence"', content, path.name)


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
            "confidence": 0.5,
            "verdict": "abstain",
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


class TestOllamaParserEmbeddedJsonFallback(unittest.TestCase):
    """Finding P2/S: 본문 내 임베드 JSON 추출 fallback 테스트"""

    def _make_provider(self):
        from src.vlm.ollama_provider import OllamaProvider

        return OllamaProvider(base_url="http://localhost:99999", model="test")

    def test_parse_inline_json_after_text(self):
        """'분석 결과: {...}' 형태 — 경로 3 fallback으로 파싱 성공해야 함"""
        provider = self._make_provider()
        json_obj = json.dumps(
            {
                "description": "안전모 미착용 작업자 발견",
                "risk_level": "high",
                "detected_events": ["no_helmet"],
                "recommended_action": "즉시 착용 지도",
                "confidence": 0.9,
                "verdict": "confirm",
            }
        )
        content = f"분석 결과:\n{json_obj}"
        result = provider._parse_response(content)
        self.assertTrue(result.parse_success, "임베드 JSON 추출 fallback이 parse_success=True여야 함")
        self.assertEqual(result.description, "안전모 미착용 작업자 발견")
        self.assertEqual(result.risk_level, "high")

    def test_parse_json_prefix_label(self):
        """'JSON: {...}' 형태도 fallback으로 파싱 성공해야 함"""
        provider = self._make_provider()
        json_obj = json.dumps(
            {
                "description": "화재 감지",
                "risk_level": "critical",
                "detected_events": ["fire"],
                "recommended_action": "소방대 호출",
                "confidence": 0.99,
                "verdict": "confirm",
            }
        )
        content = f"JSON: {json_obj}"
        result = provider._parse_response(content)
        self.assertTrue(result.parse_success)
        self.assertEqual(result.risk_level, "critical")

    def test_parse_trailing_explanation(self):
        """JSON 뒤에 설명이 붙어도 본문 앞 JSON 객체를 추출해야 함"""
        provider = self._make_provider()
        json_obj = json.dumps(
            {
                "description": "정상 상황",
                "risk_level": "low",
                "detected_events": [],
                "recommended_action": "계속 모니터링",
                "confidence": 0.7,
                "verdict": "reject",
            }
        )
        content = f"{json_obj}\n\n위 결과는 현재 상황 기준입니다."
        result = provider._parse_response(content)
        self.assertTrue(result.parse_success)
        self.assertEqual(result.risk_level, "low")

    def test_parse_no_json_returns_parse_failure(self):
        """JSON이 전혀 없으면 parse_success=False, risk_level='low'여야 함"""
        provider = self._make_provider()
        content = "현재 이미지에서 특이사항을 발견하지 못했습니다."
        result = provider._parse_response(content)
        self.assertFalse(result.parse_success)
        self.assertEqual(result.risk_level, "low")  # medium 하드코딩 제거 확인
        self.assertIn("특이사항", result.description)

    def test_extract_json_object_nested(self):
        """_extract_json_object가 중첩 객체를 올바르게 추출해야 함"""
        from src.vlm.ollama_provider import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:99999", model="test")
        text = '설명 {"a": {"b": 1}, "c": [1,2]} 이후 텍스트'
        extracted = provider._extract_json_object(text)
        self.assertIsNotNone(extracted)
        parsed = json.loads(extracted)
        self.assertEqual(parsed["a"]["b"], 1)

    def test_extract_json_object_with_braces_in_string(self):
        """문자열 내 중괄호가 있어도 괄호 균형을 올바르게 계산해야 함"""
        from src.vlm.ollama_provider import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:99999", model="test")
        text = 'prefix {"key": "value with { brace }"} suffix'
        extracted = provider._extract_json_object(text)
        self.assertIsNotNone(extracted)
        parsed = json.loads(extracted)
        self.assertIn("{", parsed["key"])


class TestPromptSelectorUnmappedWarning(unittest.TestCase):
    """Finding P3/S: 미매핑 도메인 경고 및 새 프롬프트 yaml 테스트"""

    def test_unmapped_domain_logs_warning(self):
        """PROMPT_MAP에 없는 도메인은 logger.warning을 1회 출력해야 함"""
        import logging

        import src.vlm.prompt_selector as _ps1
        from src.vlm.prompt_selector import PromptSelector as _PS1

        # _UNMAPPED_DOMAINS 초기화 (테스트 격리)
        _ps1._UNMAPPED_DOMAINS.clear()

        selector = _PS1(prompts_dir="./config/prompts")
        with self.assertLogs("src.vlm.prompt_selector", level=logging.WARNING) as cm:
            selector.get_builder("unknown_domain_xyz")

        warning_messages = [m for m in cm.output if "전용 프롬프트" in m]
        self.assertTrue(len(warning_messages) >= 1, "미매핑 도메인 경고가 없음")

    def test_unmapped_domain_warning_only_once(self):
        """같은 미매핑 도메인을 두 번 호출해도 경고는 1회만 출력해야 함"""
        import logging

        import src.vlm.prompt_selector as _ps2
        from src.vlm.prompt_selector import PromptSelector as _PS2

        _ps2._UNMAPPED_DOMAINS.clear()

        selector = _PS2(prompts_dir="./config/prompts")
        # 첫 번째 호출 — 경고 발생
        with self.assertLogs("src.vlm.prompt_selector", level=logging.WARNING):
            selector.get_builder("another_unknown_domain")

        # 두 번째 호출 — 캐시 히트이므로 경고 없음
        # assertLogs는 최소 1개 이상 있어야 통과하므로 sentinel warning 주입 후
        # 실제 "전용 프롬프트" 경고가 추가로 없는지 확인
        root_logger = logging.getLogger("src.vlm.prompt_selector")
        with self.assertLogs(root_logger, level=logging.WARNING) as cm2:
            root_logger.warning("__sentinel__")
            selector.get_builder("another_unknown_domain")

        domain_warnings = [m for m in cm2.output if "전용 프롬프트" in m]
        self.assertEqual(len(domain_warnings), 0, "두 번째 호출에서 중복 경고 발생")

    def test_mapped_domain_no_warning(self):
        """PROMPT_MAP에 있는 도메인은 미매핑 경고를 출력하지 않아야 함"""
        import logging

        import src.vlm.prompt_selector as _ps3
        from src.vlm.prompt_selector import PromptSelector as _PS3

        _ps3._UNMAPPED_DOMAINS.clear()

        selector = _PS3(prompts_dir="./config/prompts")
        # construction은 매핑 도메인 — 미매핑 경고 없어야 함
        # (파일 존재 시 INFO만 출력, WARNING 없어야 함)
        with self.assertLogs("src.vlm.prompt_selector", level=logging.DEBUG) as cm:
            selector.get_builder("construction")

        unmapped_warnings = [m for m in cm.output if "전용 프롬프트" in m]
        self.assertEqual(len(unmapped_warnings), 0)

    def test_factory_prompt_file_exists(self):
        """factory.yaml이 존재하고 정상적으로 로드되어야 함"""
        from src.vlm.prompt_selector import PromptSelector

        selector = PromptSelector(prompts_dir="./config/prompts")
        builder = selector.get_builder("factory")
        self.assertIsNotNone(builder)
        # 빌드 결과가 비어 있지 않아야 함
        prompt = builder.build("person(0.9)", "no events")
        self.assertGreater(len(prompt), 0)
        self.assertIn("공장", prompt)

    def test_warehouse_prompt_file_exists(self):
        """warehouse.yaml이 존재하고 정상적으로 로드되어야 함"""
        from src.vlm.prompt_selector import PromptSelector

        selector = PromptSelector(prompts_dir="./config/prompts")
        builder = selector.get_builder("warehouse")
        prompt = builder.build("person(0.9)", "no events")
        self.assertGreater(len(prompt), 0)
        self.assertIn("창고", prompt)

    def test_parking_prompt_file_exists(self):
        """parking.yaml이 존재하고 정상적으로 로드되어야 함"""
        from src.vlm.prompt_selector import PromptSelector

        selector = PromptSelector(prompts_dir="./config/prompts")
        builder = selector.get_builder("parking")
        prompt = builder.build("person(0.9)", "no events")
        self.assertGreater(len(prompt), 0)
        self.assertIn("주차", prompt)


class TestCreateVlmProvider(unittest.TestCase):
    """create_vlm_provider 팩토리 — config의 provider 키를 따라 생성/비활성."""

    def test_disabled_returns_none(self):
        from src.vlm.vlm_inference import create_vlm_provider
        self.assertIsNone(create_vlm_provider({"enabled": False}))
        self.assertIsNone(create_vlm_provider(None))

    def test_unknown_provider_returns_none(self):
        from src.vlm.vlm_inference import create_vlm_provider
        self.assertIsNone(create_vlm_provider({"enabled": True, "provider": "foobar"}))

    def test_qualified_provider_rejects_external_host(self):
        from src.vlm.vlm_inference import create_vlm_provider
        self.assertIsNone(create_vlm_provider({
            "enabled": True, "runtime_qualified": True, "model_digest": "a" * 64,
            "base_url": "https://example.com",
        }))

    def test_localhost_and_list_only_allowed_hosts(self):
        from src.vlm.vlm_inference import create_vlm_provider
        config = {"enabled": True, "runtime_qualified": True, "model_digest": "a" * 64}
        self.assertIsNotNone(create_vlm_provider(config))
        self.assertIsNone(create_vlm_provider({**config, "allowed_hosts": "localhost"}))

    def test_ollama_provider_created(self):
        from src.vlm.ollama_provider import OllamaProvider
        from src.vlm.vlm_inference import create_vlm_provider
        p = create_vlm_provider({
            "enabled": True, "provider": "ollama",
            "model_name": "qwen3-vl:latest", "base_url": "http://localhost:11434",
            "runtime_qualified": True, "model_digest": "a" * 64,
            "allowed_hosts": ["localhost"],
        })
        self.assertIsInstance(p, OllamaProvider)
        self.assertEqual(p.base_url, "http://127.0.0.1:11434")

    def test_allowlisted_dns_name_is_rejected_to_prevent_rebinding(self):
        from src.vlm.vlm_inference import create_vlm_provider
        config = {
            "enabled": True,
            "runtime_qualified": True,
            "model_digest": "a" * 64,
            "base_url": "http://ollama.internal:11434",
            "allowed_hosts": ["ollama.internal"],
        }
        self.assertIsNone(create_vlm_provider(config))

    @patch("src.vlm.ollama_provider.requests.post")
    @patch("src.vlm.ollama_provider.requests.get")
    def test_digest_is_rechecked_before_qualified_inference(self, mock_get, mock_post):
        from src.vlm.ollama_provider import OllamaProvider

        response = Mock(status_code=200)
        response.json.return_value = {"models": [{"name": "model", "digest": "b" * 64}]}
        mock_get.return_value = response
        result = OllamaProvider(model="model", model_digest="a" * 64).analyze(b"image", "prompt")

        self.assertEqual(result.verdict, "error")
        self.assertFalse(result.parse_success)
        mock_post.assert_not_called()

    def test_unqualified_enabled_provider_is_disabled(self):
        from src.vlm.vlm_inference import create_vlm_provider
        # Explicit qualification is required even when enabled.
        p = create_vlm_provider({"enabled": True})
        self.assertIsNone(p)


if __name__ == "__main__":
    unittest.main()
