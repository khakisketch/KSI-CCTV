import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import { CameraManagementOverview } from "@/components/camera-management-overview";

const cameras = [
  { id: "healthy", name: "정문", location: "1층", enabled: true, managed: true },
  { id: "offline", name: "창고", location: "후문", enabled: true, managed: true },
  { id: "disabled", name: "주차장", enabled: false, managed: false },
];

const mockFetch = () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      connected: true,
      telemetry_status: "ok",
      cameras: { healthy: { connected: true, health_state: "receiving", seconds_since_last_frame: 0.1 } },
    }),
  } as Response));
};

describe("CameraManagementOverview", () => {
  beforeEach(() => {
    vi.stubGlobal("IntersectionObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a master-detail list with camera names and filter buttons", async () => {
    mockFetch();
    render(<CameraManagementOverview cameras={cameras} />);

    expect((await screen.findAllByText("정상 수신")).length).toBeGreaterThanOrEqual(1);

    expect(screen.getAllByText("정문").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("창고").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("주차장").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("listbox", { name: "카메라 목록" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "전체" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "상태 확인" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "관리" })[0]).toHaveAttribute("href", "/cameras/healthy");
  });

  it("filters cameras by health and management state", async () => {
    mockFetch();
    const user = userEvent.setup();
    render(<CameraManagementOverview cameras={cameras} />);

    expect((await screen.findAllByText("정상 수신")).length).toBeGreaterThanOrEqual(1);

    await user.click(screen.getByRole("button", { name: "비활성" }));
    expect(screen.queryAllByText("정문")).toHaveLength(0);
    expect(screen.queryAllByText("창고")).toHaveLength(0);
    expect(screen.getAllByText("주차장").length).toBeGreaterThanOrEqual(1);
  });
});
