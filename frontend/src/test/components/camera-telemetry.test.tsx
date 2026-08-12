import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CameraTelemetry, CameraTelemetryProvider } from "@/components/camera-telemetry";

describe("CameraTelemetryProvider", () => {
  it("fetches once for multiple cards and does not mark a no-frame stream healthy", async () => {
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        connected: true,
        telemetry_status: "ok",
        cameras: {
          waiting: { connected: true, is_stale: false, seconds_since_last_frame: null },
          healthy: { connected: true, is_stale: false, seconds_since_last_frame: 0.2 },
        },
      }),
    });
    vi.stubGlobal("fetch", fetchSpy);

    render(
      <CameraTelemetryProvider>
        <CameraTelemetry cameraId="waiting" />
        <CameraTelemetry cameraId="healthy" />
      </CameraTelemetryProvider>,
    );

    expect(await screen.findByText("첫 프레임 대기")).toBeInTheDocument();
    expect(screen.getByText("정상 수신")).toBeInTheDocument();
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
  });

  it("shows active tracking episodes and runtime failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        connected: true,
        telemetry_status: "ok",
        cameras: {
          tracked: {
            connected: true,
            is_stale: false,
            seconds_since_last_frame: 0.1,
            tracking: {
              active_tracks: 2,
              runtime_errors: 1,
              errors: 0,
              loitering: { active_episodes: 1 },
            },
          },
        },
      }),
    }));

    render(
      <CameraTelemetryProvider>
        <CameraTelemetry cameraId="tracked" />
      </CameraTelemetryProvider>,
    );

    expect(await screen.findByText("Track 2")).toBeInTheDocument();
    expect(screen.getByText("체류 관찰 1")).toBeInTheDocument();
    expect(screen.getByText("Tracking 오류")).toBeInTheDocument();
  });

  it("does not present retained camera data as healthy when telemetry source is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        connected: false,
        telemetry_status: "unavailable",
        cameras: {},
      }),
    }));

    render(
      <CameraTelemetryProvider>
        <CameraTelemetry cameraId="cam-01" />
      </CameraTelemetryProvider>,
    );

    expect(await screen.findByText("텔레메트리 없음 (탐지 데몬 미연결)")).toBeInTheDocument();
  });
});
