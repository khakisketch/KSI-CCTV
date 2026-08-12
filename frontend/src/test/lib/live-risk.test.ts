import { describe, expect, it } from "vitest";

import { getLatestActiveCameraEvents } from "@/lib/live-risk";

describe("getLatestActiveCameraEvents", () => {
  it("does not keep a historical critical event highlighted after its incident is resolved", () => {
    const incidents = [
      { event_id: "resolved-critical", camera_id: "camera-1", status: "resolved" as const, source_event_alert_level: "critical", updated_at: "2026-07-30T10:00:00Z" },
      { event_id: "active-high", camera_id: "camera-2", status: "new" as const, source_event_alert_level: "high", updated_at: "2026-07-30T10:01:00Z" },
    ];

    const latest = getLatestActiveCameraEvents(incidents);

    expect(latest["camera-1"]).toBeUndefined();
    expect(latest["camera-2"]?.event_id).toBe("active-high");
  });

  it("keeps the critical active incident ahead of a lower severity event from the same camera", () => {
    const incidents = [
      { event_id: "active-high", camera_id: "camera-1", status: "new" as const, source_event_alert_level: "high", updated_at: "2026-07-30T10:02:00Z" },
      { event_id: "active-critical", camera_id: "camera-1", status: "acknowledged" as const, source_event_alert_level: "critical", updated_at: "2026-07-30T10:01:00Z" },
    ];

    const latest = getLatestActiveCameraEvents(incidents);

    expect(latest["camera-1"]?.event_id).toBe("active-critical");
  });
});
