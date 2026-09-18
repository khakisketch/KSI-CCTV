import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEVELOPMENT_AUTH_BYPASS_HEADER } from "@/lib/auth-mode";
import { createSession, SESSION_COOKIE } from "@/lib/auth";
import { createUser, findUserByUsername } from "@/lib/users";
import { proxy } from "@/proxy";

const TOKEN = "b".repeat(64);
const ORIGINAL_ARGV = [...process.argv];

function enableBypass(bind = "127.0.0.1") {
  process.argv.splice(
    0,
    process.argv.length,
    process.execPath,
    "/opt/ksi-cctv/next-with-auth-guard.mjs",
    "dev",
  );
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("KSI_CCTV_DEV_AUTH_BYPASS", "1");
  vi.stubEnv("KSI_CCTV_WEB_DEV", "1");
  vi.stubEnv("KSI_CCTV_WEB_BIND_HOST", bind);
  vi.stubEnv("KSI_CCTV_DEV_AUTH_BYPASS_TOKEN", TOKEN);
}

describe("auth bypass proxy boundary", () => {
  afterEach(() => {
    process.argv.splice(0, process.argv.length, ...ORIGINAL_ARGV);
    vi.unstubAllEnvs();
  });

  it("allows an attested launcher request without trusting its Host header", async () => {
    enableBypass();

    const response = await proxy(new NextRequest("http://example.test/cameras", {
      headers: {
        host: "example.test",
        [DEVELOPMENT_AUTH_BYPASS_HEADER]: TOKEN,
      },
    }));

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("rejects requests that did not pass through the protected launcher", async () => {
    enableBypass();

    const missing = await proxy(new NextRequest("http://localhost:3000/cameras", {
      headers: { host: "localhost:3000" },
    }));
    expect(missing.status).toBe(503);

    const forged = await proxy(new NextRequest("http://localhost:3000/cameras", {
      headers: { [DEVELOPMENT_AUTH_BYPASS_HEADER]: "c".repeat(64) },
    }));
    expect(forged.status).toBe(503);
    expect(await forged.json()).toEqual({
      detail: "개발 인증 생략은 보호된 로컬 launcher 요청에만 사용할 수 있습니다.",
    });
  });

  it("fails closed when bypass is requested in production or on an external bind", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("KSI_CCTV_DEV_AUTH_BYPASS", "1");
    vi.stubEnv("KSI_CCTV_WEB_DEV", "1");
    vi.stubEnv("KSI_CCTV_WEB_BIND_HOST", "127.0.0.1");
    vi.stubEnv("KSI_CCTV_DEV_AUTH_BYPASS_TOKEN", TOKEN);
    const production = await proxy(new NextRequest("http://127.0.0.1:3000/cameras", {
      headers: { [DEVELOPMENT_AUTH_BYPASS_HEADER]: TOKEN },
    }));
    expect(production.status).toBe(503);

    enableBypass("0.0.0.0");
    const externalBind = await proxy(new NextRequest("http://127.0.0.1:3000/cameras", {
      headers: { [DEVELOPMENT_AUTH_BYPASS_HEADER]: TOKEN },
    }));
    expect(externalBind.status).toBe(503);
  });

  it("fails closed for direct next dev even with a caller-chosen token", async () => {
    enableBypass();
    process.argv.splice(0, process.argv.length, process.execPath, "/app/node_modules/.bin/next", "dev");

    const response = await proxy(new NextRequest("http://localhost:3000/cameras", {
      headers: {
        host: "localhost:3000",
        [DEVELOPMENT_AUTH_BYPASS_HEADER]: TOKEN,
      },
    }));

    expect(response.status).toBe(503);
  });

  it("rejects cross-origin browser mutations before session or route handling", async () => {
    const response = await proxy(new NextRequest("https://cctv.example.internal/api/auth/login", {
      method: "POST",
      headers: { origin: "https://attacker.invalid", "sec-fetch-site": "cross-site" },
    }));

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ detail: "교차 출처 변경 요청은 허용되지 않습니다." });
  });

  it("rejects same-site mutations from a different origin", async () => {
    const response = await proxy(new NextRequest("https://cctv.example.internal/api/auth/login", {
      method: "POST",
      headers: { origin: "https://operator.example.internal", "sec-fetch-site": "same-site" },
    }));

    expect(response.status).toBe(403);
  });

  it("allows a browser same-origin mutation when Next normalizes a loopback host", async () => {
    const response = await proxy(new NextRequest("http://localhost:4175/api/auth/login", {
      method: "POST",
      headers: {
        origin: "http://127.0.0.1:4175",
        "sec-fetch-site": "same-origin",
      },
    }));

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("permits all routes in open mode and redirects login to the console", async () => {
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "");
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", "/tmp/ksi-cctv-open-mode-test");

    const consoleResponse = await proxy(new NextRequest("http://localhost:3000/cameras"));
    const loginResponse = await proxy(new NextRequest("http://localhost:3000/login"));

    expect(consoleResponse.headers.get("x-middleware-next")).toBe("1");
    expect(loginResponse.headers.get("location")).toBe("http://localhost:3000/");
  });

  it("keeps protected modes behind the session gate", async () => {
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "protected-password");
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", "/tmp/ksi-cctv-protected-mode-test");

    const response = await proxy(new NextRequest("http://localhost:3000/cameras"));

    expect(response.headers.get("location")).toBe("http://localhost:3000/login");
  });

  it("fails closed when the protection state cannot be read", async () => {
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "");
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", "/dev/null");

    const protectedResponse = await proxy(new NextRequest("http://localhost:3000/cameras"));
    const loginResponse = await proxy(new NextRequest("http://localhost:3000/login"));

    expect(protectedResponse.headers.get("location")).toBe("http://localhost:3000/login");
    expect(loginResponse.headers.get("x-middleware-next")).toBe("1");
  });
});

describe("proxy role gate (RBAC R3)", () => {
  let authDir = "";
  let adminToken = "";
  let operatorToken = "";
  let viewerToken = "";

  async function userId(username: string): Promise<string> {
    const found = await findUserByUsername(username);
    if (!found || found === "error") throw new Error(`user not found: ${username}`);
    return found.id;
  }

  function withSession(url: string, init?: { method?: string; headers?: HeadersInit }) {
    return proxy(new NextRequest(url, init));
  }

  function authed(url: string, token: string | undefined, init?: { method?: string; headers?: HeadersInit }) {
    const headers = new Headers(init?.headers);
    if (token) headers.set("cookie", `${SESSION_COOKIE}=${token}`);
    return proxy(new NextRequest(url, { ...init, headers }));
  }

  beforeEach(async () => {
    authDir = await mkdtemp(path.join(os.tmpdir(), "ksi-proxy-rbac-"));
    vi.stubEnv("KSI_CCTV_WEB_AUTH_DIR", authDir);
    vi.stubEnv("KSI_CCTV_WEB_PASSWORD", "role-gate-password");
    vi.stubEnv("KSI_CCTV_WEB_SECRET", "s".repeat(32));
    expect(await createUser("gate-op", "operator", "operator-pass-123")).toBe("created");
    expect(await createUser("gate-view", "viewer", "viewer-pass-12345")).toBe("created");
    adminToken = await createSession(3600);
    operatorToken = await createSession(3600, await userId("gate-op"));
    viewerToken = await createSession(3600, await userId("gate-view"));
  });

  afterEach(async () => {
    vi.unstubAllEnvs();
    await rm(authDir, { recursive: true, force: true });
  });

  it("redirects unauthenticated admin-path API requests to login", async () => {
    const response = await withSession("http://localhost:3000/api/models");

    expect(response.headers.get("location")).toBe("http://localhost:3000/login");
  });

  it("denies a viewer on admin API paths with 403 and no-store", async () => {
    const response = await authed("http://localhost:3000/api/models", viewerToken);

    expect(response.status).toBe(403);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(await response.json()).toEqual({ detail: "이 리소스에 접근할 권한이 없습니다." });
  });

  it("redirects a viewer on admin pages to the console root", async () => {
    const response = await authed("http://localhost:3000/settings/users", viewerToken);

    expect(response.headers.get("location")).toBe("http://localhost:3000/");
  });

  it("allows an operator to mutate cameras but blocks models", async () => {
    const allowed = await authed("http://localhost:3000/api/cameras/1", operatorToken, { method: "POST" });
    expect(allowed.headers.get("x-middleware-next")).toBe("1");

    const denied = await authed("http://localhost:3000/api/models", operatorToken);
    expect(denied.status).toBe(403);
    expect(denied.headers.get("Cache-Control")).toBe("no-store");
  });

  it("allows an operator to read viewer paths", async () => {
    const response = await authed("http://localhost:3000/api/events", operatorToken);

    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("allows an admin on admin, operator, and viewer paths", async () => {
    const models = await authed("http://localhost:3000/api/models", adminToken);
    const cameras = await authed("http://localhost:3000/api/cameras/1", adminToken, { method: "POST" });
    const settings = await authed("http://localhost:3000/settings", adminToken);

    expect(models.headers.get("x-middleware-next")).toBe("1");
    expect(cameras.headers.get("x-middleware-next")).toBe("1");
    expect(settings.headers.get("x-middleware-next")).toBe("1");
  });
});
