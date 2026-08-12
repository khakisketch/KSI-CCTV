import { describe, expect, it } from "vitest";

import { designPreviewEnabled } from "@/lib/design-preview";

describe("design preview gate", () => {
  it("allows the preview only with the explicit development flag", () => {
    expect(designPreviewEnabled({ NODE_ENV: "development", KSI_CCTV_ENABLE_DESIGN_PREVIEW: "1" })).toBe(true);
    expect(designPreviewEnabled({ NODE_ENV: "development" })).toBe(false);
    expect(designPreviewEnabled({ NODE_ENV: "production", KSI_CCTV_ENABLE_DESIGN_PREVIEW: "1" })).toBe(false);
  });
});
