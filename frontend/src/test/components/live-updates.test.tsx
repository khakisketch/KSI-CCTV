import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useLiveUpdates } from "@/components/live/use-live-updates";
import type { CameraTelemetryItem, EventItem } from "@/lib/engine";

class MockEventSource {
  static instances: MockEventSource[] = [];
  listeners = new Map<string, Array<(event: MessageEvent) => void>>();
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;

  constructor(public readonly url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {}

  dispatch(type: string, data: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function Probe({ initialTelemetry, initialEvents }: {
  initialTelemetry: Record<string, CameraTelemetryItem>;
  initialEvents: Record<string, EventItem | undefined>;
}) {
  const state = useLiveUpdates(initialTelemetry, initialEvents, { status: "ok" });
  return (
    <output>
      {state.telemetrySource.status}:{String(state.telemetrySource.stale)}:
      {Object.values(state.latestEvents).filter(Boolean).map((event) => event?.event_id).join(",")}
    </output>
  );
}

describe("useLiveUpdates", () => {
  beforeEach(() => {
    MockEventSource.instances.length = 0;
    vi.stubGlobal("EventSource", MockEventSource);
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url === "/api/cameras/telemetry") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            connected: true,
            telemetry_status: "ok",
            generated_at: "2026-07-26T00:00:00Z",
            cameras: {
              "cam-01": { connected: true, health_state: "receiving", seconds_since_last_frame: 0.1 },
            },
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          available: true,
          incidents: [{
            event_id: "active-incident",
            camera_id: "cam-02",
            status: "new",
            source_event_alert_level: "critical",
            updated_at: "2026-07-26T00:00:00Z",
          }],
        }),
      } as Response);
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("uses native EventSource reconnection and reconciles cursor gaps through unresolved incidents", async () => {
    render(<Probe initialTelemetry={{}} initialEvents={{}} />);

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = MockEventSource.instances[0];
    act(() => source.onerror?.());
    // 수동 close/recreate가 없으므로 browser가 가진 Last-Event-ID가 유지된다.
    expect(MockEventSource.instances).toHaveLength(1);

    act(() => source.dispatch("reconciliation-required", { reason: "cursor-unavailable" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/incidents?unresolved=true&limit=1000",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    expect(await screen.findByText(/active-incident/)).toBeInTheDocument();
  });

  it("evicts a resolved incident highlight during lifecycle reconciliation", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url === "/api/cameras/telemetry") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ connected: true, telemetry_status: "ok", cameras: {} }) } as Response);
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ available: true, incidents: [] }) } as Response);
    }));

    render(<Probe initialTelemetry={{}} initialEvents={{ "cam-01": { event_id: "resolved-event", camera_id: "cam-01", alert_level: "critical" } }} />);
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    act(() => MockEventSource.instances[0].dispatch("reconciliation-required", { reason: "incident-updated" }));
    await waitFor(() => expect(screen.queryByText(/resolved-event/)).not.toBeInTheDocument());
  });
});
