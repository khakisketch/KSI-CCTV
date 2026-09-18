import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST as login } from "@/app/api/auth/login/route";
import { revokeSession, SESSION_COOKIE, sessionPrincipal } from "@/lib/auth";
import { passwordHash } from "@/lib/auth-primitives";
import { createUser, findUserByUsername, updateUser } from "@/lib/users";

vi.mock("@/lib/auth-primitives", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth-primitives")>();
  return { ...actual, passwordHash: vi.fn(actual.passwordHash) };
});

const passwordHashMock = vi.mocked(passwordHash);

describe("login cookie transport", () => {
  beforeEach(() => {
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "test-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    vi.stubEnv("KSI_CCTV_WEB_SESSION_TTL_SEC", "604800");
    vi.stubEnv("KSI_CCTV_TRUST_PROXY", "false");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("ignores a spoofed forwarded protocol on a direct HTTP request", async () => {
    const response = await login(new NextRequest("http://appliance.local/api/auth/login", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-forwarded-proto": "https",
      },
      body: JSON.stringify({ password: "test-password" }),
    }));
    const token = response.cookies.get(SESSION_COOKIE)?.value;

    expect(response.status).toBe(200);
    expect(token).toBeTruthy();
    expect(response.headers.get("set-cookie")).not.toMatch(/;\s*Secure/i);
    expect(response.headers.get("set-cookie")).toContain("Max-Age=604800");
    await revokeSession(token);
  });

  it("does not count successful logins against the failed-attempt limit", async () => {
    vi.stubEnv("KSI_CCTV_TRUST_PROXY", "true");
    const tokens: string[] = [];

    for (let attempt = 0; attempt < 6; attempt += 1) {
      const response = await login(new NextRequest("http://appliance.local/api/auth/login", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-forwarded-for": "198.51.100.99",
        },
        body: JSON.stringify({ password: "test-password" }),
      }));
      const token = response.cookies.get(SESSION_COOKIE)?.value;

      expect(response.status).toBe(200);
      expect(token).toBeTruthy();
      if (token) tokens.push(token);
    }

    await Promise.all(tokens.map((token) => revokeSession(token)));
  });
});

describe("login with username (RBAC R2)", () => {
  const USER_PASSWORD = "operator-pass-123";
  let authDir = "";

  function loginRequest(ip: string, body: unknown) {
    return new NextRequest("http://appliance.local/api/auth/login", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-forwarded-for": ip,
      },
      body: JSON.stringify(body),
    });
  }

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-login-r2-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "test-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    vi.stubEnv("KSI_CCTV_WEB_SESSION_TTL_SEC", "604800");
    vi.stubEnv("KSI_CCTV_TRUST_PROXY", "true");
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(authDir, { recursive: true, force: true });
  });

  it("logs in a named user and issues a personal session", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");

    const response = await login(loginRequest("10.9.0.11", { username: "op1", password: USER_PASSWORD }));
    const token = response.cookies.get(SESSION_COOKIE)?.value;
    try {
      expect(response.status).toBe(200);
      expect(token).toBeTruthy();
      expect(await sessionPrincipal(token!)).toEqual({ role: "operator", userId: found.id });
    } finally {
      await revokeSession(token);
    }
  });

  it("rejects unknown usernames and wrong passwords without revealing which failed", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");

    const unknown = await login(loginRequest("10.9.0.12", { username: "nobody", password: USER_PASSWORD }));
    const wrong = await login(loginRequest("10.9.0.13", { username: "op1", password: "wrong-password-789" }));
    expect(unknown.status).toBe(401);
    expect(wrong.status).toBe(401);
    const unknownBody = await unknown.json();
    const wrongBody = await wrong.json();
    expect(unknownBody.detail).toBe(wrongBody.detail);
    expect(unknownBody.detail).not.toContain("nobody");
    expect(unknown.cookies.get(SESSION_COOKIE)?.value).toBeFalsy();
  });

  it("rejects disabled users without revealing the reason", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");
    expect(await updateUser(found.id, { enabled: false })).toBe("updated");

    const response = await login(loginRequest("10.9.0.14", { username: "op1", password: USER_PASSWORD }));
    expect(response.status).toBe(401);
    expect(response.cookies.get(SESSION_COOKIE)?.value).toBeFalsy();
  });

  it("keeps the admin path when username is omitted", async () => {
    const response = await login(loginRequest("10.9.0.15", { password: "test-password" }));
    const token = response.cookies.get(SESSION_COOKIE)?.value;
    try {
      expect(response.status).toBe(200);
      expect(await sessionPrincipal(token!)).toEqual({ role: "admin" });
    } finally {
      await revokeSession(token);
    }
  });
});

describe("login username timing mitigation", () => {
  const USER_PASSWORD = "operator-pass-123";
  let authDir = "";

  function timingRequest(ip: string, body: unknown) {
    return new NextRequest("http://appliance.local/api/auth/login", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-forwarded-for": ip,
      },
      body: JSON.stringify(body),
    });
  }

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-login-timing-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "test-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    vi.stubEnv("KSI_CCTV_WEB_SESSION_TTL_SEC", "604800");
    vi.stubEnv("KSI_CCTV_TRUST_PROXY", "true");
    passwordHashMock.mockClear();
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(authDir, { recursive: true, force: true });
  });

  it("runs one dummy scrypt check for an unknown username", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");
    passwordHashMock.mockClear();

    const response = await login(timingRequest("10.20.0.1", { username: "nobody", password: USER_PASSWORD }));
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "사용자명 또는 비밀번호가 올바르지 않습니다." });
    expect(passwordHashMock).toHaveBeenCalledTimes(1);
    expect(passwordHashMock.mock.calls[0]?.[0]).toBe(USER_PASSWORD);
    expect((passwordHashMock.mock.calls[0]?.[1] as Buffer).length).toBe(16);
  });

  it("runs one dummy scrypt check for a disabled user", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");
    expect(await updateUser(found.id, { enabled: false })).toBe("updated");
    passwordHashMock.mockClear();

    const response = await login(timingRequest("10.20.0.2", { username: "op1", password: USER_PASSWORD }));
    expect(response.status).toBe(401);
    expect(passwordHashMock).toHaveBeenCalledTimes(1);
    expect((passwordHashMock.mock.calls[0]?.[1] as Buffer).length).toBe(16);
  });

  it("runs one dummy scrypt check when the user store is corrupted", async () => {
    const { writeFile } = await import("node:fs/promises");
    await writeFile(path.join(authDir, "users.json"), "{corrupted", "utf8");
    passwordHashMock.mockClear();

    const response = await login(timingRequest("10.20.0.3", { username: "op1", password: USER_PASSWORD }));
    expect(response.status).toBe(401);
    expect(passwordHashMock).toHaveBeenCalledTimes(1);
    expect((passwordHashMock.mock.calls[0]?.[1] as Buffer).length).toBe(16);
  });

  it("does not add a dummy scrypt check for a valid user login", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");
    passwordHashMock.mockClear();

    const response = await login(timingRequest("10.20.0.4", { username: "op1", password: USER_PASSWORD }));
    const token = response.cookies.get(SESSION_COOKIE)?.value;
    try {
      expect(response.status).toBe(200);
      expect(passwordHashMock).toHaveBeenCalledTimes(1);
    } finally {
      await revokeSession(token);
    }
  });

  it("does not run scrypt on the admin path without username", async () => {
    passwordHashMock.mockClear();

    const response = await login(timingRequest("10.20.0.5", { password: "test-password" }));
    const token = response.cookies.get(SESSION_COOKIE)?.value;
    try {
      expect(response.status).toBe(200);
      expect(passwordHashMock).not.toHaveBeenCalled();
    } finally {
      await revokeSession(token);
    }
  });
});
