import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  WebhookSettings,
  type WebhookEndpointView,
  type WebhookSettingsActions,
} from "@/components/webhook-settings";
import {
  getWebhookChannels,
  getWebhooks,
  maskWebhookEndpoint,
  shouldConfirmChannelsSave,
} from "@/lib/engine";

const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

const ENDPOINT: WebhookEndpointView = {
  id: "abc123def4567890",
  name: "관제실",
  url: "https://hooks.example.com/ksi",
  secret: "***",
  enabled: true,
  created_at: "2026-09-20T00:00:00+00:00",
  rotated_at: null,
};

function makeActions(overrides: Partial<WebhookSettingsActions> = {}): WebhookSettingsActions {
  return {
    createEndpoint: vi.fn().mockResolvedValue({ ok: false, detail: "미구현" }),
    updateEndpoint: vi.fn().mockResolvedValue({ ok: false, detail: "미구현" }),
    deleteEndpoint: vi.fn().mockResolvedValue({ ok: false, detail: "미구현" }),
    rotateEndpoint: vi.fn().mockResolvedValue({ ok: false, detail: "미구현" }),
    testEndpoint: vi.fn().mockResolvedValue({ ok: false, detail: "미구현" }),
    saveChannels: vi.fn().mockResolvedValue({ ok: false, detail: "미구현" }),
    ...overrides,
  };
}

const BASE_PROPS = {
  initialEndpoints: [ENDPOINT],
  initialChannels: {} as Record<string, string[]> | null,
  events: [{ name: "fire", alert_level: "critical" }],
};

describe("WebhookSettings", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    // clipboard 스텁 정리(jsdom 기본값으로 복원)
    try {
      delete (window.navigator as unknown as Record<string, unknown>).clipboard;
    } catch {
      /* 정리 실패 무시 */
    }
  });

  it("목록의 secret은 마스킹(＊＊＊)으로만 표시하고 재조회 불가 문구를 보인다", () => {
    render(<WebhookSettings {...BASE_PROPS} actions={makeActions()} />);

    expect(screen.getByText("관제실")).toBeInTheDocument();
    expect(screen.getByText("https://hooks.example.com/ksi")).toBeInTheDocument();
    expect(screen.getByText("secret: ***")).toBeInTheDocument();
    expect(screen.getByText(/재조회할 수 없습니다/)).toBeInTheDocument();
    // 평문 secret이 렌더되면 안 된다 — 마스킹 외 secret 문자열 없음
    const card = screen.getByText("관제실").closest("li") ?? document.body;
    expect(within(card as HTMLElement).queryByText("plain-secret")).not.toBeInTheDocument();
  });

  it("C1: 실형상(중첩 endpoint.secret) 평문을 1회 표시하고 목록에는 ***만 둔다", async () => {
    // 백엔드 실형태: {endpoint: {..., secret: 평문}} — 분리형 {endpoint, secret}이 아님
    const created: WebhookEndpointView = { ...ENDPOINT, id: "newid1234567890", name: "신규" };
    const actions = makeActions({
      createEndpoint: vi.fn().mockResolvedValue({
        ok: true,
        data: { endpoint: { ...created, secret: "nested-plain" } },
      }),
    });
    const user = userEvent.setup();
    render(<WebhookSettings {...BASE_PROPS} actions={actions} />);

    await user.type(screen.getByLabelText("이름"), "신규");
    await user.type(screen.getByLabelText("URL"), "https://hooks.example.com/new");
    await user.click(screen.getByRole("button", { name: "엔드포인트 추가" }));

    // 1회 박스에는 평문 표시
    await waitFor(() => expect(screen.getByText("nested-plain")).toBeInTheDocument());
    // 목록 행에는 마스킹만 — 평문 유출 금지(C2)
    const row = screen.getByText("신규").closest("li") as HTMLElement;
    expect(within(row).getByText("secret: ***")).toBeInTheDocument();
    expect(within(row).queryByText("nested-plain")).not.toBeInTheDocument();
  });

  it("C1+C2: 회전 응답의 중첩 평문도 1회 표시 + 목록 마스킹", async () => {
    const actions = makeActions({
      rotateEndpoint: vi.fn().mockResolvedValue({
        ok: true,
        data: { endpoint: { ...ENDPOINT, secret: "rotated-nested" } },
      }),
    });
    const user = userEvent.setup();
    render(<WebhookSettings {...BASE_PROPS} actions={actions} />);

    await user.click(screen.getByRole("button", { name: "secret 회전" }));

    await waitFor(() => expect(actions.rotateEndpoint).toHaveBeenCalledWith(ENDPOINT.id));
    await waitFor(() => expect(screen.getByText("rotated-nested")).toBeInTheDocument());
    const row = screen.getByText("관제실").closest("li") as HTMLElement;
    expect(within(row).getByText("secret: ***")).toBeInTheDocument();
    expect(within(row).queryByText("rotated-nested")).not.toBeInTheDocument();
  });

  it("1회성 secret 복사 버튼이 평문을 클립보드에 전달한다", async () => {
    const created: WebhookEndpointView = { ...ENDPOINT, id: "newid1234567890", name: "신규" };
    const actions = makeActions({
      createEndpoint: vi.fn().mockResolvedValue({
        ok: true,
        data: { endpoint: { ...created, secret: "one-time-secret" } },
      }),
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    // userEvent.setup()이 navigator.clipboard를 자체 스텁으로 교체하므로,
    // setup 이후에 테스트 스텁을 주입해야 컴포넌트가 이 mock을 본다.
    Object.defineProperty(window.navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(<WebhookSettings {...BASE_PROPS} actions={actions} />);

    await user.type(screen.getByLabelText("이름"), "신규");
    await user.type(screen.getByLabelText("URL"), "https://hooks.example.com/new");
    await user.click(screen.getByRole("button", { name: "엔드포인트 추가" }));

    await waitFor(() => expect(screen.getByText("one-time-secret")).toBeInTheDocument());
    expect(screen.getByText(/1회만 표시됩니다/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "secret 복사" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("one-time-secret"));
  });

  it("M2: 1회성 secret은 TTL 후 자동 만료된다", async () => {
    const created: WebhookEndpointView = { ...ENDPOINT, id: "newid1234567890", name: "신규" };
    const actions = makeActions({
      createEndpoint: vi.fn().mockResolvedValue({
        ok: true,
        data: { endpoint: { ...created, secret: "expiring-secret" } },
      }),
    });
    const user = userEvent.setup();
    // 단축 TTL 주입 — 실타이머로 만료 검증(fake timers는 waitFor와 교착).
    render(<WebhookSettings {...BASE_PROPS} actions={actions} oneTimeTtlMs={50} />);

    await user.type(screen.getByLabelText("이름"), "신규");
    await user.type(screen.getByLabelText("URL"), "https://hooks.example.com/new");
    await user.click(screen.getByRole("button", { name: "엔드포인트 추가" }));

    await waitFor(() => expect(screen.getByText("expiring-secret")).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText("expiring-secret")).not.toBeInTheDocument());
  });

  it("M3: 생성 요청 중에는 추가 버튼이 비활성화된다", async () => {
    let resolveCreate!: (value: unknown) => void;
    const pending = new Promise((resolve) => {
      resolveCreate = resolve;
    });
    const created: WebhookEndpointView = { ...ENDPOINT, id: "newid1234567890", name: "신규" };
    const actions = makeActions({
      createEndpoint: vi.fn().mockReturnValue(pending),
    });
    const user = userEvent.setup();
    render(<WebhookSettings {...BASE_PROPS} actions={actions} />);

    await user.type(screen.getByLabelText("이름"), "신규");
    await user.type(screen.getByLabelText("URL"), "https://hooks.example.com/new");
    await user.click(screen.getByRole("button", { name: "엔드포인트 추가" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "엔드포인트 추가" })).toBeDisabled(),
    );
    resolveCreate({ ok: true, data: { endpoint: { ...created, secret: "late-secret" } } });
    await waitFor(() => expect(screen.getByText("late-secret")).toBeInTheDocument());
  });

  it("M4: 삭제는 2단계 확인 후에만 수행된다", async () => {
    const actions = makeActions({
      deleteEndpoint: vi.fn().mockResolvedValue({ ok: true, data: null }),
    });
    const user = userEvent.setup();
    render(<WebhookSettings {...BASE_PROPS} actions={actions} />);

    await user.click(screen.getByRole("button", { name: "삭제" }));
    // 1단계 클릭만으로는 삭제 호출 없음
    expect(actions.deleteEndpoint).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "삭제 확인" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "삭제 확인" }));
    await waitFor(() => expect(actions.deleteEndpoint).toHaveBeenCalledWith(ENDPOINT.id));
    await waitFor(() => expect(screen.queryByText("관제실")).not.toBeInTheDocument());
  });

  it("M4: 삭제 확인에서 취소하면 삭제되지 않는다", async () => {
    const actions = makeActions({
      deleteEndpoint: vi.fn().mockResolvedValue({ ok: true, data: null }),
    });
    const user = userEvent.setup();
    render(<WebhookSettings {...BASE_PROPS} actions={actions} />);

    await user.click(screen.getByRole("button", { name: "삭제" }));
    await user.click(screen.getByRole("button", { name: "취소" }));

    expect(actions.deleteEndpoint).not.toHaveBeenCalled();
    expect(screen.getByText("관제실")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "삭제 확인" })).not.toBeInTheDocument();
  });

  it("테스트 발송 성공 시 성공 토스트를 보인다", async () => {
    const actions = makeActions({
      testEndpoint: vi
        .fn()
        .mockResolvedValue({ ok: true, data: { ok: true, endpoint_id: ENDPOINT.id } }),
    });
    const user = userEvent.setup();
    render(<WebhookSettings {...BASE_PROPS} actions={actions} />);

    await user.click(screen.getByRole("button", { name: "테스트 발송" }));

    await waitFor(() => expect(actions.testEndpoint).toHaveBeenCalledWith(ENDPOINT.id));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });

  it("I1: data.ok===false면 실패 토스트를 보인다(성공 아님)", async () => {
    // HTTP 200이지만 전달 실패 — 백엔드 실형태 {ok: false, endpoint_id}
    const actions = makeActions({
      testEndpoint: vi
        .fn()
        .mockResolvedValue({ ok: true, data: { ok: false, endpoint_id: ENDPOINT.id } }),
    });
    const user = userEvent.setup();
    render(<WebhookSettings {...BASE_PROPS} actions={actions} />);

    await user.click(screen.getByRole("button", { name: "테스트 발송" }));

    await waitFor(() => expect(actions.testEndpoint).toHaveBeenCalledWith(ENDPOINT.id));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/실패/)));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("I2: channels null이면 에러 표시 + 저장·토글이 차단된다", async () => {
    const actions = makeActions({
      saveChannels: vi.fn().mockResolvedValue({ ok: true, data: { event_count: 0, channels: {} } }),
    });
    const user = userEvent.setup();
    render(<WebhookSettings {...BASE_PROPS} initialChannels={null} actions={actions} />);

    expect(screen.getByText(/불러오지 못했습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "매트릭스 저장" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "fire webhook" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "매트릭스 저장" }));
    expect(actions.saveChannels).not.toHaveBeenCalled();
  });

  it("매트릭스 토글 후 저장하면 channels와 confirm:false를 전달한다", async () => {
    const actions = makeActions({
      saveChannels: vi
        .fn()
        .mockResolvedValue({ ok: true, data: { event_count: 1, channels: { fire: ["email"] } } }),
    });
    const user = userEvent.setup();
    render(<WebhookSettings {...BASE_PROPS} actions={actions} />);

    await user.click(screen.getByRole("checkbox", { name: "fire webhook" }));
    await user.click(screen.getByRole("button", { name: "매트릭스 저장" }));

    // 미등록 타입은 기본 허용([email, webhook])이 실효되므로, webhook만 끄면
    // email은 명시 허용으로 구체화된다. 전체 해제가 아니므로 confirm:false.
    await waitFor(() =>
      expect(actions.saveChannels).toHaveBeenCalledWith({ fire: ["email"] }, false),
    );
  });

  it("M1: 저장 성공 시 서버가 돌려준 channels로 state가 동기화된다", async () => {
    const actions = makeActions({
      saveChannels: vi
        .fn()
        .mockResolvedValue({ ok: true, data: { event_count: 1, channels: { fire: ["email"] } } }),
    });
    const user = userEvent.setup();
    render(
      <WebhookSettings
        {...BASE_PROPS}
        initialChannels={{ fire: ["webhook"] }}
        actions={actions}
      />,
    );

    await user.click(screen.getByRole("button", { name: "매트릭스 저장" }));

    // 서버값 {fire: [email]}로 동기화 — webhook 체크 해제, email 체크 유지
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "fire webhook" })).not.toBeChecked(),
    );
    expect(screen.getByRole("checkbox", { name: "fire email" })).toBeChecked();
  });

  it("M5: 매트릭스 저장 실패 시 서버값으로 롤백된다", async () => {
    const actions = makeActions({
      saveChannels: vi.fn().mockResolvedValue({ ok: false, detail: "엔진 저장 실패" }),
    });
    const user = userEvent.setup();
    render(
      <WebhookSettings
        {...BASE_PROPS}
        initialChannels={{ fire: ["webhook"] }}
        actions={actions}
      />,
    );

    await user.click(screen.getByRole("checkbox", { name: "fire webhook" }));
    expect(screen.getByRole("checkbox", { name: "fire webhook" })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "매트릭스 저장" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("엔진 저장 실패"));
    // 저장 전 서버값 {fire: [webhook]}으로 롤백
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "fire webhook" })).toBeChecked(),
    );
  });

  it("전체 해제(모든 채널 off) 저장 시 confirm:true를 전송한다", async () => {
    const actions = makeActions({
      saveChannels: vi
        .fn()
        .mockResolvedValue({ ok: true, data: { event_count: 0, channels: { fire: [] } } }),
    });
    const user = userEvent.setup();
    render(
      <WebhookSettings
        {...BASE_PROPS}
        initialChannels={{ fire: ["webhook"] }}
        actions={actions}
      />,
    );

    await user.click(screen.getByRole("checkbox", { name: "fire webhook" }));
    await user.click(screen.getByRole("button", { name: "매트릭스 저장" }));

    await waitFor(() =>
      expect(actions.saveChannels).toHaveBeenCalledWith({ fire: [] }, true),
    );
  });

  it("매트릭스 미등록 타입은 기본 허용 안내와 함께 체크된 상태로 보인다", () => {
    render(<WebhookSettings {...BASE_PROPS} actions={makeActions()} />);

    expect(screen.getAllByText(/기본 허용/).length).toBeGreaterThanOrEqual(2);
    // 미등록 타입은 기본 허용이므로 체크박스가 켜진 상태
    expect(screen.getByRole("checkbox", { name: "fire webhook" })).toBeChecked();
  });
});

describe("webhook engine 헬퍼", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("getWebhooks는 /api/webhooks를 호출한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({ count: 1, endpoints: [ENDPOINT] }),
      } as unknown as Response),
    );
    const result = await getWebhooks();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/webhooks"),
      expect.anything(),
    );
    expect(result?.count).toBe(1);
  });

  it("getWebhookChannels는 /api/webhooks/channels를 호출한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: vi.fn().mockResolvedValue({ channels: { fire: ["webhook"] } }),
      } as unknown as Response),
    );
    const result = await getWebhookChannels();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/webhooks/channels"),
      expect.anything(),
    );
    expect(result?.channels).toEqual({ fire: ["webhook"] });
  });

  it("shouldConfirmChannelsSave는 빈 매트릭스에서만 true다", () => {
    expect(shouldConfirmChannelsSave({})).toBe(true);
    expect(shouldConfirmChannelsSave({ fire: [] })).toBe(true);
    expect(shouldConfirmChannelsSave({ fire: ["webhook"] })).toBe(false);
    expect(shouldConfirmChannelsSave({ fire: [], intrusion: ["email"] })).toBe(false);
  });

  it("maskWebhookEndpoint는 secret만 ***로 치환한다", () => {
    const masked = maskWebhookEndpoint({ ...ENDPOINT, secret: "nested-plain" });
    expect(masked.secret).toBe("***");
    expect(masked.id).toBe(ENDPOINT.id);
    expect(masked.name).toBe(ENDPOINT.name);
    expect(masked.url).toBe(ENDPOINT.url);
  });
});
