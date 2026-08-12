# AI Field Evaluation

`scripts/evaluate_ai_events.py` produces reproducible, offline release evidence before a new
pose, ST-GCN, or VLM event path is enabled. It validates a locked ground-truth JSONL manifest
against one prediction JSONL record per manifest record. It does not establish a universal safety,
accuracy, or deployment claim. Approval thresholds are selected by the caller for the proposed
model and operating domain.

## Input Schema

Each JSONL line is one object. `event_id` or `sample_id` is required (exactly one per record) and
must be unique within each file. Both files require non-empty `site`, `camera`, and `event_type`.
The paired records must agree on identity type, site, camera, event type, and each declared metadata
dimension.

Ground truth requires a JSON boolean `ground_truth`. Predictions require a JSON boolean `prediction`
and numeric `confidence` from 0 to 1. Optional `timestamp` fields must be non-empty strings and are
retained only for input traceability; the harness does not interpret or emit them. `latency_ms` is
optional on predictions. Optional
non-negative `observation_hours` belongs on each ground-truth record and must have the same value for
every record in a site/camera/event-type scope. Repeated sample rows do not add observation hours.
Common declared metadata dimensions are `lighting`, `indoor`, and `occlusion`.

```json
{"event_id":"sample-001","site":"site-a","camera":"loading-dock-1","event_type":"fall","ground_truth":true,"timestamp":"2026-08-01T10:00:00Z","lighting":"low","indoor":"false","occlusion":"partial","observation_hours":12}
{"event_id":"sample-001","site":"site-a","camera":"loading-dock-1","event_type":"fall","prediction":true,"confidence":0.93,"timestamp":"2026-08-01T10:00:00.047Z","latency_ms":47.2,"lighting":"low","indoor":"false","occlusion":"partial"}
```

Malformed JSON, duplicate identities, non-boolean labels, missing counterpart records, prediction
records absent from the manifest, and mismatched paired labels/grouping are rejected. Use the optional
SHA-256 check to lock evaluation to the exact ground-truth file bytes.

## Run

```bash
python3 scripts/evaluate_ai_events.py \
  --ground-truth data/field-manifest.jsonl \
  --predictions reports/candidate-predictions.jsonl \
  --metadata-dimension lighting \
  --metadata-dimension indoor \
  --metadata-dimension occlusion \
  --expected-dataset-sha256 "$(sha256sum data/field-manifest.jsonl | cut -d' ' -f1)" \
  --min-precision 0.90 --min-recall 0.85 --min-f1 0.87 \
  --max-fp-per-camera-hour 0.10 --max-p95-latency-ms 500 \
  --json-out reports/field-evaluation.json
```

The report contains TP/FP/FN/TN, precision, recall, F1, optional false positives per camera-hour,
and p50/p95 latency. Event-type FP/hour totals one observation period per site/camera/event-type
scope, rather than per sample row. Broader overall and site/camera FP/hour values are emitted only
when all event-type scopes for a camera have the same supplied observation period. Metadata FP/hour
is emitted only when each represented event scope has one value for that dimension; mixed values do
not imply time allocation and produce `null` instead. It includes event type, site/camera, and
requested metadata breakdowns. Site and camera group identifiers are deterministic truncated SHA-256
values; raw camera or site identifiers are never emitted. JSON keys and records are sorted for stable
release evidence. A requested threshold that cannot be measured, or is exceeded, returns exit status
1. Invalid inputs return exit status 2.
