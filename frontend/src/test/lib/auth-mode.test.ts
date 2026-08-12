import { describe, expect, it } from "vitest";

import {
  developmentAuthBypassState,
  isLoopbackHost,
} from "@/lib/auth-mode";

const TOKEN = "a".repeat(64);
const LAUNCHER_ARGV = ["node", "/opt/ksi-cctv/next-with-auth-guard.mjs", "dev"];

function enabledEnv(overrides: Record<string, string | undefined> = {}) {
  return {
    NODE_ENV: "development",
    KSI_CCTV_DEV_AUTH_BYPASS: "1",
    KSI_CCTV_WEB_DEV: "1",
    KSI_CCTV_WEB_BIND_HOST: "127.0.0.1",
    KSI_CCTV_DEV_AUTH_BYPASS_TOKEN: TOKEN,
    ...overrides,
  };
}

describe("development auth bypass mode", () => {
  it("is disabled unless explicitly requested", () => {
    expect(developmentAuthBypassState({ NODE_ENV: "development" })).toEqual({ status: "disabled" });
  });

  it("is active only with exact flags, a loopback bind, and launcher token", () => {
    expect(developmentAuthBypassState(enabledEnv(), LAUNCHER_ARGV)).toEqual({ status: "active" });
    expect(isLoopbackHost("[::1]")).toBe(true);
  });

  it("does not treat alternate truthy strings as bypass requests", () => {
    expect(developmentAuthBypassState(enabledEnv({ KSI_CCTV_DEV_AUTH_BYPASS: "true" }), LAUNCHER_ARGV)).toEqual({
      status: "disabled",
    });
    expect(developmentAuthBypassState(enabledEnv({ KSI_CCTV_DEV_AUTH_BYPASS: "yes" }), LAUNCHER_ARGV)).toEqual({
      status: "disabled",
    });
  });

  it("fails closed outside the guarded local development process", () => {
    expect(developmentAuthBypassState(enabledEnv({ NODE_ENV: "production" }), LAUNCHER_ARGV).status).toBe("invalid");
    expect(developmentAuthBypassState(enabledEnv({ KSI_CCTV_WEB_DEV: undefined }), LAUNCHER_ARGV).status).toBe("invalid");
    expect(developmentAuthBypassState(enabledEnv(), ["node", "/app/next", "dev"]).status).toBe("invalid");
    expect(developmentAuthBypassState(enabledEnv({ KSI_CCTV_WEB_BIND_HOST: "0.0.0.0" }), LAUNCHER_ARGV).status).toBe("invalid");
    expect(developmentAuthBypassState(enabledEnv({ KSI_CCTV_DEV_AUTH_BYPASS_TOKEN: undefined }), LAUNCHER_ARGV).status).toBe("invalid");
    expect(developmentAuthBypassState(enabledEnv({ KSI_CCTV_DEV_AUTH_BYPASS_TOKEN: "predictable" }), LAUNCHER_ARGV).status).toBe("invalid");
  });
});
