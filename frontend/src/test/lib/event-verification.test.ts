import { describe, expect, it } from "vitest";

import { abbreviateDigest, getVerificationPresentation } from "@/lib/event-verification";

describe("verification presentation", () => {
  it.each([
    ["pending", "검증 대기"],
    ["confirmed", "확인됨"],
    ["rejected", "오탐 가능"],
    ["unverified", "검증 불가"],
  ])("labels %s verification", (verification_state, label) => {
    expect(getVerificationPresentation({ verification_state })?.label).toBe(label);
  });

  it("keeps unverified events operator-visible and high attention even with a legacy false-positive state", () => {
    expect(getVerificationPresentation({ verification_state: "unverified", alert_state: "likely_false_positive" })).toMatchObject({
      label: "검증 불가",
      tone: "destructive",
      requiresAttention: true,
    });
  });

  it("abbreviates model digests before rendering", () => {
    expect(abbreviateDigest("a".repeat(64))).toBe("aaaaaaaa...aaaaaa");
  });
});
