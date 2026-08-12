import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ engineFetch: vi.fn(), engineGet: vi.fn() }));

vi.mock("@/lib/engine", () => ({ engineFetch: mocks.engineFetch, engineGet: mocks.engineGet }));

function responseWithCookies(body: BodyInit | null, init: ResponseInit, cookies: string[]) {
  const response = new Response(body, init);
  Object.defineProperty(response.headers, "getSetCookie", { value: () => cookies });
  return response;
}

describe("camera media route authorization", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("KSI_CCTV_GATEWAY_URL", "http://gateway.internal:8888");
    mocks.engineFetch.mockReset();
    mocks.engineGet.mockReset();
  });

  it("does not issue an HLS URL for a deleted camera", async () => {
    mocks.engineFetch.mockResolvedValue(new Response(null, { status: 404 }));
    const { GET } = await import("@/app/api/cameras/[id]/stream/route");

    const response = await GET(
      new NextRequest("http://localhost/api/cameras/deleted/stream"),
      { params: Promise.resolve({ id: "deleted" }) },
    );

    expect(response.status).toBe(404);
  });

  it("blocks HLS segments when the persisted camera is disabled", async () => {
    mocks.engineFetch.mockResolvedValue(Response.json({ camera: { enabled: false } }));
    const gatewayFetch = vi.spyOn(globalThis, "fetch");
    const { GET } = await import("@/app/api/cameras/[id]/hls/[...seg]/route");

    const response = await GET(
      new NextRequest("http://localhost/api/cameras/cam_001/hls/index.m3u8"),
      { params: Promise.resolve({ id: "cam_001", seg: ["index.m3u8"] }) },
    );

    expect(response.status).toBe(409);
    expect(gatewayFetch).not.toHaveBeenCalled();
    gatewayFetch.mockRestore();
  });

  it("rejects dot segments before checking or proxying a camera stream", async () => {
    const gatewayFetch = vi.spyOn(globalThis, "fetch");
    const { GET } = await import("@/app/api/cameras/[id]/hls/[...seg]/route");

    const response = await GET(
      new NextRequest("http://localhost/api/cameras/cam_001/hls/../other/index.m3u8"),
      { params: Promise.resolve({ id: "cam_001", seg: ["..", "other", "index.m3u8"] }) },
    );

    expect(response.status).toBe(400);
    expect(mocks.engineFetch).not.toHaveBeenCalled();
    expect(gatewayFetch).not.toHaveBeenCalled();
    gatewayFetch.mockRestore();
  });

  it("preserves the engine denial for a disabled snapshot", async () => {
    mocks.engineFetch.mockResolvedValue(new Response(null, { status: 409 }));
    const { GET } = await import("@/app/api/cameras/[id]/snapshot/route");

    const response = await GET(
      new NextRequest("http://localhost/api/cameras/cam_001/snapshot"),
      { params: Promise.resolve({ id: "cam_001" }) },
    );

    expect(response.status).toBe(409);
  });

  it("does not forward gateway cookies across a redirect origin", async () => {
    mocks.engineFetch.mockImplementation(async () => Response.json({ camera: { enabled: true } }));
    const gatewayFetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, {
        status: 302,
        headers: {
          location: "http://attacker.internal/collect",
          "set-cookie": "cookieCheck=private; Path=/",
        },
      }),
    );
    const { GET } = await import("@/app/api/cameras/[id]/hls/[...seg]/route");

    const response = await GET(
      new NextRequest("http://localhost/api/cameras/cam_001/hls/index.m3u8"),
      { params: Promise.resolve({ id: "cam_001", seg: ["index.m3u8"] }) },
    );

    expect(response.status).toBe(502);
    expect(gatewayFetch).toHaveBeenCalledTimes(1);
    gatewayFetch.mockRestore();
  });

  it("forwards playlists and segments after the MediaMTX cookieCheck handshake", async () => {
    mocks.engineFetch.mockImplementation(async () => Response.json({ camera: { enabled: true } }));
    const gatewayFetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      const requestUrl = String(url);
      const cookie = new Headers(init?.headers).get("cookie");
      if (requestUrl === "http://gateway.internal:8888/cam_001/index.m3u8") {
        expect(cookie).toBeNull();
        return responseWithCookies(null, {
          status: 302,
          headers: { location: "/cam_001/index.m3u8?cookieCheck=1" },
        }, ["cookieCheck=private; Path=/"]);
      }
      if (requestUrl === "http://gateway.internal:8888/cam_001/index.m3u8?cookieCheck=1") {
        expect(cookie).toBe("cookieCheck=private");
        return responseWithCookies("#EXTM3U\nsegment.ts\n", {
          headers: { "content-type": "application/vnd.apple.mpegurl" },
        }, ["mtxSession=authorized; Path=/"]);
      }
      expect(requestUrl).toBe("http://gateway.internal:8888/cam_001/segment.ts?part=1");
      expect(cookie).toBe("cookieCheck=private; mtxSession=authorized");
      return new Response(new Uint8Array([1, 2, 3]), { headers: { "content-type": "video/mp2t" } });
    });
    const { GET } = await import("@/app/api/cameras/[id]/hls/[...seg]/route");

    const playlist = await GET(
      new NextRequest("http://localhost/api/cameras/cam_001/hls/index.m3u8"),
      { params: Promise.resolve({ id: "cam_001", seg: ["index.m3u8"] }) },
    );
    const segment = await GET(
      new NextRequest("http://localhost/api/cameras/cam_001/hls/segment.ts?part=1"),
      { params: Promise.resolve({ id: "cam_001", seg: ["segment.ts"] }) },
    );

    expect(playlist.status).toBe(200);
    expect(await playlist.text()).toBe("#EXTM3U\nsegment.ts\n");
    expect(playlist.headers.get("content-type")).toBe("application/vnd.apple.mpegurl");
    expect(new Uint8Array(await segment.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
    expect(segment.headers.get("content-type")).toBe("video/mp2t");
    expect(gatewayFetch).toHaveBeenCalledTimes(3);
    gatewayFetch.mockRestore();
  });

  it("expires a gateway cookie jar before a later segment request", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    mocks.engineFetch.mockImplementation(async () => Response.json({ camera: { enabled: true } }));
    const gatewayFetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => responseWithCookies(
      "#EXTM3U",
      {},
      ["mtxSession=authorized; Path=/"],
    ));
    const { GET } = await import("@/app/api/cameras/[id]/hls/[...seg]/route");

    await GET(new NextRequest("http://localhost/api/cameras/cam_001/hls/index.m3u8"), {
      params: Promise.resolve({ id: "cam_001", seg: ["index.m3u8"] }),
    });
    vi.advanceTimersByTime(5 * 60 * 1000 + 1);
    await GET(new NextRequest("http://localhost/api/cameras/cam_001/hls/segment.ts"), {
      params: Promise.resolve({ id: "cam_001", seg: ["segment.ts"] }),
    });

    expect(gatewayFetch).toHaveBeenCalledTimes(2);
    expect(new Headers(gatewayFetch.mock.calls[1]?.[1]?.headers).get("cookie")).toBeNull();
    gatewayFetch.mockRestore();
    vi.useRealTimers();
  });

  it("evicts the least-recently-used gateway jar when the camera bound is reached", async () => {
    mocks.engineFetch.mockImplementation(async () => Response.json({ camera: { enabled: true } }));
    const gatewayFetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      const cameraId = new URL(String(url)).pathname.split("/")[1];
      return responseWithCookies("#EXTM3U", {}, [`mtxSession=${cameraId}; Path=/`]);
    });
    const { GET } = await import("@/app/api/cameras/[id]/hls/[...seg]/route");

    for (let i = 0; i <= 32; i++) {
      const id = `cam_${i}`;
      await GET(new NextRequest(`http://localhost/api/cameras/${id}/hls/index.m3u8`), {
        params: Promise.resolve({ id, seg: ["index.m3u8"] }),
      });
    }
    await GET(new NextRequest("http://localhost/api/cameras/cam_0/hls/index.m3u8"), {
      params: Promise.resolve({ id: "cam_0", seg: ["index.m3u8"] }),
    });

    expect(new Headers(gatewayFetch.mock.calls.at(-1)?.[1]?.headers).get("cookie")).toBeNull();
    gatewayFetch.mockRestore();
  });

  it("preserves an engine tracking state error", async () => {
    mocks.engineGet.mockResolvedValue({
      ok: false,
      status: 409,
      data: { detail: "disabled camera has no live tracking data" },
    });
    const { GET } = await import("@/app/api/cameras/[id]/tracking/route");

    const response = await GET(new Request("http://localhost"), {
      params: Promise.resolve({ id: "cam_001" }),
    });

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({
      detail: "disabled camera has no live tracking data",
    });
  });
});
