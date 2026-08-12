import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("events stream proxy route", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("forwards browser Last-Event-ID while keeping the engine key server-side", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(": connected\n\n", {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("@/app/api/events/stream/route");

    const response = await GET(new NextRequest("http://localhost/api/events/stream", {
      headers: { "last-event-id": "event-42" },
    }));

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/events/stream"),
      expect.objectContaining({
        headers: expect.objectContaining({
          "Last-Event-ID": "event-42",
          Accept: "text/event-stream",
        }),
      }),
    );
  });
});
