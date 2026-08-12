import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "@/app/api/auth/protection/route";
import {
  SESSION_COOKIE,
  changeLocalPassword,
  createSession,
  protectionStatus,
  setupLocalProtection,
  verifyPassword,
  verifySession,
} from "@/lib/auth";

let authDir = "";
let sessionDir = "";
let source = 0;

async function request(body: unknown, token?: string, origin: string | null = "http://console.test") {
  source += 1;
  return POST(new NextRequest("http://console.test/api/auth/protection", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(origin ? { origin } : {}),
      "x-real-ip": `test-${source}`,
      ...(token ? { cookie: `${SESSION_COOKIE}=${token}` } : {}),
    },
    body: JSON.stringify(body),
  }));
}

describe("optional local authentication", () => {
  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-auth-state-"));
    sessionDir = await mkdtemp(path.join(os.tmpdir(), "ksi-auth-sessions-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_SESSION_DIR", sessionDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "");
    vi.stubEnv("KSI_CCTV_WEB_SETUP_TOKEN", "t".repeat(32));
    vi.stubEnv("KSI_CCTV_TRUST_PROXY", "true");
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(authDir, { recursive: true, force: true });
    await rm(sessionDir, { recursive: true, force: true });
  });

  it("distinguishes open, environment, local, and malformed local state without leaking secrets", async () => {
    expect(await protectionStatus()).toEqual({ mode: "open", protected: false, management: true });
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "environment-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    expect(await protectionStatus()).toEqual({ mode: "environment", protected: true, management: false });
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "");
    expect(await setupLocalProtection("local-password-123")).toBe("created");
    expect(await protectionStatus()).toEqual({ mode: "local", protected: true, management: true });
    const persisted = await readFile(path.join(authDir, "auth.json"), "utf8");
    expect(persisted).not.toContain("local-password-123");
    await writeFile(path.join(authDir, "auth.json"), "not json");
    expect(await protectionStatus()).toEqual({ mode: "error", protected: true, management: false });
    const response = await GET();
    expect(JSON.stringify(await response.json())).not.toContain("local-password-123");
  });

  it("persists local credentials across reads and permits exactly one concurrent initial setup", async () => {
    const results = await Promise.all(Array.from({ length: 6 }, () => setupLocalProtection("local-password-123")));
    expect(results.filter((result) => result === "created")).toHaveLength(1);
    expect(results.filter((result) => result === "conflict")).toHaveLength(5);
    expect(await verifyPassword("local-password-123")).toBe(true);
    expect(await protectionStatus()).toMatchObject({ mode: "local" });
  });

  it("rejects every committed environment password placeholder", async () => {
    for (const placeholder of ["change-me", "change-me-web-password", "replace-with-installer-password"]) {
      vi.stubEnv("KSI_CCTV_WEB_PASSWORD", placeholder);
      expect(await protectionStatus()).toEqual({ mode: "open", protected: false, management: true });
    }
  });

  it("rejects passwords whose outer whitespace would be trimmed at login", async () => {
    expect(await setupLocalProtection(" local-password-123")).toBe("invalid");
    expect(await setupLocalProtection("local-password-123 ")).toBe("invalid");
    expect(await protectionStatus()).toMatchObject({ mode: "open" });
  });

  it("fails closed for environment authentication without an independent clean secret", async () => {
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "environment-password");
    expect(await protectionStatus()).toMatchObject({ mode: "error", protected: true });
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", " environment-password");
    expect(await protectionStatus()).toMatchObject({ mode: "error", protected: true });
  });

  it("rotates the local session secret when the password changes", async () => {
    await setupLocalProtection("local-password-123");
    const oldSession = await createSession();
    expect(await changeLocalPassword("local-password-123", "changed-password-123")).toBe("changed");
    expect(await verifySession(oldSession)).toBe(false);
    expect(await verifyPassword("changed-password-123")).toBe(true);
  });

  it("serializes concurrent password changes and protection disable requests", async () => {
    await setupLocalProtection("local-password-123");
    const [change, disable] = await Promise.all([
      changeLocalPassword("local-password-123", "changed-password-123"),
      import("@/lib/auth").then(({ disableLocalProtection }) => disableLocalProtection("local-password-123")),
    ]);

    expect([change, disable].filter((result) => result === "changed" || result === "disabled")).toHaveLength(1);
    expect([change, disable]).toContain("conflict");
    const status = await protectionStatus();
    if (change === "changed") expect(status.mode).toBe("local");
    if (disable === "disabled") expect(status.mode).toBe("open");
  });

  it("prevents a concurrent setup from recreating protection during disable", async () => {
    await setupLocalProtection("local-password-123");
    const [disable, setup] = await Promise.all([
      import("@/lib/auth").then(({ disableLocalProtection }) => disableLocalProtection("local-password-123")),
      setupLocalProtection("another-password-123"),
    ]);

    expect([disable, setup]).toContain("conflict");
    if (disable === "disabled") {
      expect(["exists", "conflict"]).toContain(setup);
      expect(await protectionStatus()).toMatchObject({ mode: "open" });
    } else {
      expect(setup).toBe("exists");
      expect(await protectionStatus()).toMatchObject({ mode: "local" });
    }
  });

  it("recovers an abandoned local mutation lock after its bounded lifetime", async () => {
    await writeFile(
      path.join(authDir, "auth.json.lock"),
      JSON.stringify({ version: 1, owner: "a".repeat(32), createdAt: 0 }),
      { mode: 0o600 },
    );

    expect(await setupLocalProtection("local-password-123")).toBe("created");
    expect(await protectionStatus()).toMatchObject({ mode: "local" });
  });

  it("supports setup, change, and disable through the API and revokes sessions", async () => {
    const setup = await request({ action: "setup", newPassword: "local-password-123", setupToken: "t".repeat(32) });
    const setupToken = setup.cookies.get(SESSION_COOKIE)?.value;
    expect(setup.status).toBe(200);
    expect(setupToken).toBeTruthy();

    const change = await request({ action: "change", currentPassword: "local-password-123", newPassword: "changed-password-123" }, setupToken);
    expect(change.status).toBe(200);
    expect(await verifySession(setupToken)).toBe(false);

    const current = await createSession();
    const disable = await request({ action: "disable", currentPassword: "changed-password-123" }, current);
    expect(disable.status).toBe(200);
    expect(await protectionStatus()).toMatchObject({ mode: "open" });
    expect(await verifySession(current)).toBe(false);
  });

  it("rejects cross-origin requests and environment mutations without exposing credentials", async () => {
    const crossOrigin = await request({ action: "setup", password: "local-password-123", setupToken: "t".repeat(32) }, undefined, "https://attacker.invalid");
    expect(crossOrigin.status).toBe(403);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "environment-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    const response = await request({ action: "setup", password: "local-password-123" });
    expect(response.status).toBe(409);
    expect(JSON.stringify(await response.json())).not.toContain("environment-password");
  });

  it("requires same-origin browser context and a trusted token for initial setup", async () => {
    const noOrigin = await request({ action: "setup", password: "local-password-123", setupToken: "t".repeat(32) }, undefined, null);
    expect(noOrigin.status).toBe(403);

    const noToken = await request({ action: "setup", password: "local-password-123" });
    expect(noToken.status).toBe(403);
    expect(await protectionStatus()).toMatchObject({ mode: "open" });

    const wrongToken = await request({ action: "setup", password: "local-password-123", setupToken: "x".repeat(32) });
    expect(wrongToken.status).toBe(403);
    expect(JSON.stringify(await wrongToken.json())).not.toContain("t".repeat(32));
    expect(await protectionStatus()).toMatchObject({ mode: "open" });
  });
});
