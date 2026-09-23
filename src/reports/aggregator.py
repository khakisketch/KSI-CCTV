"""P3-T2: 기간 집계 → 프롬프트 컨텍스트.

축소 규칙(테스트로 고정):
- 토큰 추정은 ``ceil(len(text) / 4)`` 휴리스틱이다 (추가 의존성 없는 상한 근사;
  실제 토크나이저 기준이 아니라 추정 기준 상한을 보장한다).
- 상한 초과 시 버킷 상세를 카메라별 요약으로 먼저 축소한다.
- 그래도 초과 시 오래된 이벤트부터 통째로 제외한다 (원문 절단 금지 —
  개별 이벤트 행을 중간에서 자르지 않는다).
- 다음으로 오래된 인시던트를 통째로 제외하고, 마지막으로 버킷 요약을 제외한다.
  (후반 순서는 인시던트→버킷으로 고정한다 — 축소 순서 변경 금지.)
- 인시던트/버킷은 limit-only 저장소를 limit 확장 재조회로 DESC 순회하며
  윈도우 시작보다 오래된 행이 보일 때까지 수집한다 (페이지 상한 내,
  기존 storage 시그니처 무수정). 이벤트는 DB 측 since/until 필터를 그대로 쓴다.
- 상한 안에 들지 않으면(헤더 단독 초과 등) 있는 그대로 반환한다 —
  best-effort이며 헤더/비고는 원문이 아니다.
- 제외된 항목은 ``## omitted``에 종류별 건수로 표기한다 (버킷 포함).
"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

_FETCH_PAGE_SIZE = 500
_MAX_FETCH_PAGES = 10
_DT_MIN = datetime.min.replace(tzinfo=timezone.utc)


def estimate_tokens(text: str) -> int:
    """문자열 길이에 대한 토큰 추정 (``ceil(len/4)``, 빈 문자열은 0)."""
    if not text:
        return 0
    return (len(text) + 3) // 4


def _parse_dt(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _in_range(value: object, since: str, until: str) -> bool:
    """파싱 실패 시 포함한다 (원본 유실을 방지)."""
    point = _parse_dt(value)
    start = _parse_dt(since)
    end = _parse_dt(until)
    if point is None or start is None or end is None:
        return True
    return start <= point <= end


def _desc_key(row: dict[str, Any], time_key: str) -> tuple[int, datetime]:
    parsed = _parse_dt(row.get(time_key))
    if parsed is None:
        return (0, _DT_MIN)
    return (1, parsed)


def _sort_desc(rows: list[dict[str, Any]], time_key: str) -> list[dict[str, Any]]:
    """저장소 반환 순서와 무관하게 최신순으로 정렬한다 (파싱 실패 행은 뒤로)."""
    return sorted(rows, key=lambda row: _desc_key(row, time_key), reverse=True)


def _paged_fetch(
    fetch_page: Any, time_key: str, since: str, until: str
) -> list[dict[str, Any]]:
    """limit-only 저장소를 limit 확장 재조회로 DESC 순회한다.

    윈도우 시작보다 오래된 행이 보이면(윈도우 커버) 중단하고, 소진되거나
    페이지 상한에 닿아도 중단한다. 기존 storage 시그니처는 수정하지 않는다.
    """
    start = _parse_dt(since)
    rows: list[dict[str, Any]] = []
    for page in range(1, _MAX_FETCH_PAGES + 1):
        batch = list(fetch_page(page * _FETCH_PAGE_SIZE))
        rows = batch
        if len(batch) < page * _FETCH_PAGE_SIZE:
            break
        if start is None:
            break
        parsed_times = [
            parsed
            for parsed in (_parse_dt(row.get(time_key)) for row in batch)
            if parsed is not None
        ]
        if parsed_times and min(parsed_times) < start:
            break
    return rows


def _event_line(event: dict[str, Any]) -> str:
    parts = [
        str(event.get("event_id", "")),
        str(event.get("timestamp", "")),
        str(event.get("camera_id", "")),
        str(event.get("event_type", "")),
        str(event.get("alert_level", "")),
        str(event.get("note", "")),
    ]
    return ("- event " + " ".join(p for p in parts if p)).rstrip()


def _bucket_line(bucket: dict[str, Any]) -> str:
    max_counts = bucket.get("max_counts") or {}
    compact = ",".join(f"{key}={max_counts[key]}" for key in sorted(max_counts))
    return (
        f"- bucket {bucket.get('camera_id', '')} "
        f"{bucket.get('bucket_start', '')} "
        f"samples={bucket.get('samples', 0)} max={compact}"
    ).rstrip()


def _bucket_summary_lines(buckets: list[dict[str, Any]]) -> list[str]:
    totals: dict[str, list[int]] = {}
    for bucket in buckets:
        camera_id = str(bucket.get("camera_id", ""))
        entry = totals.setdefault(camera_id, [0, 0])
        entry[0] += 1
        with suppress(ValueError, TypeError):
            entry[1] += int(bucket.get("samples", 0))
    return [f"- buckets {camera_id}: n={count} total={samples}" for camera_id, (count, samples) in totals.items()]


def _incident_line(incident: dict[str, Any]) -> str:
    return (
        f"- incident {incident.get('event_id', '')} "
        f"{incident.get('status', '')} "
        f"{incident.get('category', '')} "
        f"{incident.get('updated_at', '')}"
    ).rstrip()


class PeriodAggregator:
    """기간 집계기. 저장소 의존성은 생성자로 주입한다 (없으면 해당 섹션 생략)."""

    def __init__(
        self,
        event_repo: Any | None = None,
        observation_store: Any | None = None,
        incident_store: Any | None = None,
    ) -> None:
        self.event_repo = event_repo
        self.observation_store = observation_store
        self.incident_store = incident_store

    def build_context(self, since: str, until: str, max_tokens: int = 8000) -> str:
        """기간 컨텍스트를 토큰 상한 안에서 반환한다 (원문 절단 금지)."""
        if int(max_tokens) < 1:
            raise ValueError("max_tokens must be positive")
        budget = int(max_tokens)

        events = self._fetch_events(since, until)
        buckets = self._fetch_buckets(since, until)
        incidents = self._fetch_incidents(since, until)

        event_lines = [_event_line(event) for event in events]
        verbose_bucket_lines = [_bucket_line(bucket) for bucket in buckets]
        summary_bucket_lines = _bucket_summary_lines(buckets)
        incident_lines = [_incident_line(incident) for incident in incidents]

        header = f"# report-context\nsince: {since}\nuntil: {until}\n"
        if estimate_tokens(header) >= budget:
            return header

        def render(
            bucket_lines: list[str],
            kept_events: list[str],
            kept_incidents: list[str],
            buckets_dropped: bool = False,
        ) -> str:
            sections = [header]
            sections.append(f"## events ({len(kept_events)})\n")
            sections.append("\n".join(kept_events) + "\n" if kept_events else "(none)\n")
            sections.append("## buckets\n")
            sections.append("\n".join(bucket_lines) + "\n" if bucket_lines else "(none)\n")
            sections.append(f"## incidents ({len(kept_incidents)})\n")
            sections.append("\n".join(kept_incidents) + "\n" if kept_incidents else "(none)\n")
            omitted_events = len(event_lines) - len(kept_events)
            omitted_incidents = len(incident_lines) - len(kept_incidents)
            omitted_buckets = len(verbose_bucket_lines) if buckets_dropped else 0
            if omitted_events or omitted_incidents or omitted_buckets:
                sections.append("## omitted\n")
                if omitted_events:
                    sections.append(f"- events omitted: {omitted_events}\n")
                if omitted_incidents:
                    sections.append(f"- incidents omitted: {omitted_incidents}\n")
                if omitted_buckets:
                    sections.append(f"- buckets omitted: {omitted_buckets}\n")
            return "".join(sections)

        # 1) 전체 (버킷 상세 포함).
        context = render(verbose_bucket_lines, event_lines, incident_lines)
        if estimate_tokens(context) <= budget:
            return context
        # 2) 버킷 요약 우선 축소 (원문 이벤트는 유지).
        kept_events = list(event_lines)
        kept_incidents = list(incident_lines)
        context = render(summary_bucket_lines, kept_events, kept_incidents)
        if estimate_tokens(context) <= budget:
            return context
        # 3) 오래된 이벤트부터 통째로 제외 (newest-first이므로 뒤에서 제거).
        while kept_events and estimate_tokens(context) > budget:
            kept_events.pop()
            context = render(summary_bucket_lines, kept_events, kept_incidents)
        if estimate_tokens(context) <= budget:
            return context
        # 4) 오래된 인시던트 제외.
        while kept_incidents and estimate_tokens(context) > budget:
            kept_incidents.pop()
            context = render(summary_bucket_lines, kept_events, kept_incidents)
        if estimate_tokens(context) <= budget:
            return context
        # 5) 버킷 요약 제외 (헤더+비고만, best-effort).
        return render([], kept_events, kept_incidents, buckets_dropped=True)

    def _fetch_events(self, since: str, until: str) -> list[dict[str, Any]]:
        if self.event_repo is None:
            return []
        rows = list(self.event_repo.list(since=since, until=until, limit=1000))
        return _sort_desc(rows, "timestamp")

    def _fetch_buckets(self, since: str, until: str) -> list[dict[str, Any]]:
        if self.observation_store is None:
            return []
        rows = _paged_fetch(
            lambda limit: self.observation_store.query(limit=limit),
            "bucket_start",
            since,
            until,
        )
        in_window = [
            row for row in rows if _in_range(row.get("bucket_start"), since, until)
        ]
        return _sort_desc(in_window, "bucket_start")

    def _fetch_incidents(self, since: str, until: str) -> list[dict[str, Any]]:
        if self.incident_store is None:
            return []
        rows = _paged_fetch(
            lambda limit: self.incident_store.list(limit=limit),
            "updated_at",
            since,
            until,
        )
        in_window = [
            row for row in rows if _in_range(row.get("updated_at"), since, until)
        ]
        return _sort_desc(in_window, "updated_at")
