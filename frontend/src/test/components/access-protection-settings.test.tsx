import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccessProtectionSettings } from "@/components/access-protection-settings";

const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

describe("AccessProtectionSettings", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    refresh.mockReset();
  });

  it("requires a service token in open mode and submits protected setup", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ mode: "local" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AccessProtectionSettings initialMode="open" />);

    expect(screen.getByText(/관리자 선점/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("초기 설정 토큰"), "setup-token-value");
    await user.type(screen.getByLabelText("새 비밀번호"), "new-password");
    await user.type(screen.getByLabelText("새 비밀번호 확인"), "new-password");
    await user.click(screen.getByRole("button", { name: "접근 보호 켜기" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/protection", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ action: "setup", currentPassword: "", newPassword: "new-password", setupToken: "setup-token-value" }),
    }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(screen.getByText("접근 보호 비밀번호를 저장했습니다.")).toBeInTheDocument();
  });

  it("requires the current password for local protection changes and handles race responses", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "충돌" }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AccessProtectionSettings initialMode="local" />);

    await user.type(screen.getByLabelText("현재 비밀번호"), "old-password");
    await user.type(screen.getByLabelText("새 비밀번호"), "new-password");
    await user.type(screen.getByLabelText("새 비밀번호 확인"), "new-password");
    await user.click(screen.getByRole("button", { name: "비밀번호 변경" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("다른 작업으로 변경"));
    expect(refresh).toHaveBeenCalled();
  });

  it("renders externally managed and recovery states without password controls", () => {
    const { unmount } = render(<AccessProtectionSettings initialMode="environment" />);
    expect(screen.getByText(/환경 변수로 외부에서 관리/)).toBeInTheDocument();
    expect(screen.queryByLabelText("새 비밀번호")).not.toBeInTheDocument();

    unmount();
    render(<AccessProtectionSettings initialMode="error" />);
    expect(screen.getByText(/보호된 접근을 유지/)).toBeInTheDocument();
    expect(screen.queryByLabelText("새 비밀번호")).not.toBeInTheDocument();
  });
});
