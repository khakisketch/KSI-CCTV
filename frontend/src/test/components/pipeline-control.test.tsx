import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { PipelineControl } from "@/components/pipeline-control";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

function mockFetch(response: { connected: boolean; running: boolean }) {
  return vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue(response),
    } as unknown as Response)
  );
}

describe("PipelineControl", () => {
  it("초기 상태에서 '상태 확인 중…'을 렌더한다", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));

    render(<PipelineControl />);

    expect(screen.getByText("상태 확인 중…")).toBeInTheDocument();
  });

  it("connected=false 응답 시 분석 서비스 연결 안내 문구를 렌더한다", async () => {
    mockFetch({ connected: false, running: false });

    render(<PipelineControl />);

    await waitFor(() => {
      expect(screen.getByText(/분석 서비스 제어에 연결되지 않았습니다/)).toBeInTheDocument();
    });
  });

  it("connected=true, running=false일 때 '중지됨' 배지 + 시작 버튼 활성 + 중지 버튼 비활성", async () => {
    mockFetch({ connected: true, running: false });

    render(<PipelineControl />);

    await waitFor(() => {
      expect(screen.getByText("중지됨")).toBeInTheDocument();
    });

    const startButton = screen.getByRole("button", { name: "시작" });
    const stopButton = screen.getByRole("button", { name: "중지" });

    expect(startButton).toBeEnabled();
    expect(stopButton).toBeDisabled();
  });
});
