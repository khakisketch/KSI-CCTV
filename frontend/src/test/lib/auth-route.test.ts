import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST as login } from "@/app/api/auth/login/route";
import { revokeSession, SESSION_COOKIE } from "@/lib/auth";

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
