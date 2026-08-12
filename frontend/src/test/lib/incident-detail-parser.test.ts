import { describe, expect, it } from "vitest";

import { parseIncidentDetail } from "@/lib/engine";

describe("parseIncidentDetail", () => {
  it("accepts a valid detail response and drops malformed action history", () => {
    const result = parseIncidentDetail({ incident: { event_id: "event-1", status: "acknowledged", updated_at: "2026-07-29T00:00:00Z", category: "safety", source_event_alert_level: "critical" }, actions: [{ previous_status: "new", status: "acknowledged", note: "확인", created_at: "2026-07-29T00:00:00Z" }, { status: "invalid" }] });

    expect(result?.incident.event_id).toBe("event-1");
    expect(result?.incident.source_event_alert_level).toBe("critical");
    expect(result?.actions).toHaveLength(1);
  });

  it("rejects an invalid incident state", () => {
    expect(parseIncidentDetail({ incident: { event_id: "event-1", status: "unknown", updated_at: "2026-07-29T00:00:00Z" } })).toBeNull();
  });
});
