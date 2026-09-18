import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSession, SESSION_COOKIE } from "@/lib/auth";
import { requireRole } from "@/lib/require-role";
import { createUser, findUserByUsername } from "@/lib/users";

describe("requireRole (RBAC R4)", () => {
  let authDir = "";
  let adminToken = "";
  let operatorToken = "";
  let viewerToken = "";

  async function userId(username: string): Promise<string> {
    const found = await findUserByUsername(username);
    if (!found || found === "error") throw new Error(`user not found: ${username}`);
    return found.id;
  }

  function requestWith(token: string | undefined) {
    const headers = new Headers();
    if (token) headers.set("cookie", `${SESSION_COOKIE}=${token}`);
    return new NextRequest("http://localhost:3000/api/models", { headers });
  }

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-require-role-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "require-role-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    expect(await createUser("rr-op", "operator", "operator-pass-123")).toBe("created");
    expect(await createUser("rr-view", "viewer", "viewer-pass-12345")).toBe("created");
    adminToken = await createSession(3600);
    operatorToken = await createSession(3600, await userId("rr-op"));
    viewerToken = await createSession(3600, await userId("rr-view"));
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(authDir, { recursive: true, force: true });
  });

  it("returns 401 with no-store when unauthenticated", async () => {
    const denied = await requireRole(requestWith(undefined), "viewer");

    expect(denied?.status).toBe(401);
    expect(denied?.headers.get("Cache-Control")).toBe("no-store");
  });

  it("returns 403 with no-store when a viewer hits an operator gate", async () => {
    const denied = await requireRole(requestWith(viewerToken), "operator");

    expect(denied?.status).toBe(403);
    expect(denied?.headers.get("Cache-Control")).toBe("no-store");
  });

  it("returns 403 when a viewer hits an admin gate", async () => {
    const denied = await requireRole(requestWith(viewerToken), "admin");

    expect(denied?.status).toBe(403);
  });

  it("returns 403 when an operator hits an admin gate", async () => {
    const denied = await requireRole(requestWith(operatorToken), "admin");

    expect(denied?.status).toBe(403);
  });

  it("returns null when an operator meets an operator gate", async () => {
    await expect(requireRole(requestWith(operatorToken), "operator")).resolves.toBeNull();
  });

  it("returns null when an admin meets an admin gate", async () => {
    await expect(requireRole(requestWith(adminToken), "admin")).resolves.toBeNull();
  });
});

describe("requestActor (RBAC R5b)", () => {
  let authDir = "";
  let adminToken = "";
  let operatorToken = "";

  async function userId(username: string): Promise<string> {
    const found = await findUserByUsername(username);
    if (!found || found === "error") throw new Error(`user not found: ${username}`);
    return found.id;
  }

  function requestWith(token: string | undefined) {
    const headers = new Headers();
    if (token) headers.set("cookie", `${SESSION_COOKIE}=${token}`);
    return new NextRequest("http://localhost:3000/api/models", { headers });
  }

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-request-actor-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "request-actor-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    expect(await createUser("ra-op", "operator", "operator-pass-123")).toBe("created");
    adminToken = await createSession(3600);
    operatorToken = await createSession(3600, await userId("ra-op"));
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(authDir, { recursive: true, force: true });
  });

  it("resolves admin sessions to admin", async () => {
    const { requestActor } = await import("@/lib/require-role");
    await expect(requestActor(requestWith(adminToken))).resolves.toBe("admin");
  });

  it("resolves user sessions to their username", async () => {
    const { requestActor } = await import("@/lib/require-role");
    await expect(requestActor(requestWith(operatorToken))).resolves.toBe("ra-op");
  });

  it("resolves unauthenticated requests to system", async () => {
    const { requestActor } = await import("@/lib/require-role");
    await expect(requestActor(requestWith(undefined))).resolves.toBe("system");
  });
});
