import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CameraActions } from "@/components/camera-actions";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

describe("CameraActions", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("runs a structured camera connection test", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ ok: true, code: "first_frame" }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraActions cameraId="cam_001" enabled revision={4} />);

    await user.click(screen.getByRole("button", { name: "연결 검사" }));

    await waitFor(() => expect(screen.getByText(/first_frame/)).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/cameras/cam_001/action",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ action: "test", expected_revision: 4 }) }),
    );
  });

  it("does not report an HTTP 200 failed probe as success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ ok: false, code: "first_frame_failed", message: "No frame received" }),
    } as unknown as Response));
    const user = userEvent.setup();
    render(<CameraActions cameraId="cam_001" enabled />);

    await user.click(screen.getByRole("button", { name: "연결 검사" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No frame received");
    expect(screen.queryByText(/연결 확인 완료/)).not.toBeInTheDocument();
  });

  it("does not report an unapplied runtime action as success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ applied: false, apply_error: "gateway unavailable" }),
    } as unknown as Response));
    const user = userEvent.setup();
    render(<CameraActions cameraId="cam_001" enabled />);

    await user.click(screen.getByRole("button", { name: "설정 재적용" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("gateway unavailable");
    expect(screen.queryByText(/실행 상태까지 확인/)).not.toBeInTheDocument();
  });

  it("does not offer runtime reapply for a disabled camera", () => {
    render(<CameraActions cameraId="cam_001" enabled={false} />);

    expect(screen.queryByRole("button", { name: "설정 재적용" })).not.toBeInTheDocument();
    expect(screen.getByText(/비활성 카메라는 설정을 재적용할 수 없습니다/)).toBeInTheDocument();
  });
});
