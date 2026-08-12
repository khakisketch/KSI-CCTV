import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/login-form";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

describe("LoginForm", () => {
  it("does not offer a password form in open or error mode", () => {
    render(<LoginForm mode="open" />);

    expect(screen.getByText("접근 보호가 꺼져 있습니다")).toBeInTheDocument();
    expect(screen.queryByLabelText("비밀번호")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "로그인" })).not.toBeInTheDocument();
  });

  it("offers an accessible password form only in protected modes", () => {
    render(<LoginForm mode="local" />);

    expect(screen.getByLabelText("비밀번호")).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();
  });

  it("shows recovery guidance without a form in error mode", () => {
    render(<LoginForm mode="error" />);

    expect(screen.getByText("접근 보호 복구 필요")).toBeInTheDocument();
    expect(screen.queryByLabelText("비밀번호")).not.toBeInTheDocument();
  });
});
