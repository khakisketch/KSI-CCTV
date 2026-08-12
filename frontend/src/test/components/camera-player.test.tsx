import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CameraPlayer } from "@/components/camera-player";

const state = vi.hoisted(() => ({
  errorHandler: null as ((event: unknown, data: { fatal: boolean; type: string; details: string }) => void) | null,
}));

vi.mock("hls.js", () => {
  class FakeHls {
    static Events = { ERROR: "error" };
    static ErrorTypes = { NETWORK_ERROR: "networkError" };
    static isSupported() { return true; }
    loadSource() {}
    attachMedia() {}
    destroy() {}
    on(_event: string, handler: typeof state.errorHandler) { state.errorHandler = handler; }
  }
  return { default: FakeHls };
});

describe("CameraPlayer", () => {
  afterEach(() => {
    state.errorHandler = null;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("explains an unsupported HLS codec and allows retry", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        id: "cam_001",
        hls: "/api/cameras/cam_001/hls/index.m3u8",
      }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraPlayer cameraId="cam_001" />);

    await waitFor(() => expect(state.errorHandler).not.toBeNull());
    act(() => {
      state.errorHandler?.({}, {
        fatal: true,
        type: "mediaError",
        details: "manifestIncompatibleCodecsError",
      });
    });

    expect(screen.getByRole("alert")).toHaveTextContent("H.264 지원 브라우저");
    await user.click(screen.getByRole("button", { name: "재시도" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("surfaces native HLS media errors and allows retry", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "canPlayType").mockReturnValue("probably");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        id: "cam_001",
        hls: "/api/cameras/cam_001/hls/index.m3u8",
      }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { container } = render(<CameraPlayer cameraId="cam_001" />);

    const video = container.querySelector("video");
    expect(video).not.toBeNull();
    await waitFor(() => expect(video).toHaveAttribute("src", "/api/cameras/cam_001/hls/index.m3u8"));
    fireEvent.error(video!);

    expect(screen.getByRole("alert")).toHaveTextContent("브라우저 코덱 지원");
    await user.click(screen.getByRole("button", { name: "재시도" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("keeps the loading state until the first media data is available", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "canPlayType").mockReturnValue("probably");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ id: "cam_001", hls: "/api/cameras/cam_001/hls/index.m3u8" }),
    } as unknown as Response));
    const { container } = render(<CameraPlayer cameraId="cam_001" />);

    await waitFor(() => expect(container.querySelector("video")).toHaveAttribute("src"));
    expect(screen.getByText("연결 중…")).toBeInTheDocument();
    fireEvent.loadedData(container.querySelector("video")!);
    expect(screen.queryByText("연결 중…")).not.toBeInTheDocument();
  });
});
