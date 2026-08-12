import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SiteSetupForm } from "@/components/site-setup-form";

describe("SiteSetupForm", () => {
  it("saves plain site fields with the expected revision", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: vi.fn().mockResolvedValue({ revision: 4 }) } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<SiteSetupForm revision={3} site={{ name: "현장 A", location: "서울", timezone: "Asia/Seoul", installer: "김설치", expected_camera_count: 2 }} />);
    await user.clear(screen.getByLabelText("현장 이름"));
    await user.type(screen.getByLabelText("현장 이름"), "현장 B");
    await user.click(screen.getByRole("button", { name: "현장 정보 저장" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toMatchObject({ name: "현장 B", expected_revision: 3 });
    expect(screen.getByRole("alert")).toHaveTextContent("현장 정보를 저장했습니다");
  });

  it("explains revision conflicts", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 409, json: vi.fn().mockResolvedValue({}) } as unknown as Response));
    const user = userEvent.setup();
    render(<SiteSetupForm revision={3} site={{ name: "현장 A", location: "서울", timezone: "Asia/Seoul", installer: "김설치", expected_camera_count: 2 }} />);
    await user.click(screen.getByRole("button", { name: "현장 정보 저장" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("다른 사용자가 정보를 변경했습니다");
  });
});
