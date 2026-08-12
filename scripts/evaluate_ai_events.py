#!/usr/bin/env python3
"""Evaluate binary AI event predictions against a locked JSONL manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


class EvaluationError(ValueError):
    """Raised when evaluation input cannot be used as release evidence."""


def _identity(record: dict[str, Any], source: str, line_number: int) -> str:
    keys = [key for key in ("event_id", "sample_id") if key in record]
    if len(keys) != 1 or not isinstance(record.get(keys[0]) if keys else None, str):
        raise EvaluationError(
            f"{source}:{line_number}: require exactly one non-empty event_id or sample_id"
        )
    value = record[keys[0]].strip()
    if not value:
        raise EvaluationError(f"{source}:{line_number}: identity cannot be empty")
    return f"{keys[0]}:{value}"


def _require_string(record: dict[str, Any], key: str, source: str, line_number: int) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{source}:{line_number}: {key} must be a non-empty string")
    return value.strip()


def _require_bool(record: dict[str, Any], key: str, source: str, line_number: int) -> bool:
    value = record.get(key)
    if type(value) is not bool:
        raise EvaluationError(f"{source}:{line_number}: {key} must be a JSON boolean")
    return value


def _optional_number(
    record: dict[str, Any], key: str, source: str, line_number: int
) -> float | None:
    value = record.get(key)
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise EvaluationError(f"{source}:{line_number}: {key} must be a finite number")
    return float(value)


def _optional_timestamp(record: dict[str, Any], source: str, line_number: int) -> str | None:
    value = record.get("timestamp")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{source}:{line_number}: timestamp must be a non-empty string")
    return value.strip()


def _load_jsonl(path: Path, kind: str, metadata_dimensions: list[str]) -> dict[str, dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationError(f"cannot read {kind} file {path}: {exc}") from exc
    if not lines:
        raise EvaluationError(f"{kind} file is empty")

    records: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise EvaluationError(f"{kind}:{line_number}: blank lines are not allowed")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"{kind}:{line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise EvaluationError(f"{kind}:{line_number}: record must be a JSON object")

        identity = _identity(record, kind, line_number)
        if identity in records:
            raise EvaluationError(
                f"{kind}:{line_number}: duplicate identity {identity.split(':', 1)[0]}"
            )
        normalized = {
            "identity": identity,
            "event_type": _require_string(record, "event_type", kind, line_number),
            "site": _require_string(record, "site", kind, line_number),
            "camera": _require_string(record, "camera", kind, line_number),
            "timestamp": _optional_timestamp(record, kind, line_number),
            "metadata": {},
        }
        label_key = "ground_truth" if kind == "ground truth" else "prediction"
        normalized[label_key] = _require_bool(record, label_key, kind, line_number)
        if kind == "predictions":
            confidence = _optional_number(record, "confidence", kind, line_number)
            if confidence is None or not 0 <= confidence <= 1:
                raise EvaluationError(f"{kind}:{line_number}: confidence must be between 0 and 1")
            normalized["confidence"] = confidence
            latency = _optional_number(record, "latency_ms", kind, line_number)
            if latency is not None and latency < 0:
                raise EvaluationError(f"{kind}:{line_number}: latency_ms must be non-negative")
            normalized["latency_ms"] = latency
        else:
            observation_hours = _optional_number(record, "observation_hours", kind, line_number)
            if observation_hours is not None and observation_hours < 0:
                raise EvaluationError(
                    f"{kind}:{line_number}: observation_hours must be non-negative"
                )
            normalized["observation_hours"] = observation_hours
        for dimension in metadata_dimensions:
            normalized["metadata"][dimension] = _require_string(
                record, dimension, kind, line_number
            )
        records[identity] = normalized
    return records


def _hashed(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def _metrics(
    counts: dict[str, int], observation_hours: float | None = None
) -> dict[str, float | int | None]:
    tp, fp, fn, tn = (counts[key] for key in ("tp", "fp", "fn", "tn"))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "false_positives_per_camera_hour": round(fp / observation_hours, 6)
        if observation_hours and observation_hours > 0
        else None,
    }


def _new_counts() -> dict[str, int]:
    return {"tp": 0, "fp": 0, "fn": 0, "tn": 0}


def evaluate(
    truth_path: Path,
    prediction_path: Path,
    metadata_dimensions: list[str],
    expected_dataset_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate paired records and return a deterministic privacy-preserving report."""
    if len(set(metadata_dimensions)) != len(metadata_dimensions):
        raise EvaluationError("metadata dimensions must not contain duplicates")
    try:
        actual_hash = hashlib.sha256(truth_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise EvaluationError(f"cannot read ground truth file {truth_path}: {exc}") from exc
    if expected_dataset_sha256 and actual_hash.lower() != expected_dataset_sha256.lower():
        raise EvaluationError(
            "ground-truth manifest SHA-256 does not match --expected-dataset-sha256"
        )

    truth = _load_jsonl(truth_path, "ground truth", metadata_dimensions)
    predictions = _load_jsonl(prediction_path, "predictions", metadata_dimensions)
    unknown = sorted(set(predictions) - set(truth))
    if unknown:
        raise EvaluationError("predictions contain identities absent from ground truth")
    missing = sorted(set(truth) - set(predictions))
    if missing:
        raise EvaluationError("ground truth identities are missing predictions")

    overall = _new_counts()
    by_event: dict[str, dict[str, int]] = defaultdict(_new_counts)
    by_site_camera: dict[str, dict[str, int]] = defaultdict(_new_counts)
    by_metadata: dict[str, dict[str, dict[str, int]]] = {
        dimension: defaultdict(_new_counts) for dimension in metadata_dimensions
    }
    observation_by_scope: dict[str, float | None] = {}
    metadata_values_by_scope: dict[str, dict[str, set[str]]] = {
        dimension: defaultdict(set) for dimension in metadata_dimensions
    }
    latencies: list[float] = []

    for identity in sorted(truth):
        actual, predicted = truth[identity], predictions[identity]
        for field in ("event_type", "site", "camera", "metadata"):
            if actual[field] != predicted[field]:
                raise EvaluationError(
                    f"identity {identity.split(':', 1)[0]} has mixed {field} values"
                )
        key = (
            "tp"
            if actual["ground_truth"] and predicted["prediction"]
            else "fp"
            if predicted["prediction"]
            else "fn"
            if actual["ground_truth"]
            else "tn"
        )
        groups = [
            overall,
            by_event[actual["event_type"]],
            by_site_camera[f"{actual['site']}\x1f{actual['camera']}"],
        ]
        groups.extend(
            by_metadata[dimension][actual["metadata"][dimension]]
            for dimension in metadata_dimensions
        )
        for counts in groups:
            counts[key] += 1
        if predicted["latency_ms"] is not None:
            latencies.append(predicted["latency_ms"])
        camera_key = f"{actual['site']}\x1f{actual['camera']}"
        scope_key = f"{camera_key}\x1f{actual['event_type']}"
        hours = actual["observation_hours"]
        if scope_key in observation_by_scope and observation_by_scope[scope_key] != hours:
            raise EvaluationError(
                "observation_hours must be consistent for each site/camera/event_type scope"
            )
        observation_by_scope[scope_key] = hours
        for dimension in metadata_dimensions:
            metadata_values_by_scope[dimension][scope_key].add(actual["metadata"][dimension])

    def denominator(scopes: list[str]) -> float | None:
        hours = [observation_by_scope[scope] for scope in scopes]
        return sum(hours) if all(value is not None for value in hours) else None

    event_hours = {
        event_type: denominator(
            [scope for scope in observation_by_scope if scope.rsplit("\x1f", 1)[1] == event_type]
        )
        for event_type in by_event
    }
    camera_scopes: dict[str, list[str]] = defaultdict(list)
    for scope in observation_by_scope:
        camera_key, _ = scope.rsplit("\x1f", 1)
        camera_scopes[camera_key].append(scope)
    camera_hours: dict[str, float | None] = {}
    for camera_key, scopes in camera_scopes.items():
        scoped_hours = [observation_by_scope[scope] for scope in scopes]
        camera_hours[camera_key] = (
            scoped_hours[0]
            if all(value is not None and value == scoped_hours[0] for value in scoped_hours)
            else None
        )
    total_observation_hours = (
        sum(camera_hours.values())
        if all(hours is not None for hours in camera_hours.values())
        else None
    )
    metadata_hours: dict[str, dict[str, float | None]] = {}
    for dimension, values_by_scope in metadata_values_by_scope.items():
        dimension_hours: dict[str, float | None] = {}
        for value in by_metadata[dimension]:
            scopes = [scope for scope, values in values_by_scope.items() if values == {value}]
            dimension_hours[value] = (
                denominator(scopes)
                if all(len(values) == 1 for values in values_by_scope.values())
                else None
            )
        metadata_hours[dimension] = dimension_hours
    report = {
        "schema_version": 1,
        "ground_truth_sha256": actual_hash,
        "records_evaluated": len(truth),
        "metadata_dimensions": metadata_dimensions,
        "overall": _metrics(overall, total_observation_hours),
        "latency_ms": {
            "count": len(latencies),
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
        },
        "breakdowns": {
            "event_type": {
                name: _metrics(by_event[name], event_hours[name]) for name in sorted(by_event)
            },
            "site_camera": {
                _hashed(group): _metrics(by_site_camera[group], camera_hours.get(group))
                for group in sorted(by_site_camera)
            },
            "metadata": {
                dimension: {
                    value: _metrics(groups[value], metadata_hours[dimension][value])
                    for value in sorted(groups)
                }
                for dimension, groups in sorted(by_metadata.items())
            },
        },
    }
    return report


def _gate_failures(report: dict[str, Any], args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    overall = report["overall"]
    for field, threshold in (
        ("precision", args.min_precision),
        ("recall", args.min_recall),
        ("f1", args.min_f1),
    ):
        if threshold is not None and overall[field] < threshold:
            failures.append(f"{field} {overall[field]:.6f} is below {threshold:.6f}")
    if args.max_fp_per_camera_hour is not None:
        rate = overall["false_positives_per_camera_hour"]
        if rate is None:
            failures.append(
                "false positives per camera-hour unavailable: observation_hours not supplied"
            )
        elif rate > args.max_fp_per_camera_hour:
            failures.append(
                f"false positives per camera-hour {rate:.6f} exceeds {args.max_fp_per_camera_hour:.6f}"
            )
    if args.max_p95_latency_ms is not None:
        p95 = report["latency_ms"]["p95"]
        if p95 is None:
            failures.append("p95 latency unavailable: latency_ms not supplied")
        elif p95 > args.max_p95_latency_ms:
            failures.append(f"p95 latency {p95:.3f}ms exceeds {args.max_p95_latency_ms:.3f}ms")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate AI event predictions against a locked JSONL manifest"
    )
    parser.add_argument(
        "--ground-truth", required=True, type=Path, help="locked ground-truth JSONL manifest"
    )
    parser.add_argument("--predictions", required=True, type=Path, help="prediction JSONL file")
    parser.add_argument(
        "--json-out", type=Path, help="write deterministic evidence JSON to this path"
    )
    parser.add_argument(
        "--metadata-dimension",
        action="append",
        default=[],
        help="required metadata field to break down (repeatable)",
    )
    parser.add_argument(
        "--expected-dataset-sha256", help="optional SHA-256 of the exact ground-truth JSONL bytes"
    )
    parser.add_argument("--min-precision", type=float)
    parser.add_argument("--min-recall", type=float)
    parser.add_argument("--min-f1", type=float)
    parser.add_argument("--max-fp-per-camera-hour", type=float)
    parser.add_argument("--max-p95-latency-ms", type=float)
    args = parser.parse_args(argv)
    for option in ("min_precision", "min_recall", "min_f1"):
        value = getattr(args, option)
        if value is not None and not 0 <= value <= 1:
            parser.error(f"--{option.replace('_', '-')} must be between 0 and 1")
    for option in ("max_fp_per_camera_hour", "max_p95_latency_ms"):
        value = getattr(args, option)
        if value is not None and value < 0:
            parser.error(f"--{option.replace('_', '-')} must be non-negative")

    try:
        report = evaluate(
            args.ground_truth,
            args.predictions,
            args.metadata_dimension,
            args.expected_dataset_sha256,
        )
    except EvaluationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    failures = _gate_failures(report, args)
    report["ok"] = not failures
    report["gate_failures"] = failures
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
