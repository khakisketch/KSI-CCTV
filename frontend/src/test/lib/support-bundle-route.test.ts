import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/support/bundle/route";
import { createSession, SESSION_COOKIE } from "@/lib/auth";
import { createUser, findUserByUsername } from "@/lib/users";

describe("support bundle route actor forwarding (F2-M2)", () => {
  let authDir = "";
  let adminToken = "";
  let operatorToken = "";

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-support-bundle-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "support-bundle-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    expect(await createUser("bundle-op", "operator", "bundle-pass-123")).toBe("created");
    const found = await findUserByUsername("bundle-op");
    if (!found || found === "error") throw new Error("user not found: bundle-op");
    adminToken = await createSession(3600);
    operatorToken = await createSession(3600, found.id);
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    await rm(authDir, { recursive: true, force: true });
  });

  function bundleRequest(token: string | undefined) {
    const headers = new Headers();
    if (token) headers.set("cookie", `${SESSION_COOKIE}=${token}`);
    return new NextRequest("http://localhost:3000/api/support/bundle", { headers });
  }

  it("forwards X-KSI-Actor to the engine bundle call and proxies the zip", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: { "content-type": "application/zip" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(bundleRequest(adminToken));

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/zip");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, { headers: Record<string, string> }];
    expect(url).toContain("/api/support/bundle");
    expect(init.headers["X-KSI-Actor"]).toBe("admin");
  });

  it("keeps the admin-only requireRole gate", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const operatorResponse = await GET(bundleRequest(operatorToken));
    expect(operatorResponse.status).toBe(403);
    const unauthResponse = await GET(bundleRequest(undefined));
    expect(unauthResponse.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
