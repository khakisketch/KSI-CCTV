import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSession, revokeSession, sessionPrincipal, sessionTtlSeconds, verifySession } from "@/lib/auth";
import { createUser, deleteUser, findUserByUsername, resetUserPassword, updateUser } from "@/lib/users";

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

describe("session principals (RBAC R2)", () => {
  const USER_PASSWORD = "operator-pass-123";
  let authDir = "";

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-auth-r2-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "test-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(authDir, { recursive: true, force: true });
  });

  async function sessionFileFor(token: string): Promise<string> {
    return path.join(authDir, "sessions", createHash("sha256").update(token).digest("hex"));
  }

  it("creates a personal session whose principal resolves the user role", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");

    const token = await createSession(3600, found.id);
    try {
      expect(await verifySession(token)).toBe(true);
      expect(await sessionPrincipal(token)).toEqual({ role: "operator", userId: found.id });

      const stored = JSON.parse(await readFile(await sessionFileFor(token), "utf8")) as Record<string, unknown>;
      expect(stored.exp).toBe(Number(token.split(".")[1]));
      expect(stored.userId).toBe(found.id);
    } finally {
      await revokeSession(token);
    }
  });

  it("records admin sessions as JSON without userId", async () => {
    const token = await createSession(3600);
    try {
      const stored = JSON.parse(await readFile(await sessionFileFor(token), "utf8")) as Record<string, unknown>;
      expect(stored.exp).toBe(Number(token.split(".")[1]));
      expect("userId" in stored).toBe(false);
      expect(await sessionPrincipal(token)).toEqual({ role: "admin" });
      expect(await verifySession(token)).toBe(true);
    } finally {
      await revokeSession(token);
    }
  });

  it("accepts legacy plaintext-expiry sessions as admin", async () => {
    const token = await createSession(3600);
    const exp = token.split(".")[1] ?? "";
    await writeFile(await sessionFileFor(token), exp, "utf8");
    try {
      expect(await verifySession(token)).toBe(true);
      expect(await sessionPrincipal(token)).toEqual({ role: "admin" });
    } finally {
      await revokeSession(token);
    }
  });

  it("returns null for sessions of disabled or deleted users", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");

    const token = await createSession(3600, found.id);
    try {
      expect(await updateUser(found.id, { enabled: false })).toBe("updated");
      expect(await sessionPrincipal(token)).toBeNull();
      expect(await verifySession(token)).toBe(false);

      // 비활성 전환 시 세션 세대가 증가하므로, 재활성화 후에도 이전 세션은
      // 부활하지 않는다(F1-H2: 세션 회수 — 재로그인 필요).
      expect(await updateUser(found.id, { enabled: true })).toBe("updated");
      expect(await sessionPrincipal(token)).toBeNull();
      expect(await verifySession(token)).toBe(false);

      // 재로그인한 새 세션은 유효하다.
      const fresh = await createSession(3600, found.id);
      try {
        expect(await sessionPrincipal(fresh)).toEqual({ role: "operator", userId: found.id });
      } finally {
        await revokeSession(fresh);
      }

      expect(await deleteUser(found.id)).toBe("deleted");
      expect(await sessionPrincipal(token)).toBeNull();
      expect(await verifySession(token)).toBe(false);
    } finally {
      await revokeSession(token);
    }
  });

  it("returns null for corrupted or mismatched session records", async () => {
    const token = await createSession(3600);
    const file = await sessionFileFor(token);
    try {
      await writeFile(file, "not json", "utf8");
      expect(await sessionPrincipal(token)).toBeNull();
      expect(await verifySession(token)).toBe(false);

      await writeFile(file, JSON.stringify({ exp: 1, userId: "missing" }), "utf8");
      expect(await sessionPrincipal(token)).toBeNull();
      expect(await verifySession(token)).toBe(false);
    } finally {
      await revokeSession(token);
    }
  });
});

describe("sessionVersion revocation (F1-H2)", () => {
  const USER_PASSWORD = "operator-pass-123";
  const NEW_PASSWORD = "operator-pass-456";
  let authDir = "";

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-auth-sv-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "test-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(authDir, { recursive: true, force: true });
  });

  async function sessionFileFor(token: string): Promise<string> {
    return path.join(authDir, "sessions", createHash("sha256").update(token).digest("hex"));
  }

  it("invalidates the old session on password reset while a fresh login stays valid", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");

    const oldToken = await createSession(3600, found.id);
    try {
      expect(await sessionPrincipal(oldToken)).toEqual({ role: "operator", userId: found.id });
      expect(await resetUserPassword(found.id, NEW_PASSWORD)).toBe("updated");
      expect(await sessionPrincipal(oldToken)).toBeNull();
      expect(await verifySession(oldToken)).toBe(false);

      const fresh = await createSession(3600, found.id);
      try {
        expect(await sessionPrincipal(fresh)).toEqual({ role: "operator", userId: found.id });
        expect(await verifySession(fresh)).toBe(true);
      } finally {
        await revokeSession(fresh);
      }
    } finally {
      await revokeSession(oldToken);
    }
  });

  it("keeps the old session revoked after disable followed by re-enable", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");

    const oldToken = await createSession(3600, found.id);
    try {
      expect(await updateUser(found.id, { enabled: false })).toBe("updated");
      expect(await sessionPrincipal(oldToken)).toBeNull();
      expect(await updateUser(found.id, { enabled: true })).toBe("updated");
      expect(await sessionPrincipal(oldToken)).toBeNull();
      expect(await verifySession(oldToken)).toBe(false);
    } finally {
      await revokeSession(oldToken);
    }
  });

  it("treats sessions and users without version fields as version 1", async () => {
    expect(await createUser("op1", "operator", USER_PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");

    const token = await createSession(3600, found.id);
    try {
      // 세션 파일에서 sv를 제거하고 users.json에서 sessionVersion을 제거해
      // 구버전 저장소 상태를 재현한다.
      const stored = JSON.parse(await readFile(await sessionFileFor(token), "utf8")) as Record<string, unknown>;
      await writeFile(
        await sessionFileFor(token),
        JSON.stringify({ exp: stored.exp, userId: stored.userId }),
        "utf8",
      );
      const raw = JSON.parse(await readFile(path.join(authDir, "users.json"), "utf8")) as {
        version: number;
        users: Record<string, unknown>[];
      };
      for (const user of raw.users) delete user.sessionVersion;
      await writeFile(path.join(authDir, "users.json"), JSON.stringify(raw), "utf8");

      expect(await sessionPrincipal(token)).toEqual({ role: "operator", userId: found.id });
      expect(await verifySession(token)).toBe(true);
    } finally {
      await revokeSession(token);
    }
  });
});
