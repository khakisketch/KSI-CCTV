import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSession, revokeSession, sessionTtlSeconds, verifySession } from "@/lib/auth";

describe("server-side sessions", () => {
  beforeEach(() => {
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "test-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("issues unique active sessions and rejects a copied token after logout", async () => {
    const first = await createSession();
    const second = await createSession();

    expect(first).not.toBe(second);
    expect(await verifySession(first)).toBe(true);
    expect(await verifySession(second)).toBe(true);

    const copied = first;
    await revokeSession(first);
    expect(await verifySession(copied)).toBe(false);
    expect(await verifySession(second)).toBe(true);
    await revokeSession(second);
  });

  it("rejects tampered and expired session tokens", async () => {
    const active = await createSession();
    expect(await verifySession(`${active}0`)).toBe(false);
    await revokeSession(active);

    const expired = await createSession(-1);
    expect(await verifySession(expired)).toBe(false);
  });

  it("accepts only bounded explicit session lifetimes", () => {
    expect(sessionTtlSeconds({ KSI_CCTV_WEB_SESSION_TTL_SEC: "604800" })).toBe(604800);
    expect(sessionTtlSeconds({ KSI_CCTV_WEB_SESSION_TTL_SEC: "60" })).toBe(43200);
    expect(sessionTtlSeconds({ KSI_CCTV_WEB_SESSION_TTL_SEC: "invalid" })).toBe(43200);
  });
});
