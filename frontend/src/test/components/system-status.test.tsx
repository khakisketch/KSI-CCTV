import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { SystemStatus } from "@/components/system-status";

describe("SystemStatus", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders sanitized AI runtime status without a digest or endpoint", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      json: async () => ({
        metrics: {}, health: { components: [] },
        ai_runtime: {
          behavior: { enabled: true, available: false, runtime_errors: { pose: 0, action: 0 } },
          vlm: { configured_enabled: true, enabled: true, runtime_qualified: false, provider_available: true, queue: { in_flight: 2, pending: 1, dropped: 0, hard_limit: 8, saturated: false }, lifecycle: { unverified: 4 } },
        },
      }),
    } as Response));

    render(<SystemStatus />);

    expect(await screen.findByText("행동 분석: 모델 사용 불가")).toBeInTheDocument();
    expect(screen.getByText("VLM 검증: 미검증")).toBeInTheDocument();
    expect(screen.getByText("큐 2/8, 대기 1, 누락 0")).toBeInTheDocument();
    expect(screen.getByText("검증 불가 4건")).toBeInTheDocument();
    expect(screen.queryByText(/digest|http/i)).not.toBeInTheDocument();
  });

  it("treats disabled optional AI as neutral", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      json: async () => ({ metrics: {}, health: { components: [] }, ai_runtime: { behavior: { enabled: false }, vlm: { configured_enabled: false } } }),
    } as Response));

    render(<SystemStatus />);

    expect(await screen.findByText("행동 분석: 비활성")).toBeInTheDocument();
    expect(screen.getByText("VLM 검증: 비활성")).toBeInTheDocument();
  });
});
