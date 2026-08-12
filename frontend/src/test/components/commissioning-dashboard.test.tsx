import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { CommissioningDashboard } from "@/components/commissioning-dashboard";
import type { CommissioningData } from "@/lib/engine";

const baseData: CommissioningData = {
  revision: 1,
  site: { name: "테스트 현장", location: "서울", timezone: "Asia/Seoul", installer: "설치자", expected_camera_count: 1 },
  ready_to_complete: false,
  progress: { passed: 0, total: 4, percent: 0 },
  checks: [],
  cameras: [{
    id: "cam-01",
    name: "카메라 1",
    enabled: true,
    enabled_events: ["camera_offline"],
    zone_count: 0,
    zone_warnings: [],
    ai_events: [],
    checks: { live_verified: false, reconnect_verified: false, detection_verified: false, alert_verified: false },
    ready: false,
  }],
};

describe("CommissioningDashboard", () => {
  it("health 전용 이벤트가 활성화되면 알림 확인은 가능하고 감지 확인은 AI 전용으로 유지한다", () => {
    render(<CommissioningDashboard initial={baseData} />);

    expect(screen.getByRole("checkbox", { name: "알림 확인" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: /감지 확인/ })).toBeDisabled();
    expect(screen.getByText("AI 이벤트를 먼저 설정해야 합니다.")).toBeInTheDocument();
  });
});
