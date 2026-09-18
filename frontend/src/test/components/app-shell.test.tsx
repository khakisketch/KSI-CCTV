import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/app-shell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/cameras",
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "dark", setTheme: vi.fn() }),
}));

describe("AppShell", () => {
  it("keeps incidents in primary mobile navigation and exposes event history from More", async () => {
    const user = userEvent.setup();
    render(<AppShell authMode="local"><div>content</div></AppShell>);

    const mobileNav = screen.getByRole("navigation", { name: "주요 메뉴" });
    expect(within(mobileNav).getAllByRole("link")).toHaveLength(4);
    expect(within(mobileNav).getByRole("link", { name: "Signal" })).toHaveAttribute("href", "/");
    expect(within(mobileNav).getByRole("link", { name: "사건 대기열" })).toHaveAttribute("href", "/incidents");
    expect(within(mobileNav).getByRole("link", { name: "카메라" })).toHaveAttribute("href", "/cameras");
    expect(within(mobileNav).getByRole("link", { name: "카메라" })).toHaveAttribute("aria-current", "page");

    await user.click(within(mobileNav).getByRole("button", { name: "더보기 메뉴" }));

    expect(await screen.findByRole(
      "menuitem",
      { name: "모델·프롬프트" },
      { timeout: 10_000 },
    )).toHaveAttribute("href", "/models");
    expect(screen.getByRole("menuitem", { name: "설치 진행" })).toHaveAttribute("href", "/commissioning");
    expect(screen.getByRole("menuitem", { name: "이벤트 기록" })).toHaveAttribute("href", "/events");
    expect(screen.getByRole("menuitem", { name: "감사 로그" })).toHaveAttribute("href", "/audit");
    expect(screen.getByRole("menuitem", { name: "설정" })).toHaveAttribute("href", "/settings");
  });

  it("shows a persistent warning and removes logout in auth bypass mode", () => {
    render(<AppShell authMode="local" devAuthBypass><div>content</div></AppShell>);

    expect(screen.getByRole("alert")).toHaveTextContent("개발 모드 · 로그인 비활성화 · loopback 전용");
    expect(screen.queryByRole("button", { name: "로그아웃" })).not.toBeInTheDocument();
  });

  it("warns persistently and hides logout when access protection is open", () => {
    render(<AppShell authMode="open"><div>content</div></AppShell>);

    expect(screen.getByRole("alert")).toHaveTextContent("접근 보호 꺼짐");
    expect(within(screen.getByRole("alert")).getByRole("link", { name: "설정" })).toHaveAttribute("href", "/settings");
    expect(screen.queryByRole("button", { name: "로그아웃" })).not.toBeInTheDocument();
  });
});

describe("AppShell role gating (R7)", () => {
  const ADMIN_LABELS = ["모델·프롬프트", "감사 로그", "설정"];
  const RESTRICTED_LABELS = ["설치 진행", "진단", "인수인계", ...ADMIN_LABELS];

  it("viewer sees only read-only entries and hides the admin section label", async () => {
    const user = userEvent.setup();
    render(<AppShell authMode="local" role="viewer"><div>content</div></AppShell>);

    const sidebar = screen.getByRole("navigation", { name: "주요 탐색" });
    expect(within(sidebar).getByRole("link", { name: "Signal" })).toHaveAttribute("href", "/");
    expect(within(sidebar).getByRole("link", { name: "사건 대기열" })).toHaveAttribute("href", "/incidents");
    expect(within(sidebar).getByRole("link", { name: "라이브" })).toHaveAttribute("href", "/live");
    expect(within(sidebar).getByRole("link", { name: "카메라" })).toHaveAttribute("href", "/cameras");
    expect(within(sidebar).getByRole("link", { name: "이벤트 기록" })).toHaveAttribute("href", "/events");
    for (const label of RESTRICTED_LABELS) {
      expect(within(sidebar).queryByRole("link", { name: label })).not.toBeInTheDocument();
    }
    expect(within(sidebar).queryByText("관리자")).not.toBeInTheDocument();

    const mobileNav = screen.getByRole("navigation", { name: "주요 메뉴" });
    await user.click(within(mobileNav).getByRole("button", { name: "더보기 메뉴" }));
    expect(await screen.findByRole("menuitem", { name: "이벤트 기록" })).toHaveAttribute("href", "/events");
    for (const label of RESTRICTED_LABELS) {
      expect(screen.queryByRole("menuitem", { name: label })).not.toBeInTheDocument();
    }
  });

  it("operator sees the same read-only entries as viewer", () => {
    render(<AppShell authMode="local" role="operator"><div>content</div></AppShell>);

    const sidebar = screen.getByRole("navigation", { name: "주요 탐색" });
    expect(within(sidebar).getByRole("link", { name: "Signal" })).toHaveAttribute("href", "/");
    expect(within(sidebar).getByRole("link", { name: "이벤트 기록" })).toHaveAttribute("href", "/events");
    for (const label of RESTRICTED_LABELS) {
      expect(within(sidebar).queryByRole("link", { name: label })).not.toBeInTheDocument();
    }
    expect(within(sidebar).queryByText("관리자")).not.toBeInTheDocument();
  });

  it("admin sees the full navigation including admin section", async () => {
    const user = userEvent.setup();
    render(<AppShell authMode="local" role="admin"><div>content</div></AppShell>);

    const sidebar = screen.getByRole("navigation", { name: "주요 탐색" });
    expect(within(sidebar).getByRole("link", { name: "설치 진행" })).toHaveAttribute("href", "/commissioning");
    expect(within(sidebar).getByRole("link", { name: "진단" })).toHaveAttribute("href", "/diagnostics");
    expect(within(sidebar).getByRole("link", { name: "인수인계" })).toHaveAttribute("href", "/handoff");
    for (const label of ADMIN_LABELS) {
      expect(within(sidebar).getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(within(sidebar).getByText("관리자")).toBeInTheDocument();

    const mobileNav = screen.getByRole("navigation", { name: "주요 메뉴" });
    await user.click(within(mobileNav).getByRole("button", { name: "더보기 메뉴" }));
    expect(await screen.findByRole("menuitem", { name: "설정" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("menuitem", { name: "감사 로그" })).toHaveAttribute("href", "/audit");
    expect(screen.getByRole("menuitem", { name: "모델·프롬프트" })).toHaveAttribute("href", "/models");
  });
});
