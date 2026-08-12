import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TrackingOverlay } from "@/components/tracking-overlay";

describe("TrackingOverlay", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders bounded track, zone, and stale state from the authenticated proxy", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        camera_id: "cam_001",
        timestamp: "2026-07-20T10:00:00",
        age_sec: 4,
        stale: true,
        frame: { width: 1920, height: 1080 },
        events: ["line_crossing"],
        tracks: [{
          track_id: 7,
          class_name: "person",
          confidence: 0.9,
          bbox: [0.1, 0.2, 0.3, 0.8],
          trajectory: [[0.15, 0.7], [0.2, 0.8]],
          first_seen: "2026-07-20T09:59:58",
          last_seen: "2026-07-20T10:00:00",
        }],
        zones: [{
          id: "line",
          type: "line",
          points: [[0.1, 0.5], [0.9, 0.5]],
          direction: "positive",
        }],
      }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    render(<TrackingOverlay cameraId="cam_001" />);

    expect(await screen.findByLabelText("Tracking overlay")).toBeInTheDocument();
    expect(screen.getByText("#7 person 90%")).toBeInTheDocument();
    expect(screen.getByText("Tracking 지연")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/cameras/cam_001/tracking",
      expect.objectContaining({ cache: "no-store" }),
    ));
  });
});
