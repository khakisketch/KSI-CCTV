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
