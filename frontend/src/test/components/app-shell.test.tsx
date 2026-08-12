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
