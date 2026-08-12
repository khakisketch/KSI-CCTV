"""Tests for the offline AI field-evaluation evidence harness."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "evaluate_ai_events", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_ai_events.py"
)
assert SPEC and SPEC.loader
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _truth(event_id: str, label: bool, **overrides: object) -> dict[str, object]:
    return {
        "event_id": event_id,
        "site": "west-yard",
        "camera": "gate-7",
        "event_type": "fall",
        "ground_truth": label,
        "timestamp": "2026-08-01T00:00:00Z",
        "lighting": "night",
        "indoor": "false",
        "observation_hours": 2.0,
        **overrides,
    }


def _prediction(event_id: str, label: bool, **overrides: object) -> dict[str, object]:
    return {
        "event_id": event_id,
        "site": "west-yard",
        "camera": "gate-7",
        "event_type": "fall",
        "prediction": label,
        "timestamp": "2026-08-01T00:00:01Z",
        "confidence": 0.8,
        "latency_ms": 10.0,
        "lighting": "night",
        "indoor": "false",
        **overrides,
    }


def test_evaluate_computes_metrics_latency_and_anonymized_breakdowns(tmp_path: Path) -> None:
    truth = _write_jsonl(
        tmp_path / "truth.jsonl",
        [_truth("1", True), _truth("2", False), _truth("3", True), _truth("4", False)],
    )
    predictions = _write_jsonl(
        tmp_path / "predictions.jsonl",
        [
            _prediction("1", True, latency_ms=10),
            _prediction("2", True, latency_ms=20),
            _prediction("3", False, latency_ms=30),
            _prediction("4", False, latency_ms=40),
        ],
    )

    report = evaluator.evaluate(truth, predictions, ["lighting", "indoor"])

    assert report["overall"] == {
        "tp": 1,
        "fp": 1,
        "fn": 1,
        "tn": 1,
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
        "false_positives_per_camera_hour": 0.5,
    }
    assert report["latency_ms"] == {"count": 4, "p50": 25.0, "p95": 38.5}
    assert report["breakdowns"]["event_type"]["fall"]["false_positives_per_camera_hour"] == 0.5
    site_camera = next(iter(report["breakdowns"]["site_camera"]))
    assert site_camera.startswith("sha256:")
    assert "west-yard" not in json.dumps(report)
    assert "gate-7" not in json.dumps(report)


@pytest.mark.parametrize(
    ("truth_records", "prediction_records", "message"),
    [
        ([_truth("1", True), _truth("1", False)], [_prediction("1", True)], "duplicate identity"),
        ([_truth("1", True)], [_prediction("2", True)], "absent from ground truth"),
        ([_truth("1", True)], [_prediction("1", True, event_type="fight")], "mixed event_type"),
        ([_truth("1", "true")], [_prediction("1", True)], "JSON boolean"),
        ([_truth("1", True, timestamp=42)], [_prediction("1", True)], "timestamp must be"),
        (
            [_truth("1", True)],
            [_prediction("1", True, confidence=1.2)],
            "confidence must be between",
        ),
    ],
)
def test_evaluate_rejects_invalid_or_mismatched_records(
    tmp_path: Path,
    truth_records: list[dict[str, object]],
    prediction_records: list[dict[str, object]],
    message: str,
) -> None:
    truth = _write_jsonl(tmp_path / "truth.jsonl", truth_records)
    predictions = _write_jsonl(tmp_path / "predictions.jsonl", prediction_records)

    with pytest.raises(evaluator.EvaluationError, match=message):
        evaluator.evaluate(truth, predictions, ["lighting", "indoor"])


def test_evaluate_rejects_hash_mismatch_and_inconsistent_observation_hours(tmp_path: Path) -> None:
    truth = _write_jsonl(
        tmp_path / "truth.jsonl", [_truth("1", True), _truth("2", False, observation_hours=3)]
    )
    predictions = _write_jsonl(
        tmp_path / "predictions.jsonl", [_prediction("1", True), _prediction("2", False)]
    )

    with pytest.raises(evaluator.EvaluationError, match="SHA-256"):
        evaluator.evaluate(truth, predictions, [], "0" * 64)
    with pytest.raises(evaluator.EvaluationError, match="site/camera/event_type"):
        evaluator.evaluate(truth, predictions, [])


def test_evaluate_scopes_observation_hours_by_event_without_double_counting(tmp_path: Path) -> None:
    truth = _write_jsonl(
        tmp_path / "truth.jsonl",
        [
            _truth("1", False, observation_hours=2),
            _truth("2", False, observation_hours=2),
            _truth("3", False, event_type="fight", observation_hours=4, lighting="day"),
        ],
    )
    predictions = _write_jsonl(
        tmp_path / "predictions.jsonl",
        [
            _prediction("1", True),
            _prediction("2", False),
            _prediction("3", True, event_type="fight", lighting="day"),
        ],
    )

    report = evaluator.evaluate(truth, predictions, ["lighting"])

    assert report["breakdowns"]["event_type"]["fall"]["false_positives_per_camera_hour"] == 0.5
    assert report["breakdowns"]["event_type"]["fight"]["false_positives_per_camera_hour"] == 0.25
    assert report["overall"]["false_positives_per_camera_hour"] is None
    site_camera = next(iter(report["breakdowns"]["site_camera"].values()))
    assert site_camera["false_positives_per_camera_hour"] is None
    assert (
        report["breakdowns"]["metadata"]["lighting"]["night"]["false_positives_per_camera_hour"]
        == 0.5
    )
    assert (
        report["breakdowns"]["metadata"]["lighting"]["day"]["false_positives_per_camera_hour"]
        == 0.25
    )


def test_evaluate_omits_metadata_rate_for_a_partial_observation_scope(tmp_path: Path) -> None:
    truth = _write_jsonl(
        tmp_path / "truth.jsonl",
        [_truth("1", False, lighting="night"), _truth("2", False, lighting="day")],
    )
    predictions = _write_jsonl(
        tmp_path / "predictions.jsonl",
        [_prediction("1", True), _prediction("2", False, lighting="day")],
    )

    report = evaluator.evaluate(truth, predictions, ["lighting"])

    assert (
        report["breakdowns"]["metadata"]["lighting"]["night"]["false_positives_per_camera_hour"]
        is None
    )
    assert (
        report["breakdowns"]["metadata"]["lighting"]["day"]["false_positives_per_camera_hour"]
        is None
    )


def test_main_writes_deterministic_evidence_and_fails_requested_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    truth = _write_jsonl(tmp_path / "truth.jsonl", [_truth("1", True)])
    predictions = _write_jsonl(tmp_path / "predictions.jsonl", [_prediction("1", False)])
    output = tmp_path / "evidence.json"

    result = evaluator.main(
        [
            "--ground-truth",
            str(truth),
            "--predictions",
            str(predictions),
            "--metadata-dimension",
            "lighting",
            "--json-out",
            str(output),
            "--min-f1",
            "0.5",
        ]
    )

    assert result == 1
    assert output.read_text(encoding="utf-8") == capsys.readouterr().out
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["ok"] is False
    assert evidence["gate_failures"] == ["f1 0.000000 is below 0.500000"]
