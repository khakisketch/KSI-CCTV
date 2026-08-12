import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LogoutButton } from "@/components/logout-button";

const push = vi.fn();
const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
}));

const fetchSpy = vi.fn().mockResolvedValue({ ok: true } as Response);
vi.stubGlobal("fetch", fetchSpy);

describe("LogoutButton", () => {
  it("aria-label='로그아웃'인 버튼을 렌더한다", () => {
    render(<LogoutButton />);

    expect(screen.getByLabelText("로그아웃")).toBeInTheDocument();
  });

  it("클릭 시 /api/auth/logout POST를 호출하고 /login으로 이동한다", async () => {
    const user = userEvent.setup();
    render(<LogoutButton />);

    await user.click(screen.getByLabelText("로그아웃"));

    expect(fetchSpy).toHaveBeenCalledWith("/api/auth/logout", { method: "POST" });
    expect(push).toHaveBeenCalledWith("/login");
    expect(refresh).toHaveBeenCalled();
  });
});
