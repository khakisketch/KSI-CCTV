import { describe, expect, it, vi } from "vitest";

const engineGet = vi.hoisted(() => vi.fn());
vi.mock("@/lib/engine", () => ({ engineGet }));

describe("commissioning report route", () => {
  it("marks customer handoff data private and non-cacheable", async () => {
    engineGet.mockResolvedValue({
      ok: true,
      status: 200,
      data: { handoff: { customer_name: "Customer", notes: "private" } },
    });
    const { GET } = await import("@/app/api/commissioning/report/route");

    const response = await GET();

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });
});
