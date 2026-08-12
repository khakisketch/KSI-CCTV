import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DiagnosticsPanel } from "@/components/diagnostics-panel";
import type { CommissioningCheck } from "@/lib/engine";

const checks: CommissioningCheck[] = [
  {
    id: "stream",
    label: "카메라 스트림",
    status: "fail",
    summary: "카메라가 프레임을 보내지 않습니다.",
    remediation: "RTSP 주소와 네트워크 연결을 확인하세요.",
  },
  {
    id: "gpu",
    label: "GPU 실행 공급자",
    status: "warning",
    summary: "CPU 실행 공급자를 사용 중입니다.",
    remediation: "GPU overlay와 CUDA 공급자를 확인하세요.",
  },
  {
    id: "storage",
    label: "저장소 여유 공간",
    status: "pass",
    summary: "저장소 여유 공간이 충분합니다.",
  },
];

describe("DiagnosticsPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("prioritizes failed checks and shows the selected remediation", async () => {
    const user = userEvent.setup();
    render(<DiagnosticsPanel initial={[checks[2], checks[1], checks[0]]} />);

    const failed = screen.getByRole("row", { name: /카메라 스트림/ });
    const warning = screen.getByRole("row", { name: /GPU 실행 공급자/ });
    const passed = screen.getByRole("row", { name: /저장소 여유 공간/ });
    const rows = screen.getAllByRole("row");

    expect(rows.indexOf(failed)).toBeLessThan(rows.indexOf(warning));
    expect(rows.indexOf(warning)).toBeLessThan(rows.indexOf(passed));
    expect(failed).toHaveAttribute("aria-selected", "true");

    await user.click(warning);

    expect(warning).toHaveAttribute("aria-selected", "true");
    expect(screen.getAllByText("GPU overlay와 CUDA 공급자를 확인하세요.").length).toBeGreaterThan(0);
  });

  it("replaces checks and confirms a successful refresh", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ checks: [checks[1]] }),
    }));
    const user = userEvent.setup();
    render(<DiagnosticsPanel initial={[checks[0]]} />);

    await user.click(screen.getByRole("button", { name: "다시 확인" }));

    expect(fetch).toHaveBeenCalledWith("/api/preflight", { cache: "no-store" });
    expect(await screen.findByRole("status")).toHaveTextContent("진단 결과를 갱신했습니다.");
    expect(screen.getByRole("row", { name: /GPU 실행 공급자/ })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /카메라 스트림/ })).not.toBeInTheDocument();
  });

  it("shows the engine error detail when refresh fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "엔진 점검이 아직 끝나지 않았습니다." }),
    }));
    const user = userEvent.setup();
    render(<DiagnosticsPanel initial={checks} />);

    await user.click(screen.getByRole("button", { name: "다시 확인" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("엔진 점검이 아직 끝나지 않았습니다.");
  });

  it("shows disabled behavior normally and VLM hard saturation as a failure", () => {
    render(<DiagnosticsPanel initial={checks} initialAiRuntime={{
      behavior: { enabled: false },
      vlm: { configured_enabled: true, enabled: true, runtime_qualified: false, provider_available: true, queue: { in_flight: 16, pending: 2, dropped: 1, hard_limit: 16, saturated: true }, lifecycle: { unverified: 3 } },
    }} />);

    expect(screen.getByText("행동 분석: 비활성")).toBeInTheDocument();
    expect(screen.getByText("VLM 큐 포화")).toBeInTheDocument();
    expect(screen.getByText(/큐 16\/16, 대기 2, 누락 1/)).toBeInTheDocument();
    expect(screen.getByText("검증 불가 3건")).toBeInTheDocument();
  });
});
