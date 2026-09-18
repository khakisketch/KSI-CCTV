import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const engineGet = vi.hoisted(() => vi.fn());
vi.mock("@/lib/engine", () => ({ engineGet }));

import { createSession, SESSION_COOKIE } from "@/lib/auth";

describe("commissioning report route", () => {
  let authDir = "";

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-commissioning-report-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "commissioning-report-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(authDir, { recursive: true, force: true });
  });

  it("marks customer handoff data private and non-cacheable", async () => {
    engineGet.mockResolvedValue({
      ok: true,
      status: 200,
      data: { handoff: { customer_name: "Customer", notes: "private" } },
    });
    const { GET } = await import("@/app/api/commissioning/report/route");
    const token = await createSession(3600);

    const response = await GET(new NextRequest("http://localhost:3000/api/commissioning/report", {
      headers: { cookie: `${SESSION_COOKIE}=${token}` },
    }));

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });
});
