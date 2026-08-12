import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CameraSettings } from "@/components/camera-settings";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

// getEventCatalog가 별도 fetch로 부르므로 모듈 내부에서 fetch를 스텁한다.
const SAMPLE_CATALOG = [
  { name: "intrusion", alert_level: "critical", domains: ["construction", "school"] },
  { name: "safety_gear", alert_level: "high", domains: ["construction"] },
  { name: "crowd", alert_level: "medium", domains: ["school"] },
  { name: "fire", alert_level: "critical", domains: ["general"] },
  { name: "camera_disconnect", label: "카메라 단절", status: "planned" as const, selectable: false, reason: "준비 중" },
];

const PROFILE_PRESETS = {
  construction: { label: "건설현장", default_events: ["safety_gear", "intrusion"] },
  school: { label: "학교", default_events: ["intrusion", "crowd"] },
};

const POLICY_PROFILES = {
  construction: [
    { name: "safety_gear", alert_level: "high" },
    { name: "intrusion", alert_level: "critical" },
  ],
  school: [
    { name: "intrusion", alert_level: "critical" },
    { name: "crowd", alert_level: "medium" },
  ],
};

function mockFetch(ok = true, detail?: string, bodyImpl?: (input: RequestInfo | URL, init?: RequestInit) => unknown) {
  return vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (bodyImpl) {
        const out = bodyImpl(input, init);
        if (out) return out as Response;
      }
      const url = String(input);
      if (url.includes("/api/event-catalog")) {
        return {
          ok: true,
          json: async () => ({ catalog: SAMPLE_CATALOG, count: SAMPLE_CATALOG.length }),
        } as unknown as Response;
      }
      return {
        ok,
        json: async () => (detail ? { detail } : {}),
      } as unknown as Response;
    }),
  );
}

describe("CameraSettings", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("초기 상태에서 '설정 편집' 버튼만 렌더한다", () => {
    render(
      <CameraSettings
        cameraId="cam-01"
        initialDomain="construction"
        initialEvents={["safety_gear"]}
        policyProfiles={POLICY_PROFILES}
      />,
    );

    expect(screen.getByRole("button", { name: "설정 편집" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "construction" })).not.toBeInTheDocument();
    expect(screen.queryByText("활성 이벤트")).not.toBeInTheDocument();
  });

  it("'설정 편집' 클릭 시 도메인 선택과 기본값 적용 버튼이 표시된다", async () => {
    mockFetch();
    render(
      <CameraSettings
        cameraId="cam-01"
        initialDomain="construction"
        initialEvents={["safety_gear"]}
        policyProfiles={POLICY_PROFILES}
        eventCatalog={SAMPLE_CATALOG}
        profilePresets={PROFILE_PRESETS}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "설정 편집" }));

    expect(screen.getByLabelText("현장 유형")).toHaveValue("construction");
    expect(screen.getByRole("button", { name: "기본값 적용" })).toBeInTheDocument();
  });

  it("도메인을 변경해도 카탈로그의 모든 이벤트는 표시되고 활성 이벤트만 필터링된다", async () => {
    mockFetch();
    render(
      <CameraSettings
        cameraId="cam-01"
        initialDomain="construction"
        initialEvents={["safety_gear", "intrusion"]}
        policyProfiles={POLICY_PROFILES}
        eventCatalog={SAMPLE_CATALOG}
        profilePresets={PROFILE_PRESETS}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "설정 편집" }));

    await userEvent.selectOptions(screen.getByLabelText("현장 유형"), "school");

    // 카탈로그 전체 이벤트는 표시되어야 한다 (자유 선택 UX).
    await waitFor(() => {
      expect(screen.getByText("fire")).toBeInTheDocument();
    });
    expect(screen.getByText("crowd")).toBeInTheDocument();
    expect(screen.getByText("카메라 단절")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /카메라 단절/ })).toBeDisabled();
  });

  it("저장 버튼 클릭 시 PUT /api/cameras/{id}/profile를 호출한다", async () => {
    mockFetch();
    render(
      <CameraSettings
        cameraId="cam-01"
        initialDomain="construction"
        initialEvents={["safety_gear"]}
        policyProfiles={POLICY_PROFILES}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "설정 편집" }));
    // 저장은 '저장 (N)' 패턴으로 라벨에 카운트가 포함된다.
    await userEvent.click(screen.getByRole("button", { name: /^저장/ }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalled();
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/cameras/cam-01/profile",
      expect.objectContaining({
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain: "construction", enabled_events: ["safety_gear"] }),
      }),
    );
  });

  it("활성 이벤트가 없어도 빈 목록을 저장할 수 있다", async () => {
    mockFetch();
    render(
      <CameraSettings
        cameraId="cam-01"
        initialDomain="construction"
        initialEvents={[]}
        policyProfiles={POLICY_PROFILES}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "설정 편집" }));
    await userEvent.click(screen.getByRole("button", { name: "저장 (0)" }));
    expect(fetch).toHaveBeenCalledWith(
      "/api/cameras/cam-01/profile",
      expect.objectContaining({
        body: JSON.stringify({ domain: "construction", enabled_events: [] }),
      }),
    );
  });

  it("zone-required event에 필요한 영역이 없으면 경고한다", async () => {
    render(
      <CameraSettings
        cameraId="cam-01"
        initialDomain="construction"
        initialEvents={["loitering"]}
        policyProfiles={POLICY_PROFILES}
        eventCatalog={[{ name: "loitering", label: "배회", requires_zone: "loitering" }]}
        zones={[]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "설정 편집" }));
    expect(screen.getByRole("alert")).toHaveTextContent("배회");
    expect(screen.getByRole("button", { name: /^저장/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /배회/ }));
    expect(screen.getByRole("button", { name: /^저장/ })).toBeEnabled();
  });

  it("릴리스 미자격 시험 기능은 선택할 수 없고 qualification 이유를 표시한다", async () => {
    render(
      <CameraSettings
        cameraId="cam-01"
        initialDomain="construction"
        initialEvents={[]}
        policyProfiles={POLICY_PROFILES}
        eventCatalog={[{
          name: "fall",
          label: "쓰러짐 의심",
          status: "experimental",
          release_qualified: false,
          selectable: false,
          blocked_reason: "이번 릴리스에서 자격 검증되지 않았습니다.",
        }]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "설정 편집" }));
    expect(screen.getByRole("checkbox", { name: /쓰러짐 의심/ })).toBeDisabled();
    expect(screen.getByText("릴리스 미자격")).toBeInTheDocument();
    expect(screen.getByText(/현장 YAML이나 환경 변수/)).toBeInTheDocument();
  });

  it("저장된 릴리스 미자격 선택은 차단 상태로 표시하고 해제할 수 있다", async () => {
    render(
      <CameraSettings
        cameraId="cam-01"
        initialDomain="construction"
        initialEvents={["fall"]}
        policyProfiles={POLICY_PROFILES}
        eventCatalog={[{
          name: "fall",
          label: "쓰러짐 의심",
          status: "experimental",
          release_qualified: false,
          selectable: false,
          blocked_reason: "이번 릴리스에서 자격 검증되지 않았습니다.",
        }]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "설정 편집" }));
    const fall = screen.getByRole("checkbox", { name: /쓰러짐 의심/ });
    expect(fall).toBeChecked();
    expect(fall).toBeEnabled();
    expect(screen.getByText("릴리스 미자격")).toBeInTheDocument();
  });

  it("sends the camera revision and explains a profile conflict", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: vi.fn().mockResolvedValue({ detail: "revision conflict" }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    render(
      <CameraSettings
        cameraId="cam-01"
        initialDomain="construction"
        initialEvents={[]}
        policyProfiles={POLICY_PROFILES}
        revision={8}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "설정 편집" }));
    await userEvent.click(screen.getByRole("button", { name: "저장 (0)" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/cameras/cam-01/profile",
      expect.objectContaining({
        body: JSON.stringify({ domain: "construction", enabled_events: [], expected_revision: 8 }),
      }),
    );
    const { toast } = await import("sonner");
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("페이지를 새로고침"));
  });

  it("policyProfiles가 빈 객체일 때 null을 렌더한다", () => {
    const { container } = render(
      <CameraSettings
        cameraId="cam-01"
        initialDomain=""
        initialEvents={[]}
        policyProfiles={{}}
      />,
    );

    expect(container.firstChild).toBeNull();
  });
});
