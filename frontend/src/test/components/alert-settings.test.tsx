import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AlertSettings } from "@/components/alert-settings";

const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

describe("AlertSettings", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("shows a recovery message when saving fails before reaching the server", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network unavailable")));
    const user = userEvent.setup();
    render(
      <AlertSettings
        initial={{ email_alert_level_threshold: "medium", per_event_email_enabled: {} }}
        events={[{ name: "fire", alert_level: "critical" }]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "알림 설정 저장" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      "저장 요청을 보낼 수 없습니다. 네트워크 연결을 확인한 뒤 다시 저장하세요.",
    ));
  });
});
