import { NextRequest } from "next/server";
import { describe, expect, it, vi } from "vitest";

const engineGet = vi.hoisted(() => vi.fn());
vi.mock("@/lib/engine", () => ({ engineGet }));

describe("incidents list proxy route", () => {
  it("forwards unresolved lifecycle reconciliation through the authenticated server client", async () => {
    engineGet.mockResolvedValue({ ok: true, status: 200, data: { available: true, incidents: [] } });
    const { GET } = await import("@/app/api/incidents/route");

    const response = await GET(new NextRequest("http://localhost/api/incidents?unresolved=true&limit=1000"));

    expect(response.status).toBe(200);
    expect(engineGet).toHaveBeenCalledWith("/api/incidents?unresolved=true&limit=1000", { includeStatus: true });
  });
});
