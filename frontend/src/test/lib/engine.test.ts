import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// engine.ts가 모듈 로드 시점에 process.env를 읽어 정적으로 캐싱하므로
// 각 테스트 전 vi.resetModules()로 ESM 캐시를 비우고, 동적 import로 재로드한다.
async function loadGetEvents(engineUrl: string, apiKey: string) {
  vi.resetModules();
  process.env.KSI_CCTV_ENGINE_URL = engineUrl;
  process.env.KSI_CCTV_API_KEY = apiKey;
  const { getEvents } = await import("@/lib/engine");
  return getEvents;
}

describe("getEvents", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.KSI_CCTV_ENGINE_URL;
    delete process.env.KSI_CCTV_API_KEY;
  });

  function mockFetch() {
    return vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({ count: 0, events: [] }),
      } as unknown as Response),
    );
  }

  it("getEvents({ limit: 100 })은 /api/events?limit=100을 호출한다", async () => {
    const getEvents = await loadGetEvents("http://engine-test:8000", "test-key");
    mockFetch();
    await getEvents({ limit: 100 });

    expect(fetch).toHaveBeenCalledWith(
      "http://engine-test:8000/api/events?limit=100",
      expect.objectContaining({
        headers: { "X-API-Key": "test-key" },
        cache: "no-store",
      }),
    );
  });

  it("getEvents({ event_type, alert_level })은 두 파라미터를 모두 포함한다", async () => {
    const getEvents = await loadGetEvents("http://engine-test:8000", "test-key");
    mockFetch();
    await getEvents({ event_type: "fire", alert_level: "critical" });

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("event_type=fire"),
      expect.anything(),
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("alert_level=critical"),
      expect.anything(),
    );
  });

  it("getEvents({ since, offset })은 since/offset 파라미터를 모두 포함한다", async () => {
    const getEvents = await loadGetEvents("http://engine-test:8000", "test-key");
    mockFetch();
    await getEvents({ since: "2026-06-01T00:00:00Z", offset: 10 });

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("since=2026-06-01T00%3A00%3A00Z"),
      expect.anything(),
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("offset=10"),
      expect.anything(),
    );
  });

  it("getEvents(200)은 { limit: 200 }로 해석하여 호출한다", async () => {
    const getEvents = await loadGetEvents("http://engine-test:8000", "test-key");
    mockFetch();
    await getEvents(200);

    expect(fetch).toHaveBeenCalledWith(
      "http://engine-test:8000/api/events?limit=200",
      expect.anything(),
    );
  });

  it("getEvents()은 파라미터 없이 /api/events를 호출한다", async () => {
    const getEvents = await loadGetEvents("http://engine-test:8000", "test-key");
    mockFetch();
    await getEvents();

    expect(fetch).toHaveBeenCalledWith(
      "http://engine-test:8000/api/events?",
      expect.anything(),
    );
  });

  it("getEventCatalog는 /api/event-catalog을 호출하고 catalog 배열을 받는다", async () => {
    vi.resetModules();
    process.env.KSI_CCTV_ENGINE_URL = "http://engine-test:8000";
    process.env.KSI_CCTV_API_KEY = "test-key";
    const { getEventCatalog } = await import("@/lib/engine");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({
          catalog: [
            { name: "fire", alert_level: "critical", domains: ["general"] },
            { name: "intrusion", alert_level: "medium", domains: ["construction"] },
          ],
          count: 2,
        }),
      } as unknown as Response),
    );
    const result = await getEventCatalog();
    if (result === null) throw new Error("event catalog response was unexpectedly null");
    expect(fetch).toHaveBeenCalledWith(
      "http://engine-test:8000/api/event-catalog",
      expect.anything(),
    );
    expect(result.count).toBe(2);
    expect(result.catalog.find((c) => c.name === "fire")?.alert_level).toBe("critical");
  });

  it("engineWrite는 느린 장치 검사에 호출별 timeout을 허용한다", async () => {
    vi.resetModules();
    process.env.KSI_CCTV_ENGINE_URL = "http://engine-test:8000";
    process.env.KSI_CCTV_API_KEY = "test-key";
    const timeout = vi.spyOn(AbortSignal, "timeout");
    const { engineWrite } = await import("@/lib/engine");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: vi.fn().mockResolvedValue({ ok: true }),
      } as unknown as Response),
    );

    await engineWrite("/api/cameras/test", {}, "POST", 20_000);

    expect(timeout).toHaveBeenCalledWith(20_000);
  });
});
