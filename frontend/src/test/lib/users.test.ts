import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createUser,
  deleteUser,
  findUserByUsername,
  listUsers,
  resetUserPassword,
  roleForUserId,
  updateUser,
  verifyUserPassword,
} from "@/lib/users";

const PASSWORD = "local-password-123";
const OTHER_PASSWORD = "another-password-456";

let authDir = "";

beforeEach(async () => {
  authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-users-"));
  vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
  vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "");
  vi.stubEnv("KSI_CCTV_WEB_SECRET", "");
});

afterEach(async () => {
  vi.unstubAllEnvs();
  await rm(authDir, { recursive: true, force: true });
});

describe("multi-user store (users.json)", () => {
  it("creates a user and lists it without exposing the plaintext password", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");

    const users = await listUsers();
    expect(users).toHaveLength(1);
    expect(users[0]).toMatchObject({ username: "op1", role: "operator", enabled: true });
    expect(typeof users[0]?.id).toBe("string");
    expect(typeof users[0]?.createdAt).toBe("number");
    expect(typeof users[0]?.updatedAt).toBe("number");

    const found = await findUserByUsername("op1");
    expect(found).not.toBe("error");
    expect(found).toMatchObject({ username: "op1", role: "operator", enabled: true });
    if (found && found !== "error") {
      expect(await verifyUserPassword(found, PASSWORD)).toBe(true);
      expect(await verifyUserPassword(found, "wrong-password-789")).toBe(false);
    }

    const persisted = await readFile(path.join(authDir, "users.json"), "utf8");
    expect(persisted).not.toContain(PASSWORD);
    const parsed = JSON.parse(persisted) as { version: number; users: unknown[] };
    expect(parsed.version).toBe(1);
    expect(parsed.users).toHaveLength(1);
  });

  it("stores the file with 0600 and the directory with 0700", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    expect((await stat(path.join(authDir, "users.json"))).mode & 0o777).toBe(0o600);
    expect((await stat(authDir)).mode & 0o777).toBe(0o700);
  });

  it("rejects duplicate usernames", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    expect(await createUser("op1", "viewer", OTHER_PASSWORD)).toBe("exists");
    expect(await listUsers()).toHaveLength(1);
  });

  it("validates username shape", async () => {
    for (const username of ["", "a/b", "a b", "한글", "a".repeat(65), "op@1", "op!1"]) {
      expect(await createUser(username, "viewer", PASSWORD)).toBe("invalid");
    }
    expect(await createUser("a".repeat(64), "viewer", PASSWORD)).toBe("created");
    expect(await createUser("op.1_2-3", "viewer", PASSWORD)).toBe("created");
  });

  it("refuses admin and unknown roles", async () => {
    for (const role of ["admin", "", "root", "OPERATOR", "superuser"]) {
      expect(await createUser(`user-${role || "empty"}`, role, PASSWORD)).toBe("invalid");
    }
    expect(await listUsers()).toHaveLength(0);
  });

  it("enforces the shared password policy", async () => {
    expect(await createUser("short", "viewer", "too-short")).toBe("invalid");
    expect(await createUser("leading", "viewer", ` ${PASSWORD}`)).toBe("invalid");
    expect(await createUser("trailing", "viewer", `${PASSWORD} `)).toBe("invalid");
    expect(await listUsers()).toHaveLength(0);
  });

  it("disables and re-enables a user via roleForUserId", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    expect(found).not.toBe("error");
    if (!found || found === "error") throw new Error("user not found");
    const id = found.id;

    expect(await roleForUserId(id)).toBe("operator");
    expect(await updateUser(id, { enabled: false })).toBe("updated");
    expect(await roleForUserId(id)).toBeUndefined();
    // Password verification is independent of enabled; login layer enforces enabled.
    expect(await verifyUserPassword(found, PASSWORD)).toBe(true);

    expect(await updateUser(id, { enabled: true })).toBe("updated");
    expect(await roleForUserId(id)).toBe("operator");
  });

  it("changes role and validates update input", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");

    expect(await updateUser(found.id, { role: "viewer" })).toBe("updated");
    expect(await roleForUserId(found.id)).toBe("viewer");
    expect(await updateUser(found.id, { role: "admin" })).toBe("invalid");
    expect(await updateUser(found.id, {})).toBe("invalid");
    expect(await updateUser("missing-id", { enabled: false })).toBe("missing");
    expect(await roleForUserId(found.id)).toBe("viewer");
  });

  it("resets a password", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");

    expect(await resetUserPassword(found.id, OTHER_PASSWORD)).toBe("updated");
    const refreshed = await findUserByUsername("op1");
    if (!refreshed || refreshed === "error") throw new Error("user not found");
    expect(await verifyUserPassword(refreshed, PASSWORD)).toBe(false);
    expect(await verifyUserPassword(refreshed, OTHER_PASSWORD)).toBe(true);

    expect(await resetUserPassword(found.id, "short")).toBe("invalid");
    expect(await resetUserPassword("missing-id", OTHER_PASSWORD)).toBe("missing");
  });

  it("deletes a user", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");

    expect(await deleteUser(found.id)).toBe("deleted");
    expect(await findUserByUsername("op1")).toBeUndefined();
    expect(await roleForUserId(found.id)).toBeUndefined();
    expect(await listUsers()).toHaveLength(0);
    expect(await deleteUser(found.id)).toBe("missing");
  });

  it("treats a missing store as empty", async () => {
    expect(await listUsers()).toEqual([]);
    expect(await findUserByUsername("nobody")).toBeUndefined();
    expect(await roleForUserId("nobody")).toBeUndefined();
    expect(await updateUser("nobody", { enabled: false })).toBe("missing");
    expect(await resetUserPassword("nobody", PASSWORD)).toBe("missing");
    expect(await deleteUser("nobody")).toBe("missing");
  });

  it("handles a corrupted store fail-closed", async () => {
    await writeFile(path.join(authDir, "users.json"), "not json", "utf8");
    expect(await findUserByUsername("op1")).toBe("error");
    expect(await listUsers()).toEqual([]);
    expect(await createUser("op1", "operator", PASSWORD)).toBe("conflict");
    // Corrupted content is preserved, never silently overwritten.
    expect(await readFile(path.join(authDir, "users.json"), "utf8")).toBe("not json");
  });

  it("permits exactly one winner for concurrent creates of the same username", async () => {
    const results = await Promise.all(
      Array.from({ length: 6 }, () => createUser("op1", "operator", PASSWORD)),
    );
    expect(results.filter((result) => result === "created")).toHaveLength(1);
    for (const result of results) {
      expect(["created", "exists", "conflict"]).toContain(result);
    }
    expect(await listUsers()).toHaveLength(1);
  });
});

describe("findUserById (RBAC R5b)", () => {
  it("finds a user by id", async () => {
    const { findUserById } = await import("@/lib/users");
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    const created = await findUserByUsername("op1");
    if (!created || created === "error") throw new Error("user not found");
    const found = await findUserById(created.id);
    expect(found).not.toBe("error");
    expect(found).toMatchObject({ id: created.id, username: "op1", role: "operator" });
  });

  it("returns undefined for unknown or invalid ids and error for a corrupted store", async () => {
    const { findUserById } = await import("@/lib/users");
    expect(await findUserById("nobody")).toBeUndefined();
    expect(await findUserById("")).toBeUndefined();
    await writeFile(path.join(authDir, "users.json"), "not json", "utf8");
    expect(await findUserById("whatever")).toBe("error");
  });
});

describe("reserved usernames (F1-H1)", () => {
  it("rejects each reserved username as invalid", async () => {
    for (const username of ["admin", "system", "api-key", "api_key", "apikey", "unknown", "anonymous"]) {
      expect(await createUser(username, "viewer", PASSWORD)).toBe("invalid");
    }
    expect(await listUsers()).toHaveLength(0);
  });

  it("compares case-insensitively with separator normalization", async () => {
    for (const username of ["Admin", "ADMIN", "System", "SYSTEM", "API-KEY", "Api_Key", "APIKEY", "Unknown", "UNKNOWN", "Anonymous", "ANONYMOUS", "api.key", "a-d-m-i-n", "a_dmin"]) {
      expect(await createUser(username, "viewer", PASSWORD)).toBe("invalid");
    }
    expect(await listUsers()).toHaveLength(0);
  });

  it("allows ordinary names that merely contain reserved words", async () => {
    for (const username of ["adminuser", "administrator", "ops-admin-2", "myanonymous", "unknown2", "apikeys"]) {
      expect(await createUser(username, "viewer", PASSWORD)).toBe("created");
    }
    expect(await listUsers()).toHaveLength(6);
  });
});

describe("sessionVersion (F1-H2)", () => {
  async function userId(username: string): Promise<string> {
    const found = await findUserByUsername(username);
    if (!found || found === "error") throw new Error(`user not found: ${username}`);
    return found.id;
  }

  it("starts new users at sessionVersion 1", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");
    expect(found.sessionVersion).toBe(1);
  });

  it("bumps sessionVersion on password reset", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    const id = await userId("op1");
    expect(await resetUserPassword(id, OTHER_PASSWORD)).toBe("updated");
    const refreshed = await findUserByUsername("op1");
    if (!refreshed || refreshed === "error") throw new Error("user not found");
    expect(refreshed.sessionVersion).toBe(2);
    expect(await verifyUserPassword(refreshed, OTHER_PASSWORD)).toBe(true);
  });

  it("bumps sessionVersion only on the transition to disabled", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    const id = await userId("op1");
    expect(await updateUser(id, { enabled: false })).toBe("updated");
    const disabled = await findUserByUsername("op1");
    if (!disabled || disabled === "error") throw new Error("user not found");
    expect(disabled.sessionVersion).toBe(2);
    // Repeating disabled is not a transition — no further bump.
    expect(await updateUser(id, { enabled: false })).toBe("updated");
    const stillDisabled = await findUserByUsername("op1");
    if (!stillDisabled || stillDisabled === "error") throw new Error("user not found");
    expect(stillDisabled.sessionVersion).toBe(2);
    // Re-enabling does not bump either; the disable already revoked old sessions.
    expect(await updateUser(id, { enabled: true })).toBe("updated");
    const reenabled = await findUserByUsername("op1");
    if (!reenabled || reenabled === "error") throw new Error("user not found");
    expect(reenabled.sessionVersion).toBe(2);
  });

  it("treats a missing sessionVersion as 1 for backward compatibility", async () => {
    expect(await createUser("op1", "operator", PASSWORD)).toBe("created");
    const raw = JSON.parse(await readFile(path.join(authDir, "users.json"), "utf8")) as {
      version: number;
      users: Record<string, unknown>[];
    };
    for (const user of raw.users) delete user.sessionVersion;
    await writeFile(path.join(authDir, "users.json"), JSON.stringify(raw), "utf8");
    const found = await findUserByUsername("op1");
    if (!found || found === "error") throw new Error("user not found");
    expect(found.sessionVersion ?? 1).toBe(1);
  });
});
