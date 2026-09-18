import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

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

describe("LoginForm username (RBAC R2)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers an optional username field in protected modes only", () => {
    const { unmount } = render(<LoginForm mode="local" />);
    const username = screen.getByLabelText("사용자명");
    expect(username).toHaveAttribute("autocomplete", "username");
    expect(username).not.toBeRequired();
    unmount();

    render(<LoginForm mode="open" />);
    expect(screen.queryByLabelText("사용자명")).not.toBeInTheDocument();
  });

  it("submits the username together with the password", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<LoginForm mode="local" />);

    await user.type(screen.getByLabelText("사용자명"), "op1");
    await user.type(screen.getByLabelText("비밀번호"), "operator-pass-123");
    await user.click(screen.getByRole("button", { name: "로그인" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as [string, { body: string }];
    expect(JSON.parse(init.body)).toEqual({ username: "op1", password: "operator-pass-123" });
  });
});
