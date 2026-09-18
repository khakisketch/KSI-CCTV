import { describe, expect, it } from "vitest";

import { LoginRateLimiter, loginSource } from "@/lib/login-rate-limit";

describe("LoginRateLimiter", () => {
  it("limits repeated attempts from one source and reports retry time", () => {
    const limiter = new LoginRateLimiter(60_000, 2, 10);
    expect(limiter.consume("camera-vlan", 1_000).allowed).toBe(true);
    expect(limiter.consume("camera-vlan", 2_000).allowed).toBe(true);
    expect(limiter.consume("camera-vlan", 3_000)).toEqual({
      allowed: false,
      retryAfterSeconds: 58,
    });
    expect(limiter.consume("camera-vlan", 61_001).allowed).toBe(true);
  });

  it("uses a global cap even when source headers rotate", () => {
    const limiter = new LoginRateLimiter(60_000, 5, 2);
    expect(limiter.consume("one", 1_000).allowed).toBe(true);
    expect(limiter.consume("two", 2_000).allowed).toBe(true);
    expect(limiter.consume("three", 3_000).allowed).toBe(false);
  });

  it("checks limits without consuming an attempt", () => {
    const limiter = new LoginRateLimiter(60_000, 2, 10);
    expect(limiter.check("camera-vlan", 1_000).allowed).toBe(true);
    expect(limiter.consume("camera-vlan", 1_000).allowed).toBe(true);
    expect(limiter.check("camera-vlan", 2_000).allowed).toBe(true);
    expect(limiter.consume("camera-vlan", 2_000).allowed).toBe(true);
    expect(limiter.check("camera-vlan", 3_000)).toEqual({
      allowed: false,
      retryAfterSeconds: 58,
    });
  });

  it("normalizes forwarded source headers", () => {
    const headers = new Headers({ "x-forwarded-for": "192.168.1.20, 10.0.0.2" });
    expect(loginSource(headers)).toBe("direct");
    // 단일 신뢰 프록시가 append하는 관행에 따라 마지막 홉을 사용한다(F1-H3b).
    expect(loginSource(headers, true)).toBe("10.0.0.2");
  });

  it("ignores a spoofed first X-Forwarded-For entry when trusting a proxy", () => {
    expect(loginSource(new Headers({ "x-forwarded-for": "9.9.9.9, 10.0.0.5" }), true)).toBe("10.0.0.5");
    expect(loginSource(new Headers({ "x-forwarded-for": "spoofed-client, 192.0.2.1" }), true)).toBe("192.0.2.1");
  });

  it("skips empty X-Forwarded-For entries", () => {
    expect(loginSource(new Headers({ "x-forwarded-for": "  1.1.1.1 , , 2.2.2.2  " }), true)).toBe("2.2.2.2");
    expect(loginSource(new Headers({ "x-forwarded-for": "9.9.9.9, 10.0.0.5" }))).toBe("direct");
  });

  it("evicts expired sources and caps rotating source identities", () => {
    const limiter = new LoginRateLimiter(1_000, 5, 10_000, 2);
    limiter.consume("one", 1);
    limiter.consume("two", 2);
    limiter.consume("three", 3);
    expect(limiter.trackedSourceCount()).toBe(2);

    limiter.consume("fresh", 2_000);
    expect(limiter.trackedSourceCount()).toBe(1);
  });
});
