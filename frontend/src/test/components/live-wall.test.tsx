import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { LiveWall } from "@/components/live/live-wall";
import type { CameraItem, CameraTelemetryItem, EventItem } from "@/lib/engine";

// LiveWall 상태를 검증하는 테스트이므로 HLS 네트워크/타이머는 여기서 격리한다.
vi.mock("@/components/camera-live", () => ({
  CameraLive: ({ cameraId }: { cameraId: string }) => (
    <div data-testid="camera-live" data-camera-id={cameraId} />
  ),
}));

vi.mock("@/components/live/use-live-updates", () => ({
  useLiveUpdates: (
    telemetry: Record<string, CameraTelemetryItem>,
    latestEvents: Record<string, EventItem | undefined>,
  ) => ({ telemetry, latestEvents, telemetrySource: { status: "ok", stale: false } }),
}));

function makeLocalStorageMock() {
  const store = new Map<string, string>();
  return {
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => store.set(key, value)),
    removeItem: vi.fn((key: string) => store.delete(key)),
    clear: vi.fn(() => store.clear()),
  };
}

function makeCameras(n: number): CameraItem[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `cam-${String(i + 1).padStart(2, "0")}`,
    name: `카메라 ${i + 1}`,
    location: `위치 ${i + 1}`,
    enabled: true,
    managed: true,
    enabled_events: [],
  }));
}

function healthyTelemetry(ids: string[]): Record<string, CameraTelemetryItem> {
  return Object.fromEntries(
    ids.map((id) => [
      id,
      {
        connected: true,
        fps: 30,
        is_stale: false,
        health_state: "receiving",
        seconds_since_last_frame: 0.1,
      },
    ]),
  );
}

describe("LiveWall", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", makeLocalStorageMock());
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} })));
    vi.stubGlobal("IntersectionObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("4분할은 카메라 8대에서 첫 페이지 4대만 표시하고 다음 페이지로 이동한다", async () => {
    const cameras = makeCameras(8);
    const telemetry = healthyTelemetry(cameras.map((c) => c.id!));
    render(<LiveWall site="test" cameras={cameras} telemetry={telemetry} latestEvents={{}} />);

    expect(await screen.findByText("1 / 2")).toBeInTheDocument();
    expect(screen.getByText("카메라 1")).toBeInTheDocument();
    expect(screen.getByText("카메라 4")).toBeInTheDocument();
    expect(screen.queryByText("카메라 5")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "다음 페이지" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByText("카메라 5")).toBeInTheDocument();
    expect(screen.queryByText("카메라 1")).not.toBeInTheDocument();
  });

  it("9분할에서도 자동/수동 재생을 합쳐 4개 스트림으로 제한한다", async () => {
    const cameras = makeCameras(9);
    const telemetry = healthyTelemetry(cameras.map((c) => c.id!));
    render(<LiveWall site="test" cameras={cameras} telemetry={telemetry} latestEvents={{}} />);

    await screen.findByText("1 / 3");
    fireEvent.click(screen.getByRole("button", { name: "9분할" }));
    expect(screen.getByText("카메라 9")).toBeInTheDocument();
    expect(screen.getAllByTestId("camera-live")).toHaveLength(4);

    fireEvent.click(screen.getByRole("button", { name: "카메라 5 라이브 보기" }));
    expect(screen.getAllByTestId("camera-live")).toHaveLength(4);
  });

  it("위험도 우선 정렬에서 critical 카메라를 첫 타일로 올린다", async () => {
    const cameras = makeCameras(4);
    const telemetry = healthyTelemetry(cameras.map((c) => c.id!));
    const latestEvents: Record<string, EventItem | undefined> = {
      "cam-03": {
        event_id: "ev-1",
        camera_id: "cam-03",
        event_type: "intrusion",
        alert_level: "critical",
        timestamp: "2026-07-01T00:00:00Z",
        confidence: 0.95,
      },
    };
    const { container } = render(
      <LiveWall site="test" cameras={cameras} telemetry={telemetry} latestEvents={latestEvents} />,
    );

    expect(await screen.findByText("긴급")).toBeInTheDocument();
    const firstTile = container.querySelector(".grid.gap-2 > div");
    expect(firstTile).toHaveTextContent("카메라 3");
  });

  it("사건에서 전달된 카메라를 즉시 포커스하고 사건 컨텍스트를 표시한다", async () => {
    const cameras = makeCameras(4);
    const telemetry = healthyTelemetry(cameras.map((camera) => camera.id!));
    render(
      <LiveWall
        site="test"
        cameras={cameras}
        telemetry={telemetry}
        latestEvents={{}}
        initialFocusCameraId="cam-03"
        incidentEventId="event-3"
        incident={{ event_id: "event-3", camera_id: "cam-03", status: "acknowledged", updated_at: "2026-07-01T00:00:00Z" }}
        incidentEvent={{ event_id: "event-3", camera_id: "cam-03", description: "제한 구역 진입", alert_level: "critical" }}
      />,
    );

    expect(await screen.findByTestId("camera-live")).toHaveAttribute("data-camera-id", "cam-03");
    expect(screen.getByText("사건 확인 중")).toBeInTheDocument();
    expect(screen.getByText("제한 구역 진입")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "사건 상세로 돌아가기" })).toHaveAttribute("href", "/incidents/event-3");
  });

  it("오탐 가능성이 높은 critical 이벤트는 위험 강조와 위험도 우선 정렬에서 제외한다", async () => {
    const cameras = makeCameras(4);
    const telemetry = healthyTelemetry(cameras.map((c) => c.id!));
    const latestEvents: Record<string, EventItem | undefined> = {
      "cam-03": {
        event_id: "ev-1",
        camera_id: "cam-03",
        event_type: "intrusion",
        alert_level: "critical",
        alert_state: "likely_false_positive",
      },
    };
    const { container } = render(
      <LiveWall site="test" cameras={cameras} telemetry={telemetry} latestEvents={latestEvents} />,
    );

    await screen.findByText("카메라 1");
    const firstTile = container.querySelector(".grid.gap-2 > div");
    expect(firstTile).toHaveTextContent("카메라 1");
    expect(screen.queryByText("긴급")).not.toBeInTheDocument();
  });

  it("검증 불가 이벤트는 legacy 오탐 상태가 있어도 위험 강조를 유지한다", async () => {
    const cameras = makeCameras(4);
    const telemetry = healthyTelemetry(cameras.map((c) => c.id!));
    render(<LiveWall site="test" cameras={cameras} telemetry={telemetry} latestEvents={{
      "cam-03": { event_id: "ev-1", camera_id: "cam-03", alert_level: "critical", alert_state: "likely_false_positive", verification_state: "unverified" },
    }} />);

    expect(await screen.findByText("긴급")).toBeInTheDocument();
    expect(screen.getByText("검증 불가")).toBeInTheDocument();
  });

  it("9분할과 16분할은 휴대폰용 두 열 클래스를 유지한다", async () => {
    const cameras = makeCameras(16);
    const telemetry = healthyTelemetry(cameras.map((c) => c.id!));
    const { container } = render(<LiveWall site="test" cameras={cameras} telemetry={telemetry} latestEvents={{}} />);

    await screen.findByText("1 / 4");
    fireEvent.click(screen.getByRole("button", { name: "9분할" }));
    expect(container.querySelector(".grid.gap-2")).toHaveClass("grid-cols-2", "sm:grid-cols-3");
    fireEvent.click(screen.getByRole("button", { name: "16분할" }));
    expect(container.querySelector(".grid.gap-2")).toHaveClass("grid-cols-2", "sm:grid-cols-4");
  });

  it("기본 순서에서 키보드/터치 이동 버튼으로 사용자 순서를 저장한다", async () => {
    const cameras = makeCameras(2);
    const telemetry = healthyTelemetry(cameras.map((c) => c.id!));
    const storage = window.localStorage as unknown as { setItem: ReturnType<typeof vi.fn> };
    render(<LiveWall site="test" cameras={cameras} telemetry={telemetry} latestEvents={{}} />);

    await screen.findByText("카메라 1");
    fireEvent.change(screen.getByLabelText("정렬"), { target: { value: "default" } });
    const moveNext = screen
      .getAllByRole("button", { name: "다음 위치로 이동" })
      .find((button) => !button.hasAttribute("disabled"));
    expect(moveNext).toBeDefined();
    fireEvent.click(moveNext!);

    expect(storage.setItem).toHaveBeenCalledWith(
      "ksi-cctv:live:presets:test",
      expect.stringContaining('"order":["cam-02","cam-01"]'),
    );
  });
});
