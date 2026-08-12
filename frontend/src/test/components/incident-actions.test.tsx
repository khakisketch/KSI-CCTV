import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IncidentActions, IncidentStatusBadge } from "@/components/incident-actions";

describe("IncidentActions", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("acknowledges an event through the same-origin incident route", async () => {
    const onUpdated = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        event_id: "event-1",
        status: "acknowledged",
        disposition: null,
        updated_at: "2026-07-28T00:00:00Z",
        actions: [{
          previous_status: "new",
          status: "acknowledged",
          disposition: null,
          note: "현장 확인 요청",
          created_at: "2026-07-28T00:00:00Z",
        }],
      }),
    } as unknown as Response));
    const user = userEvent.setup();
    render(<IncidentActions eventId="event-1" incident={{ event_id: "event-1", status: "new", updated_at: "2026-07-28T00:00:00Z" }} onUpdated={onUpdated} />);

    await user.type(screen.getByLabelText("조치 메모"), "현장 확인 요청");
    await user.click(screen.getByRole("button", { name: "확인" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/incidents/event-1/actions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ status: "acknowledged", disposition: undefined, note: "현장 확인 요청" }),
      }),
    ));
    expect(onUpdated).toHaveBeenCalledWith(expect.objectContaining({ status: "acknowledged" }));
    expect(screen.getByRole("list", { name: "조치 이력" })).toHaveTextContent("현장 확인 요청");
  });

  it("requires a disposition in the resolution request", async () => {
    const onUpdated = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        event_id: "event-1",
        status: "resolved",
        disposition: "false_positive",
        updated_at: "2026-07-28T00:00:00Z",
      }),
    } as unknown as Response));
    const user = userEvent.setup();
    render(<IncidentActions eventId="event-1" incident={{ event_id: "event-1", status: "new", updated_at: "2026-07-28T00:00:00Z" }} onUpdated={onUpdated} />);

    await user.selectOptions(screen.getByLabelText("종결 판정"), "false_positive");
    await user.click(screen.getByRole("button", { name: "종결" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/incidents/event-1/actions",
      expect.objectContaining({
        body: JSON.stringify({ status: "resolved", disposition: "false_positive", note: "" }),
      }),
    ));
  });

  it("does not present a missing incident as a new incident", () => {
    render(<IncidentActions eventId="event-1" onUpdated={vi.fn()} />);

    expect(screen.getByText("사건 미생성")).toBeInTheDocument();
    expect(screen.getByText("운영자 처리 대상 사건이 아직 생성되지 않았습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "확인" })).not.toBeInTheDocument();
    render(<IncidentStatusBadge />);
    expect(screen.getAllByText("사건 미생성")).toHaveLength(2);
  });
});
