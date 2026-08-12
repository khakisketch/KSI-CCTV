import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuditTable } from "@/components/audit-table";
import type { AuditEntry } from "@/lib/engine";

const entries: AuditEntry[] = [
  {
    timestamp: "2026-07-31T01:00:00Z",
    event: "camera.updated",
    source: "installer",
    camera_id: "cam-1",
    revision: 8,
  },
  {
    timestamp: "2026-07-31T01:01:00Z",
    event: "pipeline.started",
    source: "system",
    options: { mode: "policy" },
  },
];

describe("AuditTable", () => {
  it("selects an entry by keyboard and renders its structured details", async () => {
    const user = userEvent.setup();
    render(<AuditTable entries={entries} connected />);

    const first = screen.getByRole("row", { name: /camera.updated/ });
    const second = screen.getByRole("row", { name: /pipeline.started/ });
    expect(first).toHaveAttribute("aria-selected", "true");

    second.focus();
    await user.keyboard("{Enter}");

    expect(second).toHaveAttribute("aria-selected", "true");
    expect(first).toHaveAttribute("aria-selected", "false");
    expect(screen.getAllByText("options").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/"mode": "policy"/).length).toBeGreaterThan(0);
  });

  it("distinguishes an empty audit log from an unavailable engine", () => {
    const { rerender } = render(<AuditTable entries={[]} connected />);
    expect(screen.getByText("감사 기록이 없습니다.")).toBeInTheDocument();

    rerender(<AuditTable entries={[]} connected={false} />);
    expect(screen.getByText("엔진 API에 연결되지 않았습니다.")).toBeInTheDocument();
  });
});
