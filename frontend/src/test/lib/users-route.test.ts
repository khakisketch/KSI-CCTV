import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET as listUsersRoute, POST as createUserRoute } from "@/app/api/users/route";
import { DELETE as deleteUserRoute, PATCH as patchUserRoute } from "@/app/api/users/[id]/route";
import { POST as resetPasswordRoute } from "@/app/api/users/[id]/reset-password/route";
import { createSession, SESSION_COOKIE } from "@/lib/auth";
import { createUser, findUserByUsername } from "@/lib/users";

const VALID_PASSWORD = "operator-pass-123";

describe("users routes (RBAC R6)", () => {
  let authDir = "";
  let adminToken = "";
  let operatorToken = "";

  async function userId(username: string): Promise<string> {
    const found = await findUserByUsername(username);
    if (!found || found === "error") throw new Error(`user not found: ${username}`);
    return found.id;
  }

  function requestWith(url: string, token: string | undefined, init?: { method?: string; headers?: HeadersInit; body?: string }) {
    const headers = new Headers(init?.headers);
    if (token) headers.set("cookie", `${SESSION_COOKIE}=${token}`);
    return new NextRequest(url, { method: init?.method, headers, body: init?.body });
  }

  function params(id: string) {
    return { params: Promise.resolve({ id }) };
  }

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-users-route-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "users-route-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    expect(await createUser("route-op", "operator", VALID_PASSWORD)).toBe("created");
    adminToken = await createSession(3600);
    operatorToken = await createSession(3600, await userId("route-op"));
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(authDir, { recursive: true, force: true });
  });

  it("returns 401 with no-store when unauthenticated", async () => {
    const response = await listUsersRoute(requestWith("http://localhost:3000/api/users", undefined));

    expect(response.status).toBe(401);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect((await response.json()).detail).toBeTruthy();
  });

  it("returns 403 when a non-admin requests the list", async () => {
    const response = await listUsersRoute(requestWith("http://localhost:3000/api/users", operatorToken));

    expect(response.status).toBe(403);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
  });

  it("lists sanitized users without credential material", async () => {
    const response = await listUsersRoute(requestWith("http://localhost:3000/api/users", adminToken));

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    const body = await response.json() as { users: Record<string, unknown>[] };
    expect(Array.isArray(body.users)).toBe(true);
    const entry = body.users.find((user) => user.username === "route-op");
    expect(entry).toMatchObject({ username: "route-op", role: "operator", enabled: true });
    expect(entry).toHaveProperty("id");
    expect(entry).toHaveProperty("createdAt");
    expect(entry).toHaveProperty("updatedAt");
    expect(entry).not.toHaveProperty("passwordHash");
    expect(entry).not.toHaveProperty("salt");
  });

  it("never exposes sessionVersion in user DTOs (F1-H2)", async () => {
    const list = await listUsersRoute(requestWith("http://localhost:3000/api/users", adminToken));
    expect(list.status).toBe(200);
    const listBody = await list.json() as { users: Record<string, unknown>[] };
    expect(listBody.users.length).toBeGreaterThan(0);
    for (const user of listBody.users) {
      expect(user).not.toHaveProperty("sessionVersion");
    }

    const created = await createUserRoute(requestWith("http://localhost:3000/api/users", adminToken, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "dto-op", role: "viewer", password: VALID_PASSWORD }),
    }));
    expect(created.status).toBe(201);
    expect((await created.json() as { user: Record<string, unknown> }).user).not.toHaveProperty("sessionVersion");

    const id = await userId("route-op");
    const patched = await patchUserRoute(
      requestWith(`http://localhost:3000/api/users/${id}`, adminToken, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: false }),
      }),
      params(id),
    );
    expect(patched.status).toBe(200);
    expect((await patched.json() as { user: Record<string, unknown> }).user).not.toHaveProperty("sessionVersion");
  });

  it("creates a user and returns 201 without credential material", async () => {
    const response = await createUserRoute(requestWith("http://localhost:3000/api/users", adminToken, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "new-op", role: "operator", password: VALID_PASSWORD }),
    }));

    expect(response.status).toBe(201);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    const body = await response.json() as { user: Record<string, unknown> };
    expect(body.user).toMatchObject({ username: "new-op", role: "operator", enabled: true });
    expect(body.user).not.toHaveProperty("passwordHash");
    expect(body.user).not.toHaveProperty("salt");
  });

  it("rejects creation with 400 for invalid role or password", async () => {
    const badRole = await createUserRoute(requestWith("http://localhost:3000/api/users", adminToken, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "bad-role", role: "admin", password: VALID_PASSWORD }),
    }));
    const badPassword = await createUserRoute(requestWith("http://localhost:3000/api/users", adminToken, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "bad-pw", role: "viewer", password: "short" }),
    }));

    expect(badRole.status).toBe(400);
    expect(badPassword.status).toBe(400);
    expect((await badRole.json()).detail).toBeTruthy();
    expect((await badPassword.json()).detail).toBeTruthy();
  });

  it("returns 409 when the username already exists", async () => {
    const response = await createUserRoute(requestWith("http://localhost:3000/api/users", adminToken, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "route-op", role: "viewer", password: VALID_PASSWORD }),
    }));

    expect(response.status).toBe(409);
    expect((await response.json()).detail).toBeTruthy();
  });

  it("returns 409 when the store is corrupted", async () => {
    await writeFile(path.join(authDir, "users.json"), "{broken", "utf8");
    const response = await createUserRoute(requestWith("http://localhost:3000/api/users", adminToken, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "late-op", role: "operator", password: VALID_PASSWORD }),
    }));

    expect(response.status).toBe(409);
  });

  it("patches role and enabled state, 404 on missing, 400 on invalid", async () => {
    const id = await userId("route-op");
    const roleResponse = await patchUserRoute(
      requestWith(`http://localhost:3000/api/users/${id}`, adminToken, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "viewer" }),
      }),
      params(id),
    );
    expect(roleResponse.status).toBe(200);
    expect(roleResponse.headers.get("Cache-Control")).toBe("no-store");
    const roleBody = await roleResponse.json() as { user: Record<string, unknown> };
    expect(roleBody.user).toMatchObject({ id, role: "viewer" });
    expect(roleBody.user).not.toHaveProperty("passwordHash");

    const disableResponse = await patchUserRoute(
      requestWith(`http://localhost:3000/api/users/${id}`, adminToken, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: false }),
      }),
      params(id),
    );
    expect(disableResponse.status).toBe(200);
    expect((await disableResponse.json() as { user: Record<string, unknown> }).user)
      .toMatchObject({ id, enabled: false });

    const missing = await patchUserRoute(
      requestWith("http://localhost:3000/api/users/no-such-id", adminToken, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "viewer" }),
      }),
      params("no-such-id"),
    );
    expect(missing.status).toBe(404);

    const invalid = await patchUserRoute(
      requestWith(`http://localhost:3000/api/users/${id}`, adminToken, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: "superadmin" }),
      }),
      params(id),
    );
    expect(invalid.status).toBe(400);
  });

  it("deletes a user and returns 404 for unknown ids", async () => {
    expect(await createUser("gone-op", "viewer", VALID_PASSWORD)).toBe("created");
    const id = await userId("gone-op");
    const response = await deleteUserRoute(
      requestWith(`http://localhost:3000/api/users/${id}`, adminToken, { method: "DELETE" }),
      params(id),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");

    const again = await deleteUserRoute(
      requestWith(`http://localhost:3000/api/users/${id}`, adminToken, { method: "DELETE" }),
      params(id),
    );
    expect(again.status).toBe(404);
  });

  it("resets a password and rejects invalid input or unknown ids", async () => {
    const id = await userId("route-op");
    const response = await resetPasswordRoute(
      requestWith(`http://localhost:3000/api/users/${id}/reset-password`, adminToken, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: "brand-new-pass-456" }),
      }),
      params(id),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");

    const invalid = await resetPasswordRoute(
      requestWith(`http://localhost:3000/api/users/${id}/reset-password`, adminToken, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: "short" }),
      }),
      params(id),
    );
    expect(invalid.status).toBe(400);

    const missing = await resetPasswordRoute(
      requestWith("http://localhost:3000/api/users/no-such-id/reset-password", adminToken, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: VALID_PASSWORD }),
      }),
      params("no-such-id"),
    );
    expect(missing.status).toBe(404);
  });

  it("denies operator access to mutation routes", async () => {
    const id = await userId("route-op");
    const create = await createUserRoute(requestWith("http://localhost:3000/api/users", operatorToken, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "nope", role: "viewer", password: VALID_PASSWORD }),
    }));
    const patch = await patchUserRoute(
      requestWith(`http://localhost:3000/api/users/${id}`, operatorToken, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: false }),
      }),
      params(id),
    );
    const remove = await deleteUserRoute(
      requestWith(`http://localhost:3000/api/users/${id}`, operatorToken, { method: "DELETE" }),
      params(id),
    );
    const reset = await resetPasswordRoute(
      requestWith(`http://localhost:3000/api/users/${id}/reset-password`, operatorToken, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: VALID_PASSWORD }),
      }),
      params(id),
    );

    expect(create.status).toBe(403);
    expect(patch.status).toBe(403);
    expect(remove.status).toBe(403);
    expect(reset.status).toBe(403);
  });

  it("audits user_created with username and role without password material", async () => {
    const engine = await import("@/lib/engine");
    const spy = vi.spyOn(engine, "engineWrite").mockResolvedValue({ ok: true, status: 200, data: null });
    try {
      const password = "audit-create-pass-123";
      const response = await createUserRoute(requestWith("http://localhost:3000/api/users", adminToken, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "audit-op", role: "operator", password }),
      }));

      expect(response.status).toBe(201);
      expect(spy).toHaveBeenCalledTimes(1);
      const [path, body, method, timeoutMs, actor] = spy.mock.calls[0] as unknown as [
        string, Record<string, unknown>, string, number, string,
      ];
      expect(path).toBe("/api/audit/entry");
      expect(method).toBe("POST");
      expect(timeoutMs).toBe(8000);
      expect(actor).toBe("admin");
      expect(body).toMatchObject({ event: "user_created", fields: { username: "audit-op", role: "operator" } });
      expect(JSON.stringify(spy.mock.calls)).not.toContain(password);
      expect(JSON.stringify(body)).not.toContain("password");
    } finally {
      spy.mockRestore();
    }
  });

  it("audits user_updated with only the requested change keys", async () => {
    const engine = await import("@/lib/engine");
    const spy = vi.spyOn(engine, "engineWrite").mockResolvedValue({ ok: true, status: 200, data: null });
    try {
      const id = await userId("route-op");
      const response = await patchUserRoute(
        requestWith(`http://localhost:3000/api/users/${id}`, adminToken, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: "viewer" }),
        }),
        params(id),
      );

      expect(response.status).toBe(200);
      expect(spy).toHaveBeenCalledTimes(1);
      const [path, body, method, , actor] = spy.mock.calls[0] as unknown as [
        string, Record<string, unknown>, string, number, string,
      ];
      expect(path).toBe("/api/audit/entry");
      expect(method).toBe("POST");
      expect(actor).toBe("admin");
      expect(body).toMatchObject({
        event: "user_updated",
        fields: { username: "route-op", changes: { role: "viewer" } },
      });
      expect((body.fields as { changes: Record<string, unknown> }).changes).not.toHaveProperty("enabled");
    } finally {
      spy.mockRestore();
    }
  });

  it("audits user_deleted with username", async () => {
    const engine = await import("@/lib/engine");
    const spy = vi.spyOn(engine, "engineWrite").mockResolvedValue({ ok: true, status: 200, data: null });
    try {
      expect(await createUser("audit-gone", "viewer", VALID_PASSWORD)).toBe("created");
      const id = await userId("audit-gone");
      const response = await deleteUserRoute(
        requestWith(`http://localhost:3000/api/users/${id}`, adminToken, { method: "DELETE" }),
        params(id),
      );

      expect(response.status).toBe(200);
      expect(spy).toHaveBeenCalledTimes(1);
      const [path, body, method, , actor] = spy.mock.calls[0] as unknown as [
        string, Record<string, unknown>, string, number, string,
      ];
      expect(path).toBe("/api/audit/entry");
      expect(method).toBe("POST");
      expect(actor).toBe("admin");
      expect(body).toMatchObject({ event: "user_deleted", fields: { username: "audit-gone" } });
    } finally {
      spy.mockRestore();
    }
  });

  it("audits user_password_reset without password material", async () => {
    const engine = await import("@/lib/engine");
    const spy = vi.spyOn(engine, "engineWrite").mockResolvedValue({ ok: true, status: 200, data: null });
    try {
      const id = await userId("route-op");
      const password = "audit-reset-pass-456";
      const response = await resetPasswordRoute(
        requestWith(`http://localhost:3000/api/users/${id}/reset-password`, adminToken, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password }),
        }),
        params(id),
      );

      expect(response.status).toBe(200);
      expect(spy).toHaveBeenCalledTimes(1);
      const [path, body, method, , actor] = spy.mock.calls[0] as unknown as [
        string, Record<string, unknown>, string, number, string,
      ];
      expect(path).toBe("/api/audit/entry");
      expect(method).toBe("POST");
      expect(actor).toBe("admin");
      expect(body).toMatchObject({ event: "user_password_reset", fields: { username: "route-op" } });
      expect(JSON.stringify(spy.mock.calls)).not.toContain(password);
    } finally {
      spy.mockRestore();
    }
  });

  it("keeps 201 when the audit call fails", async () => {
    const engine = await import("@/lib/engine");
    const spy = vi.spyOn(engine, "engineWrite").mockRejectedValue(new Error("engine down"));
    try {
      const response = await createUserRoute(requestWith("http://localhost:3000/api/users", adminToken, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "audit-resilient", role: "viewer", password: VALID_PASSWORD }),
      }));

      expect(response.status).toBe(201);
      const body = await response.json() as { user: Record<string, unknown> };
      expect(body.user).toMatchObject({ username: "audit-resilient" });
    } finally {
      spy.mockRestore();
    }
  });

  it("keeps 200 on password reset when the audit call reports failure", async () => {
    const engine = await import("@/lib/engine");
    const spy = vi.spyOn(engine, "engineWrite").mockResolvedValue({ ok: false, status: 0, data: null });
    try {
      const id = await userId("route-op");
      const response = await resetPasswordRoute(
        requestWith(`http://localhost:3000/api/users/${id}/reset-password`, adminToken, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: "audit-keep-pass-789" }),
        }),
        params(id),
      );

      expect(response.status).toBe(200);
      expect((await response.json()).detail).toBeTruthy();
    } finally {
      spy.mockRestore();
    }
  });

  it("warns on audit failure (ok:false) but keeps 201 on create without sensitive data (F2-M1)", async () => {
    const engine = await import("@/lib/engine");
    const spy = vi.spyOn(engine, "engineWrite").mockResolvedValue({ ok: false, status: 503, data: null });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const response = await createUserRoute(requestWith("http://localhost:3000/api/users", adminToken, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "audit-warn-op", role: "viewer", password: VALID_PASSWORD }),
      }));

      expect(response.status).toBe(201);
      expect(warn).toHaveBeenCalledTimes(1);
      const message = String(warn.mock.calls[0]?.[0] ?? "");
      expect(message).toContain("user_created");
      expect(message).not.toContain(VALID_PASSWORD);
      expect(message).not.toContain("audit-warn-op");
    } finally {
      spy.mockRestore();
      warn.mockRestore();
    }
  });

  it("warns on audit throw but keeps 200 on patch without sensitive data (F2-M1)", async () => {
    const engine = await import("@/lib/engine");
    const spy = vi.spyOn(engine, "engineWrite").mockRejectedValue(new Error("engine down"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const id = await userId("route-op");
      const response = await patchUserRoute(
        requestWith(`http://localhost:3000/api/users/${id}`, adminToken, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: false }),
        }),
        params(id),
      );

      expect(response.status).toBe(200);
      expect(warn).toHaveBeenCalledTimes(1);
      expect(String(warn.mock.calls[0]?.[0] ?? "")).toContain("user_updated");
    } finally {
      spy.mockRestore();
      warn.mockRestore();
    }
  });

  it("warns on audit failure (ok:false) but keeps 200 on delete and reset (F2-M1)", async () => {
    const engine = await import("@/lib/engine");
    const spy = vi.spyOn(engine, "engineWrite").mockResolvedValue({ ok: false, status: 500, data: null });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      expect(await createUser("audit-warn-gone", "viewer", VALID_PASSWORD)).toBe("created");
      const deleteId = await userId("audit-warn-gone");
      const deleted = await deleteUserRoute(
        requestWith(`http://localhost:3000/api/users/${deleteId}`, adminToken, { method: "DELETE" }),
        params(deleteId),
      );
      expect(deleted.status).toBe(200);
      expect(warn).toHaveBeenCalledTimes(1);
      expect(String(warn.mock.calls[0]?.[0] ?? "")).toContain("user_deleted");

      warn.mockClear();
      const resetId = await userId("route-op");
      const resetPassword = "audit-warn-reset-123";
      const reset = await resetPasswordRoute(
        requestWith(`http://localhost:3000/api/users/${resetId}/reset-password`, adminToken, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: resetPassword }),
        }),
        params(resetId),
      );
      expect(reset.status).toBe(200);
      expect(warn).toHaveBeenCalledTimes(1);
      const resetMessage = String(warn.mock.calls[0]?.[0] ?? "");
      expect(resetMessage).toContain("user_password_reset");
      expect(resetMessage).not.toContain(resetPassword);
    } finally {
      spy.mockRestore();
      warn.mockRestore();
    }
  });
});
