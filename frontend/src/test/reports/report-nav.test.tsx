import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { AppShell } from "@/components/app-shell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/reports",
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "dark", setTheme: vi.fn() }),
}));

describe("reports navigation entry (P3-T4 R5)", () => {
  it("admin 사이드바에 /reports 링크를 노출한다", () => {
    render(<AppShell authMode="local" role="admin"><div>content</div></AppShell>);
    const sidebar = screen.getByRole("navigation", { name: "주요 탐색" });
    expect(within(sidebar).getByRole("link", { name: "정기 보고서" })).toHaveAttribute("href", "/reports");
  });

  it("viewer 사이드바에도 /reports 링크를 노출한다 (조회 등급)", () => {
    render(<AppShell authMode="local" role="viewer"><div>content</div></AppShell>);
    const sidebar = screen.getByRole("navigation", { name: "주요 탐색" });
    expect(within(sidebar).getByRole("link", { name: "정기 보고서" })).toHaveAttribute("href", "/reports");
    expect(within(sidebar).queryByText("관리자")).not.toBeInTheDocument();
  });
});
