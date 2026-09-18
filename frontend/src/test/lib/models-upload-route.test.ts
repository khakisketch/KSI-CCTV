import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/models/upload/route";
import { createSession, SESSION_COOKIE } from "@/lib/auth";
import { createUser, findUserByUsername } from "@/lib/users";

describe("models upload route actor forwarding (F2-M2)", () => {
  let authDir = "";
  let adminToken = "";
  let operatorToken = "";

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-models-upload-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "models-upload-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    expect(await createUser("upload-op", "operator", "upload-pass-123")).toBe("created");
    const found = await findUserByUsername("upload-op");
    if (!found || found === "error") throw new Error("user not found: upload-op");
    adminToken = await createSession(3600);
    operatorToken = await createSession(3600, found.id);
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    await rm(authDir, { recursive: true, force: true });
  });

  function uploadRequest(token: string | undefined) {
    const headers = new Headers({ "content-type": "multipart/form-data; boundary=test-boundary" });
    if (token) headers.set("cookie", `${SESSION_COOKIE}=${token}`);
    return new NextRequest("http://localhost:3000/api/models/upload", {
      method: "POST",
      headers,
      body: "--test-boundary--\r\n",
    });
  }

  it("forwards X-KSI-Actor to the engine upload call", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(uploadRequest(adminToken));

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, { headers: Record<string, string> }];
    expect(url).toContain("/api/models/upload");
    expect(init.headers["X-KSI-Actor"]).toBe("admin");
  });

  it("keeps the admin-only requireRole gate", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const operatorResponse = await POST(uploadRequest(operatorToken));
    expect(operatorResponse.status).toBe(403);
    const unauthResponse = await POST(uploadRequest(undefined));
    expect(unauthResponse.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
