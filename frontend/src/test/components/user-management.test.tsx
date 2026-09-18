import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UserManagement, type ManagedUser } from "@/components/user-management";

const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

const INITIAL: ManagedUser[] = [
  { id: "id-op", username: "op1", role: "operator", enabled: true, createdAt: 1700000000000, updatedAt: 1700000000000 },
  { id: "id-view", username: "view1", role: "viewer", enabled: false, createdAt: 1700000000000, updatedAt: 1700000000000 },
];

describe("UserManagement", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    refresh.mockReset();
  });

  it("renders the user list with role, status and creation date", () => {
    render(<UserManagement initialUsers={INITIAL} />);

    expect(screen.getByText("op1")).toBeInTheDocument();
    expect(screen.getByText("view1")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(screen.getByLabelText("역할 변경: op1")).toHaveValue("operator");
    expect(screen.getByLabelText("역할 변경: view1")).toHaveValue("viewer");
    expect(within(table).getByText("활성")).toBeInTheDocument();
    expect(within(table).getByText("비활성")).toBeInTheDocument();
    expect(screen.getByLabelText("새 사용자 이름")).toBeInTheDocument();
    expect(screen.getByLabelText("새 사용자 역할")).toBeInTheDocument();
    expect(screen.getByLabelText("새 사용자 비밀번호")).toBeInTheDocument();
  });

  it("submits a create request and refreshes on success", async () => {
    const user = userEvent.setup();
    const created: ManagedUser = { id: "id-new", username: "op2", role: "viewer", enabled: true, createdAt: 1700000000000, updatedAt: 1700000000000 };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ user: created }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<UserManagement initialUsers={INITIAL} />);

    await user.type(screen.getByLabelText("새 사용자 이름"), "op2");
    await user.selectOptions(screen.getByLabelText("새 사용자 역할"), "viewer");
    await user.type(screen.getByLabelText("새 사용자 비밀번호"), "new-password-123");
    await user.click(screen.getByRole("button", { name: "사용자 추가" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith("/api/users", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ username: "op2", role: "viewer", password: "new-password-123" }),
    }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(screen.getByRole("status")).toHaveTextContent("op2");
  });

  it("shows the server error detail when creation fails", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "이미 존재하는 사용자 이름입니다." }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<UserManagement initialUsers={INITIAL} />);

    await user.type(screen.getByLabelText("새 사용자 이름"), "op1");
    await user.type(screen.getByLabelText("새 사용자 비밀번호"), "new-password-123");
    await user.click(screen.getByRole("button", { name: "사용자 추가" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("이미 존재하는 사용자 이름입니다."));
    expect(refresh).not.toHaveBeenCalled();
  });

  it("sends a role change patch for the selected user", async () => {
    const user = userEvent.setup();
    const updated: ManagedUser = { ...INITIAL[0], role: "viewer" };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ user: updated }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<UserManagement initialUsers={INITIAL} />);

    await user.selectOptions(screen.getByLabelText("역할 변경: op1"), "viewer");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith("/api/users/id-op", expect.objectContaining({
      method: "PATCH",
      body: JSON.stringify({ role: "viewer" }),
    }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
  });
});
