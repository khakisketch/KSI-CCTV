import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import { SignalWorkspace } from "@/components/signal-workspace";
import type { CameraZonePoint } from "@/lib/cameras";

const METRICS = {
  cameraHealth: "카메라 0/1",
  pipeline: "파이프라인 가동",
  installProgress: "설치 100%",
  siteName: "테스트 현장",
};

describe("SignalWorkspace", () => {
  beforeEach(() => {
    vi.stubGlobal("IntersectionObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({}),
    } as unknown as Response));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("distinguishes an unavailable incident store from an empty queue", () => {
    const { rerender } = render(<SignalWorkspace available={false} initialItems={[]} metrics={METRICS} />);
    expect(screen.getByText(/사건 저장소를 사용할 수 없습니다/)).toBeInTheDocument();

    rerender(<SignalWorkspace available initialItems={[]} metrics={METRICS} />);
    expect(screen.getByText("미종결 사건 없음")).toBeInTheDocument();
  });

  it("renders the incident queue and auto-selects the first incident", () => {
    render(<SignalWorkspace available initialItems={[{
      incident: { event_id: "evt-1", camera_id: "cam-1", category: "safety", status: "new", updated_at: "2026-07-30T00:00:00Z" },
      event: { event_id: "evt-1", description: "제한 구역 진입", timestamp: "2026-07-30T00:00:00Z", alert_level: "critical" },
      camera: { id: "cam-1", name: "창고 A-03", zones: [{ id: "z1", type: "polygon", points: [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }] satisfies CameraZonePoint[] }], enabled_events: ["intrusion"] },
      cameraName: "창고 A-03",
      telemetry: { connected: true, health_state: "receiving", seconds_since_last_frame: 0.2 },
    }]} metrics={METRICS} />);

    expect(screen.getByText("사건 대기열")).toBeInTheDocument();
    expect(screen.getAllByText("제한 구역 진입").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "사건 확인" })).toBeInTheDocument();
  });

  it("records acknowledgement and shows success feedback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        event_id: "evt-1",
        status: "acknowledged",
        updated_at: "2026-07-30T00:01:00Z",
        actions: [{ previous_status: "new", status: "acknowledged", note: "", created_at: "2026-07-30T00:01:00Z" }],
      }),
    } as unknown as Response));

    const user = userEvent.setup();
    render(<SignalWorkspace available initialItems={[{
      incident: { event_id: "evt-1", camera_id: "cam-1", category: "safety", status: "new", updated_at: "2026-07-30T00:00:00Z" },
      event: { event_id: "evt-1", description: "제한 구역 진입", timestamp: "2026-07-30T00:00:00Z", alert_level: "critical" },
      camera: null,
      cameraName: "창고 A-03",
    }]} metrics={METRICS} />);

    await user.click(screen.getByRole("button", { name: "사건 확인" }));
    expect(await screen.findByText("사건을 확인 상태로 기록했습니다.")).toBeInTheDocument();
  });

  it("shows error feedback when incident action fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      json: vi.fn().mockResolvedValue({ detail: "충돌: 다른 운영자가 변경했습니다." }),
    } as unknown as Response));

    const user = userEvent.setup();
    render(<SignalWorkspace available initialItems={[{
      incident: { event_id: "evt-1", camera_id: "cam-1", category: "safety", status: "new", updated_at: "2026-07-30T00:00:00Z" },
      event: { event_id: "evt-1", description: "화재 감지", timestamp: "2026-07-30T00:00:00Z" },
      camera: null,
      cameraName: "카메라 1",
    }]} metrics={METRICS} />);

    await user.click(screen.getByRole("button", { name: "사건 확인" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/충돌/);
  });

  it("prioritizes critical incidents with persisted source severity", () => {
    const { container } = render(<SignalWorkspace available initialItems={[
      {
        incident: { event_id: "legacy-critical", camera_id: "cam-1", status: "new", source_event_alert_level: "critical", updated_at: "2026-07-30T10:00:00Z" },
        event: null,
        camera: null,
        cameraName: "카메라 1",
      },
      {
        incident: { event_id: "recent-low", camera_id: "cam-2", status: "new", updated_at: "2026-07-30T10:02:00Z" },
        event: { event_id: "recent-low", alert_level: "low", description: "최근 저위험 이벤트", timestamp: "2026-07-30T10:02:00Z" },
        camera: null,
        cameraName: "카메라 2",
      },
    ]} metrics={METRICS} />);

    const options = container.querySelectorAll("[role='option']");
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(options[0].textContent).toContain("카메라 1");
  });
});
