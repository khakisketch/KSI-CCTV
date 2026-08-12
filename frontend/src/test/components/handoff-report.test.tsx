import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HandoffReport } from "@/components/handoff-report";

const report = {
  revision: 4,
  generated_at: "2026-07-21T00:00:00Z",
  completed_at: "2026-07-21T00:00:00Z",
  ready: true,
  site: { name: "Factory A", location: "Seoul", installer: "Installer" },
  progress: { passed: 5, total: 5, percent: 100 },
  checks: [],
  cameras: [],
  handoff: { customer_name: "", notes: "", accepted_at: null },
};

describe("HandoffReport", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("records a named customer acceptance against the report revision", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({
        revision: 5,
        handoff: {
          customer_name: "Customer Manager",
          notes: "Reviewed",
          accepted_at: "2026-07-21T01:00:00Z",
        },
      }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<HandoffReport report={report} />);

    await user.type(screen.getByLabelText("고객 담당자 이름"), "Customer Manager");
    await user.type(screen.getByLabelText("인수 메모 (선택)"), "Reviewed");
    await user.click(screen.getByLabelText(/설치 결과와 미해결 항목/));
    await user.click(screen.getByRole("button", { name: "인수 확인 기록" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({
      customer_name: "Customer Manager",
      notes: "Reviewed",
      expected_revision: 4,
    });
    expect(await screen.findByText("현장 인수 확인을 기록했습니다.")).toBeInTheDocument();
    expect(screen.getByText("Customer Manager")).toBeInTheDocument();
  });
});
