import { describe, expect, it } from "vitest";

import { isCameraReceiving } from "@/lib/engine";

describe("isCameraReceiving", () => {
  it("requires an actual first frame before reporting healthy ingest", () => {
    expect(isCameraReceiving({ connected: true, is_stale: false, seconds_since_last_frame: null })).toBe(false);
    expect(isCameraReceiving({ connected: true, is_stale: false, seconds_since_last_frame: 0.2 })).toBe(true);
  });

  it("rejects disconnected and stale streams", () => {
    expect(isCameraReceiving({ connected: false, is_stale: false, seconds_since_last_frame: 0.1 })).toBe(false);
    expect(isCameraReceiving({ connected: true, is_stale: true, seconds_since_last_frame: 0.1 })).toBe(false);
    expect(isCameraReceiving({ connected: true, is_stale: false, seconds_since_last_frame: 0.1, health_state: "stale" })).toBe(false);
    expect(isCameraReceiving({ connected: true, is_stale: false, seconds_since_last_frame: 0.1, health_state: "disconnected" })).toBe(false);
    expect(isCameraReceiving({ connected: true, is_stale: false, seconds_since_last_frame: 0.1, health_state: "receiving" })).toBe(true);
  });
});
