import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CameraConnectionEditor } from "@/components/camera-connection-editor";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

const camera = {
  id: "cam_001", name: "정문", location: "1층", managed: true, enabled: true,
  stream_url: "rtsp://camera/stream", credential_configured: true, profile: "general",
  enabled_events: ["intrusion"], zones: [{ id: "gate", type: "polygon" as const, points: [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }] }],
  fps: 15, resolution: [1920, 1080] as [number, number],
};

describe("CameraConnectionEditor", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.stubGlobal("IntersectionObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() { return []; }
    });
    refresh.mockReset();
  });

  it("invalidates a successful transient test when the URL changes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: vi.fn().mockResolvedValue({ ok: true }) } as unknown as Response));
    const user = userEvent.setup();
    render(<CameraConnectionEditor camera={camera} revision={7} />);

    await user.click(screen.getByRole("button", { name: "스트림 검사" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "변경 저장" })).toBeEnabled());
    await user.type(screen.getByLabelText("RTSP 주소"), "?new");

    expect(screen.getByRole("button", { name: "변경 저장" })).toBeDisabled();
    expect(screen.getByText(/저장하려면 현재 연결 정보/)).toBeInTheDocument();
  });

  it("omits replacement secrets to preserve configured credentials and sends the revision payload", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: vi.fn().mockResolvedValue({ ok: true }) } as unknown as Response)
      .mockResolvedValueOnce({ ok: true, json: vi.fn().mockResolvedValue({ applied: true, revision: 8 }) } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraConnectionEditor camera={camera} revision={7} />);

    expect(screen.queryByText("existing-secret")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "스트림 검사" }));
    await user.click(screen.getByRole("button", { name: "변경 저장" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [testUrl, testInit] = fetchMock.mock.calls[0];
    expect(testUrl).toBe("/api/cameras/cam_001/test-draft");
    expect(JSON.parse(String((testInit as RequestInit).body))).not.toHaveProperty("password");
    const [saveUrl, saveInit] = fetchMock.mock.calls[1];
    expect(saveUrl).toBe("/api/cameras/cam_001?expected_revision=7");
    const payload = JSON.parse(String((saveInit as RequestInit).body));
    expect(payload).toMatchObject({ profile: "general", enabled_events: ["intrusion"], fps: 15, resolution: [1920, 1080], enabled: true });
    expect(payload.zones).toEqual(camera.zones);
    expect(payload).not.toHaveProperty("username");
    expect(payload).not.toHaveProperty("password");
  });

  it("tests and saves explicit credential removal without exposing stored secrets", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: vi.fn().mockResolvedValue({ ok: true }) } as unknown as Response)
      .mockResolvedValueOnce({ ok: true, json: vi.fn().mockResolvedValue({ applied: true, revision: 8 }) } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraConnectionEditor camera={camera} revision={7} />);

    await user.click(screen.getByLabelText("저장된 인증 정보 제거"));
    await user.click(screen.getByRole("button", { name: "스트림 검사" }));
    await user.click(screen.getByRole("button", { name: "변경 저장" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    for (const call of fetchMock.mock.calls) {
      const payload = JSON.parse(String((call[1] as RequestInit).body));
      expect(payload).toMatchObject({ clear_credentials: true });
      expect(payload).not.toHaveProperty("username");
      expect(payload).not.toHaveProperty("password");
    }
  });

  it("replaces only the credential field entered by the installer", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: vi.fn().mockResolvedValue({ ok: true }) } as unknown as Response)
      .mockResolvedValueOnce({ ok: true, json: vi.fn().mockResolvedValue({ applied: true, revision: 8 }) } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraConnectionEditor camera={camera} revision={7} />);

    await user.type(screen.getByLabelText("교체 사용자 이름 (선택)"), "new-viewer");
    await user.click(screen.getByRole("button", { name: "스트림 검사" }));
    await user.click(screen.getByRole("button", { name: "변경 저장" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    for (const call of fetchMock.mock.calls) {
      const payload = JSON.parse(String((call[1] as RequestInit).body));
      expect(payload.username).toBe("new-viewer");
      expect(payload).not.toHaveProperty("password");
    }
  });

  it("reports that the previous settings were restored when runtime application fails", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: vi.fn().mockResolvedValue({ ok: true }) } as unknown as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        json: vi.fn().mockResolvedValue({ detail: "새 연결 설정을 실행 환경에 적용하지 못해 이전 설정으로 복구했습니다." }),
      } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraConnectionEditor camera={camera} revision={7} />);

    await user.click(screen.getByRole("button", { name: "스트림 검사" }));
    await user.click(screen.getByRole("button", { name: "변경 저장" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("이전 설정으로 복구했습니다");
  });
});
