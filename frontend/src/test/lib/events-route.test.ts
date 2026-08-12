import { NextRequest } from "next/server";
import { describe, expect, it, vi } from "vitest";

const engineGet = vi.hoisted(() => vi.fn());
vi.mock("@/lib/engine", () => ({ engineGet }));

describe("events list proxy route", () => {
  it("forwards reconciliation filters through the authenticated server client", async () => {
    engineGet.mockResolvedValue({ ok: true, status: 200, data: { events: [] } });
    const { GET } = await import("@/app/api/events/route");

    const response = await GET(new NextRequest("http://localhost/api/events?limit=200"));

    expect(response.status).toBe(200);
    expect(engineGet).toHaveBeenCalledWith("/api/events?limit=200", { includeStatus: true });
  });
});
