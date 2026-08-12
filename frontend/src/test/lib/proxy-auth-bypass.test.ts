import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEVELOPMENT_AUTH_BYPASS_HEADER } from "@/lib/auth-mode";
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
