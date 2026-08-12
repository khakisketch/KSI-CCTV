import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { csvCell, EventsLive } from "@/components/events-live";
import type { EventItem } from "@/lib/engine";

function createMockEventSource() {
  const listeners = new Map<string, ((e: MessageEvent) => void)[]>();
  return {
    addEventListener: vi.fn((type: string, fn: (e: MessageEvent) => void) => {
      listeners.set(type, [...(listeners.get(type) ?? []), fn]);
    }),
    removeEventListener: vi.fn(),
    close: vi.fn(),
    onopen: null as (() => void) | null,
    onerror: null as (() => void) | null,
    dispatch(type: string, data: unknown) {
      const event = new MessageEvent(type, { data: JSON.stringify(data) });
      (listeners.get(type) ?? []).forEach((fn) => fn(event));
    },
  };
}

const MOCK_EVENTS_1: EventItem[] = [
  {
    event_id: "ev-1",
    camera_id: "cam-01",
    event_type: "intrusion",
    alert_level: "critical",
    timestamp: "2026-06-17T09:00:00Z",
    confidence: 0.91,
    description: "침입 탐지",
  },
  {
    event_id: "ev-2",
    camera_id: "cam-02",
    event_type: "motion",
    alert_level: "low",
    timestamp: "2026-06-17T09:01:00Z",
    confidence: 0.72,
    description: "움직임 감지",
  },
];

const MOCK_EVENTS_2: EventItem[] = [
  ...MOCK_EVENTS_1,
  {
    event_id: "ev-3",
    camera_id: "cam-03",
    event_type: "fire",
    alert_level: "high",
    timestamp: "2026-06-17T09:02:00Z",
    confidence: 0.88,
    description: "화재 탐지",
  },
];

describe("EventsLive", () => {
  let MockEventSource: ReturnType<typeof createMockEventSource>;

  beforeEach(() => {
    MockEventSource = createMockEventSource();
    vi.stubGlobal("EventSource", vi.fn(() => MockEventSource));
    vi.stubGlobal("IntersectionObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ events: MOCK_EVENTS_2 }) } as Response)));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("빈 initialEvents + connected=false일 때 미연결 안내 문구를 렌더한다", () => {
    render(<EventsLive initialEvents={[]} connected={false} />);

    expect(screen.getByText("엔진 API에 연결되지 않았습니다.")).toBeInTheDocument();
    expect(EventSource).not.toHaveBeenCalled();
  });

  it("빈 initialEvents + connected=true일 때 '조건에 맞는 이벤트가 없습니다.'를 렌더한다", () => {
    render(<EventsLive initialEvents={[]} connected={true} />);

    expect(screen.getByText("조건에 맞는 이벤트가 없습니다.")).toBeInTheDocument();
    expect(EventSource).toHaveBeenCalledWith("/api/events/stream");
  });

  it("이벤트가 있을 때 테이블에 camera_id, event_type, alert_level을 렌더한다", () => {
    render(<EventsLive initialEvents={MOCK_EVENTS_1} connected={false} />);

    expect(screen.getAllByText("cam-01").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("cam-02").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("intrusion").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("motion").length).toBeGreaterThanOrEqual(1);
  });

  it("alert_level이 critical일 때 destructive variant Badge를 렌더한다", () => {
    render(<EventsLive initialEvents={MOCK_EVENTS_1.slice(0, 1)} connected={false} />);

    const badges = screen.getAllByText("critical");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  it("이벤트 확인 상태를 inspector에 표시한다", () => {
    render(<EventsLive initialEvents={[{ ...MOCK_EVENTS_1[0], alert_state: "likely_false_positive" }]} connected={false} />);

    expect(screen.getAllByText("오탐 가능").length).toBeGreaterThanOrEqual(1);
  });

  it("검증 상태와 안전한 판정 근거를 inspector에 표시한다", () => {
    render(<EventsLive initialEvents={[{
      ...MOCK_EVENTS_1[0], verification_state: "unverified", verification_mode: "three_frame", frame_count: 3,
      confirm_count: 1, reject_count: 0, error_count: 2, verification_quorum: 2, verification_model: "local-vlm",
      verification_model_digest: "a".repeat(64), verification_reason_codes: ["VLM_TIMEOUT"],
      source_models: ["yolo", "pose", "stgcn"], rule_name: "stgcn_action",
      metadata: { track_id: 17, action: "fall", confidence: 0.92, model_names: ["pose", "stgcn"] },
    }]} connected={false} />);

    expect(screen.getAllByText("검증 불가").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/감지 모델: yolo, pose, stgcn/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/행동: fall · 92% · 추적 17/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/aaaaaaaa\.\.\.aaaaaa/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/사유: VLM_TIMEOUT/).length).toBeGreaterThanOrEqual(1);
  });

  it("tracking metadata가 있으면 inspector에 표시한다", async () => {
    render(<EventsLive initialEvents={[{ ...MOCK_EVENTS_1[0], evidence_image: "20260720/cam-01/loitering_120000.jpg", metadata: { track_id: 17, zone_id: "loitering", direction_label: "화살표 방향", dwell_sec: 61.2 } }]} connected={false} />);

    expect(screen.getAllByText("추적 ID: 17").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("영역: loitering").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("방향: 화살표 방향").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("체류: 61.2초").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("link", { name: "라이브 보기" })[0]).toHaveAttribute(
      "href",
      "/live?camera=cam-01&event=ev-1",
    );
  });

  it("자동 새로고침 토글 버튼이 렌더된다", () => {
    render(<EventsLive initialEvents={[]} connected={true} />);

    expect(screen.getByRole("switch")).toBeInTheDocument();
    expect(screen.getByText("꺼짐")).toBeInTheDocument();
  });

  it("자동 새로고침을 켜면 /api/events를 fetch로 갱신하고 SSE 실시간 이벤트를 병합한다", async () => {
    render(<EventsLive initialEvents={MOCK_EVENTS_1} connected={true} />);

    const toggle = screen.getByRole("switch");
    await userEvent.click(toggle);

    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/events?limit=200",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ), { timeout: 1000 });

    act(() => MockEventSource.dispatch("alert", MOCK_EVENTS_2[2]));
    await vi.waitFor(() => {
      expect(screen.getAllByText("cam-03").length).toBeGreaterThanOrEqual(1);
    }, { timeout: 2000 });
  });

  it("자동 새로고침 토글을 Enter 키로 조작할 수 있다", async () => {
    render(<EventsLive initialEvents={[]} connected={true} />);

    const toggle = screen.getByRole("switch");
    toggle.focus();
    await userEvent.keyboard("{Enter}");

    expect(screen.getByText("켜짐")).toBeInTheDocument();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/events?limit=200",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ), { timeout: 1000 });
  });

  it("자동 새로고침 토글을 Space 키로 조작할 수 있다", async () => {
    render(<EventsLive initialEvents={[]} connected={true} />);

    const toggle = screen.getByRole("switch");
    toggle.focus();
    await userEvent.keyboard(" ");

    expect(screen.getByText("켜짐")).toBeInTheDocument();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/events?limit=200",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ), { timeout: 1000 });
  });

  it("동일 event_id의 SSE lifecycle update는 최신 payload로 upsert한다", async () => {
    render(<EventsLive initialEvents={MOCK_EVENTS_1.slice(0, 1)} connected={true} />);

    act(() => {
      MockEventSource.dispatch("alert", {
        ...MOCK_EVENTS_1[0],
        alert_state: "confirmed",
        description: "확정 침입 탐지",
      });
    });

    await vi.waitFor(() => {
      expect(screen.queryAllByText("확정 침입 탐지").length).toBeGreaterThanOrEqual(1);
    }, { timeout: 2000 });
  });

  it("SSE cursor gap은 REST reconciliation으로 복구한다", async () => {
    render(<EventsLive initialEvents={MOCK_EVENTS_1} connected={true} />);

    act(() => MockEventSource.dispatch("reconciliation-required", { reason: "cursor-unavailable" }));

    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/events?limit=200",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    expect(await screen.findByText("cam-03")).toBeInTheDocument();
  });

  it("CSV cells neutralize spreadsheet formulas", () => {
    expect(csvCell('=HYPERLINK("https://attacker.invalid")')).toBe(`"'=HYPERLINK(""https://attacker.invalid"")"`);
    expect(csvCell(" normal text")).toBe('" normal text"');
  });
});
