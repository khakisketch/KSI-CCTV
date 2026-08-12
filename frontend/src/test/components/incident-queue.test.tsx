import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import { IncidentQueue } from "@/components/incident-queue";

describe("IncidentQueue", () => {
  it("distinguishes an unavailable incident store from an empty queue", () => {
    const { rerender } = render(<IncidentQueue available={false} items={[]} />);
    expect(screen.getByText(/사건 저장소를 사용할 수 없습니다/)).toBeInTheDocument();

    rerender(<IncidentQueue available items={[]} />);
    expect(screen.getByText("조건에 맞는 미종결 사건이 없습니다.")).toBeInTheDocument();
  });

  it("joins camera names and exact event evidence in the non-resolved queue", () => {
    render(<IncidentQueue available items={[{ incident: { event_id: "event-1", camera_id: "camera-1", category: "safety", status: "new", updated_at: "2026-07-29T00:00:00Z" }, cameraName: "정문 카메라", event: { event_id: "event-1", description: "침입 증거", timestamp: "2026-07-29T00:00:00Z" } }]} />);

    expect(screen.getAllByText("정문 카메라").length).toBeGreaterThan(0);
    expect(screen.getAllByText("침입 증거").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /상세/ })[0]).toHaveAttribute("href", "/incidents/event-1");
  });

  it("uses the incident summary without loading event evidence for every queue item", () => {
    render(<IncidentQueue available items={[{ incident: { event_id: "event-2", camera_id: "camera-2", category: "safety", source_event_type: "fire", status: "new", updated_at: "2026-07-29T00:00:00Z" }, cameraName: "창고 카메라", event: null }]} />);

    expect(screen.getAllByText("fire").length).toBeGreaterThan(0);
  });
});
