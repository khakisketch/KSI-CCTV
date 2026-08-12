import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import { IncidentDetail } from "@/components/incident-detail";

describe("IncidentDetail", () => {
  it("keeps operator handling available when the source event is missing", () => {
    render(<IncidentDetail initialIncident={{ event_id: "event-1", camera_id: "camera-1", category: "camera_health", status: "acknowledged", updated_at: "2026-07-29T00:00:00Z", recovered_at: "2026-07-29T00:01:00Z" }} event={null} cameraName="정문 카메라" />);

    expect(screen.getByText(/증거 이미지 없음/)).toBeInTheDocument();
    expect(screen.getByText(/종결 전 운영자 확인이 필요합니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "대응 시작" })).toBeInTheDocument();
  });
});
