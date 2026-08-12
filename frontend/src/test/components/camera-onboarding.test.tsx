import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CameraOnboarding } from "@/components/camera-onboarding";

const policies = { construction: [{ name: "intrusion", alert_level: "critical" }] };

function response(data: unknown, ok = true): Response {
  return { ok, json: vi.fn().mockResolvedValue(data) } as unknown as Response;
}

describe("CameraOnboarding", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("searches automatically and uses the discovered ONVIF service port for profiles", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/video-sources/capabilities") {
        return Promise.resolve(response({ adapters: [{ kind: "v4l2", available: false, reason: "USB bridge required" }] }));
      }
      if (url === "/api/cameras/discover") {
        return Promise.resolve(response({
          available: true,
          devices: [{
            name: "Front Door",
            host: "192.168.1.20",
            model: "Axis M30",
            onvif_port: 8080,
            discovery_id: "a".repeat(32),
            source_type: "onvif",
          }],
        }));
      }
      if (url === "/api/cameras/onvif-profiles") {
        return Promise.resolve(response({
          profiles: [{ token: "sub", name: "Sub stream", rtsp_url: "rtsp://camera/sub", resolution: [1280, 720], fps: 10, encoding: "H264" }],
        }));
      }
      return Promise.resolve(response({ ok: true }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraOnboarding policyProfiles={policies} />);

    await user.click(screen.getByRole("button", { name: "카메라 추가" }));
    expect(await screen.findByText(/USB bridge required/)).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /Front Door/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.type(screen.getByLabelText("사용자 이름"), "installer");
    await user.type(screen.getByLabelText("비밀번호"), "do-not-show");
    await user.click(screen.getByRole("button", { name: /ONVIF 스트림 프로필 조회/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/cameras/onvif-profiles",
      expect.objectContaining({
        body: JSON.stringify({ discovery_id: "a".repeat(32), username: "installer", password: "do-not-show" }),
      }),
    ));
    expect(await screen.findByLabelText("분석 스트림")).toHaveValue("rtsp://camera/sub");
  });

  it("keeps manual RTSP behind the no-device fallback and never repeats the password", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/video-sources/capabilities") return Promise.resolve(response({ adapters: [] }));
      if (url === "/api/cameras/discover") return Promise.resolve(response({ available: true, devices: [] }));
      if (url === "/api/cameras") return Promise.resolve(response({ applied: true, camera: { id: "cam_001" } }));
      return Promise.resolve(response({ ok: true, diagnostics: { codec: "h264" } }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraOnboarding policyProfiles={policies} />);

    await user.click(screen.getByRole("button", { name: "카메라 추가" }));
    await screen.findByText(/검색된 새 카메라가 없습니다/);
    await user.click(screen.getByRole("button", { name: /RTSP 주소 직접 입력/ }));
    await user.type(screen.getByLabelText("카메라 이름"), "정문");
    await user.type(screen.getByLabelText("RTSP 주소"), "rtsp://camera/stream");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.type(screen.getByLabelText("사용자 이름"), "installer");
    await user.type(screen.getByLabelText("비밀번호"), "do-not-show");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: "스트림 검사 실행" }));
    await waitFor(() => expect(screen.getByText("연결 성공")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("checkbox", { name: /intrusion/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    expect(screen.getByText(/비밀번호: 입력됨/)).toBeInTheDocument();
    expect(screen.queryByText("do-not-show")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "등록 적용" }));
    await waitFor(() => expect(screen.getByText("카메라 운영 준비가 완료되었습니다")).toBeInTheDocument());
  });

  it("accepts a discovered gateway stream without requiring an address", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/video-sources/capabilities") return Promise.resolve(response({ adapters: [] }));
      if (url === "/api/cameras/discover") return Promise.resolve(response({
        available: true,
        devices: [{
          name: "연결된 영상 cam_005",
          host: "영상 Gateway",
          rtsp_url: "rtsp://gateway:8554/cam_005",
          model: "H264 · 1280x720",
          source_type: "gateway",
        }],
      }));
      return Promise.resolve(response({ ok: true }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraOnboarding policyProfiles={policies} />);

    await user.click(screen.getByRole("button", { name: "카메라 추가" }));
    await user.click(await screen.findByRole("button", { name: /연결된 영상 cam_005/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    expect(screen.getByText(/선택한 Gateway 영상/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: "스트림 검사 실행" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/cameras/test",
      expect.objectContaining({ body: JSON.stringify({ rtsp_url: "rtsp://gateway:8554/cam_005", username: "", password: "" }) }),
    ));
  });

  it("redacts a password when the stream test response echoes it", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/video-sources/capabilities") return Promise.resolve(response({ adapters: [] }));
      if (url === "/api/cameras/discover") return Promise.resolve(response({ available: true, devices: [] }));
      return Promise.resolve(response({ detail: "authentication failed: do-not-show" }, false));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraOnboarding policyProfiles={policies} />);

    await user.click(screen.getByRole("button", { name: "카메라 추가" }));
    await screen.findByText(/검색된 새 카메라가 없습니다/);
    await user.click(screen.getByRole("button", { name: /RTSP 주소 직접 입력/ }));
    await user.type(screen.getByLabelText("카메라 이름"), "정문");
    await user.type(screen.getByLabelText("RTSP 주소"), "rtsp://camera/stream");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.type(screen.getByLabelText("비밀번호"), "do-not-show");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: "스트림 검사 실행" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("authentication failed: [redacted]");
    expect(screen.queryByText(/do-not-show/)).not.toBeInTheDocument();
  });

  it("keeps the selected ONVIF camera and offers an in-place RTSP fallback when profiles fail", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/video-sources/capabilities") return Promise.resolve(response({ adapters: [] }));
      if (url === "/api/cameras/discover") return Promise.resolve(response({ available: true, devices: [{ name: "Front Door", discovery_id: "a".repeat(32), source_type: "onvif" }] }));
      if (url === "/api/cameras/onvif-profiles") return Promise.resolve(response({ detail: "profile unavailable" }, false));
      return Promise.resolve(response({}));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraOnboarding policyProfiles={policies} />);

    await user.click(screen.getByRole("button", { name: "카메라 추가" }));
    await user.click(await screen.findByRole("button", { name: /Front Door/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /ONVIF 스트림 프로필 조회/ }));

    expect(await screen.findByLabelText("RTSP 주소 직접 입력")).toBeInTheDocument();
    expect(screen.getByText(/선택한 카메라의 RTSP 주소를 직접 입력/)).toBeInTheDocument();
  });

  it("does not replace event selections on domain change and discloses deferred zone defaults", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/video-sources/capabilities") return Promise.resolve(response({ adapters: [] }));
      if (url === "/api/cameras/discover") return Promise.resolve(response({ available: true, devices: [] }));
      return Promise.resolve(response({ ok: true }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraOnboarding
      policyProfiles={{ construction: [{ name: "intrusion" }], warehouse: [{ name: "intrusion" }, { name: "loitering" }] }}
      eventCatalog={[{ name: "intrusion", label: "침입" }, { name: "loitering", label: "배회", requires_zone: "loitering" }]}
      profilePresets={{ construction: { default_events: ["intrusion"] }, warehouse: { default_events: ["loitering"] } }}
    />);

    await user.click(screen.getByRole("button", { name: "카메라 추가" }));
    await user.click(await screen.findByRole("button", { name: /RTSP 주소 직접 입력/ }));
    await user.type(screen.getByLabelText("카메라 이름"), "정문");
    await user.type(screen.getByLabelText("RTSP 주소"), "rtsp://camera/stream");
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: /다음/ }));
    await user.click(screen.getByRole("button", { name: "스트림 검사 실행" }));
    await user.click(screen.getByRole("button", { name: /다음/ }));

    await user.selectOptions(screen.getByLabelText("현장 유형"), "warehouse");
    expect(screen.getByRole("checkbox", { name: /침입/ })).toBeChecked();
    expect(screen.getByText(/기본값 중 영역이 필요한 감지는 등록 후로 보류됩니다: 배회 \(필요 영역: loitering\)/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "이 현장 유형의 기본값 적용" }));
    expect(screen.getByRole("checkbox", { name: /침입/ })).not.toBeChecked();
  });

  it("enrolls selected batch cameras sequentially and retries only failures", async () => {
    let registrations = 0;
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/cameras/discover") return Promise.resolve(response({ available: true, devices: [{ name: "A", rtsp_url: "rtsp://a", source_type: "gateway" }, { name: "B", rtsp_url: "rtsp://b", source_type: "gateway" }] }));
      if (url === "/api/cameras") {
        registrations += 1;
        return Promise.resolve(response(registrations === 2 ? { detail: "offline" } : { applied: true }, registrations !== 2));
      }
      return Promise.resolve(response({}));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraOnboarding policyProfiles={policies} />);

    await user.click(screen.getByRole("button", { name: "여러 대 등록" }));
    await user.click(await screen.findByRole("checkbox", { name: /A/ }));
    await user.click(screen.getByRole("checkbox", { name: /B/ }));
    await user.click(screen.getByRole("button", { name: "선택한 카메라 등록" }));
    expect(await screen.findByLabelText("A: 완료")).toBeInTheDocument();
    expect(screen.getByLabelText("B: 실패 - offline")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "실패한 카메라만 재시도" }));
    await waitFor(() => expect(screen.getByLabelText("B: 완료")).toBeInTheDocument());
    expect(registrations).toBe(3);
  });

  it("shows a retryable error when camera discovery cannot reach the engine", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/video-sources/capabilities") return Promise.resolve(response({ adapters: [] }));
      if (url === "/api/cameras/discover") return Promise.reject(new Error("network unavailable"));
      return Promise.resolve(response({}));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraOnboarding policyProfiles={policies} />);

    await user.click(screen.getByRole("button", { name: "카메라 추가" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("network unavailable");
    expect(screen.getByRole("button", { name: "카메라 다시 검색" })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/cameras/discover",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });
});
