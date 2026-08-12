import { describe, it, expect } from "vitest";

import { formatTs } from "@/lib/utils";

describe("formatTs", () => {
  it("ISO 타임스탬프를 KST(UTC+9) 'YYYY-MM-DD HH:mm:ss'로 변환한다", () => {
    expect(formatTs("2026-06-17T09:00:00Z")).toBe("2026-06-17 18:00:00");
  });

  it("undefined를 입력하면 '—'를 반환한다", () => {
    expect(formatTs(undefined)).toBe("—");
  });

  it("파싱 실패 문자열은 원문을 폴백한다", () => {
    expect(formatTs("not-a-timestamp")).toBe("not-a-timestamp");
  });
});
