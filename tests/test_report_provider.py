"""P3-T1: ReportProvider 추상화 + Ollama 텍스트 구현 테스트 (TDD RED)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests


def _ok_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"message": {"content": text}}
    return resp


def test_report_provider_interface_generate_returns_str() -> None:
    from src.reports.provider import ReportProvider

    assert hasattr(ReportProvider, "generate")
    # 추상 인터페이스: generate(prompt) -> str 시그니처 유지
    import inspect

    sig = inspect.signature(ReportProvider.generate)
    assert list(sig.parameters)[1] == "prompt"


def test_ollama_text_provider_success() -> None:
    from src.reports.provider import OllamaTextProvider

    provider = OllamaTextProvider(base_url="http://127.0.0.1:11434", model="llama3.1", timeout_sec=5.0)
    with patch("src.reports.provider.requests.post", return_value=_ok_response("  일일 보고  ")) as m:
        assert provider.generate("요약해줘") == "일일 보고"
        assert m.call_args[0][0] == "http://127.0.0.1:11434/api/chat"
        # G-1: timeout 전달값·payload 계약 고정
        assert m.call_args[1]["timeout"] == 5.0
        payload = m.call_args[1]["json"]
        assert payload["model"] == "llama3.1"
        assert payload["messages"] == [{"role": "user", "content": "요약해줘"}]
        assert payload["stream"] is False


def test_ollama_text_provider_rejects_missing_model() -> None:
    from src.reports.provider import OllamaTextProvider

    with pytest.raises(ValueError):
        OllamaTextProvider(base_url="http://127.0.0.1:11434", model="")
    with pytest.raises(ValueError):
        OllamaTextProvider(base_url="http://127.0.0.1:11434", model="   ")


def test_ollama_text_provider_rejects_non_local_endpoint() -> None:
    from src.reports.provider import OllamaTextProvider

    with pytest.raises(ValueError):
        OllamaTextProvider(base_url="https://api.external.example.com", model="llama3.1")
    with pytest.raises(ValueError):
        OllamaTextProvider(base_url="http://8.8.8.8:11434", model="llama3.1")
    with pytest.raises(ValueError):
        OllamaTextProvider(base_url="http://0.0.0.0:11434", model="llama3.1")


def test_ollama_text_provider_timeout_propagates() -> None:
    from src.reports.provider import OllamaTextProvider

    provider = OllamaTextProvider(base_url="http://127.0.0.1:11434", model="llama3.1")
    with (
        patch(
            "src.reports.provider.requests.post",
            side_effect=requests.Timeout("timed out"),
        ),
        pytest.raises(requests.Timeout),
    ):
        provider.generate("요약해줘")


def test_ollama_text_provider_connection_error_propagates() -> None:
    from src.reports.provider import OllamaTextProvider

    provider = OllamaTextProvider(base_url="http://127.0.0.1:11434", model="llama3.1")
    with (
        patch(
            "src.reports.provider.requests.post",
            side_effect=requests.ConnectionError("refused"),
        ),
        pytest.raises(requests.ConnectionError),
    ):
        provider.generate("요약해줘")


def test_ollama_text_provider_malformed_json_propagates() -> None:
    from src.reports.provider import OllamaTextProvider

    provider = OllamaTextProvider(base_url="http://127.0.0.1:11434", model="llama3.1")
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("No JSON could be decoded")
    with (
        patch("src.reports.provider.requests.post", return_value=resp),
        pytest.raises(ValueError),
    ):
        provider.generate("요약해줘")


def test_ollama_text_provider_http500_propagates() -> None:
    from src.reports.provider import OllamaTextProvider

    provider = OllamaTextProvider(base_url="http://127.0.0.1:11434", model="llama3.1")
    resp = MagicMock()
    resp.status_code = 500
    resp.text = "internal error"
    with (
        patch("src.reports.provider.requests.post", return_value=resp),
        pytest.raises(RuntimeError),
    ):
        provider.generate("요약해줘")


def test_ollama_text_provider_empty_response_raises() -> None:
    from src.reports.provider import OllamaTextProvider

    provider = OllamaTextProvider(base_url="http://127.0.0.1:11434", model="llama3.1")
    with (
        patch("src.reports.provider.requests.post", return_value=_ok_response("   ")),
        pytest.raises(RuntimeError),
    ):
        provider.generate("요약해줘")
