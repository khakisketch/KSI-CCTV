"""백로그 B3 — 단일 프레임 VLM 자격 하네스 테스트.

실제 ollama 호출 금지: provider(ContextBuilder/OllamaProvider)는 monkeypatch로
대체하고, 이미지 로드는 cv2로 만든 임시 PNG를 사용한다.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "qualify_vlm.py"


def _load_module(monkeypatch):
    """scripts/qualify_vlm.py를 `qualify_vlm` 모듈명으로 로드한다."""
    monkeypatch.syspath_prepend(str(ROOT))
    spec = importlib.util.spec_from_file_location("qualify_vlm", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["qualify_vlm"] = module
    spec.loader.exec_module(module)
    return module


def _make_image(path: Path) -> str:
    """cv2로 읽을 수 있는 임시 PNG를 생성한다."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    assert cv2.imwrite(str(path), frame)
    return str(path)


class _FakeResult:
    def __init__(self, parse_success=True, failure_kind="", verdict="confirm"):
        self.description = "테스트 설명"
        self.risk_level = "low"
        self.confidence = 0.9
        self.inference_time_ms = 1.0
        self.parse_success = parse_success
        self.verdict = verdict
        self.failure_kind = failure_kind


class _FakeBuilder:
    def __init__(self, *args, **kwargs):
        pass

    def build(self, *args, **kwargs):
        return "테스트 프롬프트"

    @staticmethod
    def format_detections(detections):
        return "탐지 결과 없음"


def _install_fake(monkeypatch, module, analyze):
    """OllamaProvider/ContextBuilder를 가짜로 교체한다."""

    class _FakeProvider:
        def __init__(self, *args, **kwargs):
            pass

        def analyze(self, image_bytes, prompt):
            return analyze(image_bytes, prompt)

    monkeypatch.setattr(module, "OllamaProvider", _FakeProvider)
    monkeypatch.setattr(module, "ContextBuilder", _FakeBuilder)


def _read_report(json_out: Path) -> dict:
    return json.loads(json_out.read_text(encoding="utf-8"))


def test_success_qualified_true(monkeypatch, tmp_path):
    module = _load_module(monkeypatch)
    image = _make_image(tmp_path / "frame.png")
    json_out = tmp_path / "report.json"
    _install_fake(monkeypatch, module, lambda img, prompt: _FakeResult())

    rc = module.main([
        "--model", "test-model",
        "--image", image,
        "--samples", "3",
        "--deadline-sec", "60",
        "--json-out", str(json_out),
    ])

    assert rc == 0
    report = _read_report(json_out)
    assert report["qualified"] is True
    assert report["stats"]["success_rate"] == 1.0
    for key in ("min_ms", "avg_ms", "p50_ms", "p95_ms", "max_ms"):
        assert key in report["stats"]
    assert len(report["samples"]) == 3
    sample = report["samples"][0]
    for key in ("latency_ms", "success", "failure_kind", "parse_success", "verdict"):
        assert key in sample
    assert sample["success"] is True


def test_success_qualified_false_when_p95_exceeds_deadline(monkeypatch, tmp_path):
    module = _load_module(monkeypatch)
    image = _make_image(tmp_path / "frame.png")
    json_out = tmp_path / "report.json"

    def _analyze(image_bytes, prompt):
        import time as _time

        _time.sleep(0.05)
        return _FakeResult()

    _install_fake(monkeypatch, module, _analyze)

    rc = module.main([
        "--model", "test-model",
        "--image", image,
        "--samples", "3",
        "--deadline-sec", "0.01",
        "--json-out", str(json_out),
    ])

    assert rc == 1
    report = _read_report(json_out)
    assert report["qualified"] is False
    assert report["stats"]["success_rate"] == 1.0
    assert report["stats"]["p95_ms"] > report["deadline_sec"] * 1000


def test_timeout_and_failure_kind_counts(monkeypatch, tmp_path):
    module = _load_module(monkeypatch)
    image = _make_image(tmp_path / "frame.png")
    json_out = tmp_path / "report.json"
    calls = {"n": 0}

    def _analyze(image_bytes, prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("마감 초과")
        if calls["n"] == 2:
            return _FakeResult(parse_success=False, failure_kind="malformed", verdict="error")
        return _FakeResult()

    _install_fake(monkeypatch, module, _analyze)

    rc = module.main([
        "--model", "test-model",
        "--image", image,
        "--samples", "3",
        "--deadline-sec", "60",
        "--json-out", str(json_out),
    ])

    assert rc == 1
    report = _read_report(json_out)
    assert report["qualified"] is False
    assert report["stats"]["success_rate"] == pytest.approx(1 / 3)
    kinds = report["stats"]["failure_kinds"]
    assert kinds.get("timeout", 0) == 1
    assert kinds.get("malformed", 0) == 1


def test_provider_total_failure_reports_unqualified(monkeypatch, tmp_path):
    module = _load_module(monkeypatch)
    image = _make_image(tmp_path / "frame.png")
    json_out = tmp_path / "report.json"

    def _analyze(image_bytes, prompt):
        raise RuntimeError("연결 실패")

    _install_fake(monkeypatch, module, _analyze)

    rc = module.main([
        "--model", "test-model",
        "--image", image,
        "--samples", "2",
        "--deadline-sec", "60",
        "--json-out", str(json_out),
    ])

    assert rc == 1
    report = _read_report(json_out)
    assert report["qualified"] is False
    assert report["stats"]["success_rate"] == 0.0
    assert sum(report["stats"]["failure_kinds"].values()) == 2


def test_image_load_failure_exits_2(monkeypatch, tmp_path, capsys):
    module = _load_module(monkeypatch)
    json_out = tmp_path / "report.json"

    rc = module.main([
        "--model", "test-model",
        "--image", str(tmp_path / "없는-이미지.png"),
        "--json-out", str(json_out),
    ])

    assert rc == 2
    assert "이미지" in capsys.readouterr().err
    assert not json_out.exists()


def test_report_required_keys(monkeypatch, tmp_path):
    module = _load_module(monkeypatch)
    image = _make_image(tmp_path / "frame.png")
    json_out = tmp_path / "report.json"
    _install_fake(monkeypatch, module, lambda img, prompt: _FakeResult())

    rc = module.main([
        "--model", "test-model",
        "--base-url", "http://localhost:11434",
        "--image", image,
        "--samples", "1",
        "--json-out", str(json_out),
    ])

    assert rc == 0
    report = _read_report(json_out)
    for key in ("model", "base_url", "host", "samples", "stats",
                "deadline_sec", "qualified", "generated_at"):
        assert key in report, f"리포트 필수 키 누락: {key}"
    for key in ("machine", "platform", "gpu"):
        assert key in report["host"], f"호스트 필수 키 누락: {key}"
