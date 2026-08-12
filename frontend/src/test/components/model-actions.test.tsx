import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ModelActions } from "@/components/model-actions";

const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    refresh,
    push: vi.fn(),
  }),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

function mockFetch(ok = true, detail?: string) {
  return vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok,
      json: vi.fn().mockResolvedValue(detail ? { detail } : {}),
    } as unknown as Response),
  );
}

describe("ModelActions", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    refresh.mockClear();
  });

  it("enabled=true일 때 '비활성화' 버튼을 렌더한다", () => {
    render(<ModelActions name="yolo-v8" enabled={true} deletable={false} />);

    expect(screen.getByRole("button", { name: "비활성화" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "활성화" })).not.toBeInTheDocument();
  });

  it("enabled=false일 때 '활성화' 버튼을 렌더한다", () => {
    render(<ModelActions name="yolo-v8" enabled={false} deletable={false} />);

    expect(screen.getByRole("button", { name: "활성화" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "비활성화" })).not.toBeInTheDocument();
  });

  it("토글 클릭 시 fetch(PUT) 호출하고 toast.success + router.refresh를 호출한다", async () => {
    mockFetch();
    render(<ModelActions name="yolo-v8" enabled={true} deletable={false} />);

    await userEvent.click(screen.getByRole("button", { name: "비활성화" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/models/yolo-v8/enabled",
        expect.objectContaining({
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: false }),
        }),
      );
    });
    expect(vi.mocked((await import("sonner")).toast.success)).toHaveBeenCalled();
    expect(refresh).toHaveBeenCalled();
  });

  it("deletable=false일 때 '삭제' 버튼이 없다", () => {
    render(<ModelActions name="yolo-v8" enabled={true} deletable={false} />);

    expect(screen.queryByRole("button", { name: "삭제" })).not.toBeInTheDocument();
  });

  it("deletable=true일 때 '삭제' → '삭제 확인' → fetch(DELETE)를 호출한다", async () => {
    mockFetch();
    render(<ModelActions name="yolo-v8" enabled={true} deletable={true} />);

    await userEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(screen.getByRole("button", { name: "삭제 확인" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "삭제 확인" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/models/yolo-v8",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
    expect(vi.mocked((await import("sonner")).toast.success)).toHaveBeenCalled();
    expect(refresh).toHaveBeenCalled();
  });

  it("삭제 확인 UI에서 '취소' 클릭 시 확인 UI가 닫힌다", async () => {
    render(<ModelActions name="yolo-v8" enabled={true} deletable={true} />);

    await userEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(screen.getByRole("button", { name: "삭제 확인" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(screen.queryByRole("button", { name: "삭제 확인" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "삭제" })).toBeInTheDocument();
  });
});
